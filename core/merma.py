"""
core/merma.py — Motor de análisis de merma (v2: por selección de período)

Para una sucursal y un rango de fechas, calcula por SKU la suma valorizada
de cada categoría de movimiento. Las categorías agrupan subtipos del ERP
(`tipo`) según la tabla configurable `tipos_categoria`.

Modelo (decisiones cerradas en MIGRACION_V2.md):
  - Universo de SKUs: los que tengan algún movimiento O alguna venta en el
    rango/sucursal (incluye SKUs con solo ventas).
  - Por SKU y categoría: SUM(diferencia) (neto con signo), valorizado.
  - Valorización: snapshot de `stock_sucursal` as-of <= fecha_hasta,
    columna `costo` o `lista_1` según `modo_valorizacion`. Nunca se usa el
    costo del movimiento (los subtipos del ERP son inconsistentes).
  - Merma (numerador): parte negativa de las categorías marcadas es_merma.
  - Denominador: venta del archivo `ventas` (criterio_ventas). En modo
    "costo" la venta se revaloriza como unidades_vendidas * costo_snapshot;
    en modo "lista_1" se usa venta_neta (importe real a precio de venta).
  - % merma = merma_total_valorizada / venta_neta * 100.

Retorna un DataFrame ancho: una fila por SKU, una columna $ por categoría
(neto con signo), más merma_total_valorizada, venta_neta y
pct_merma_sobre_ventas. La lista ordenada de columnas-categoría queda en
`df.attrs["categorias"]`.

Uso:
    from core.merma import calcular_merma

    df = calcular_merma(
        proyecto="cliente_alfa",
        codigodepo="002",
        fecha_desde="2026-01-01",
        fecha_hasta="2026-03-31",
        modo_valorizacion="costo",    # o "lista_1"
        criterio_ventas="contenido",  # o "solapado" / "prorrateado"
    )
"""

import duckdb
import pandas as pd


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CRITERIOS_VENTAS = ("contenido", "solapado", "prorrateado")
MODOS_VALORIZACION = ("costo", "lista_1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(proyecto: str) -> duckdb.DuckDBPyConnection:
    from setup_db import get_db_path
    db_path = get_db_path(proyecto)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos para '{proyecto}'. "
            f"Corré: python setup_db.py --project {proyecto}"
        )
    return duckdb.connect(str(db_path))


def _sql_ventas(criterio: str) -> str:
    """
    Agregación de ventas por SKU dentro de la ventana fija [$desde, $hasta].
    Mismos criterios que en v1 pero con ventana única (no por SKU).
    """
    if criterio == "contenido":
        return """
            SELECT codigo,
                   SUM(unidades)   AS unidades_vendidas,
                   SUM(venta_neta) AS venta_neta
            FROM ventas
            WHERE codigodepo  = $depo
              AND fecha_desde >= $desde::DATE
              AND fecha_hasta <= $hasta::DATE
            GROUP BY codigo
        """
    if criterio == "solapado":
        return """
            SELECT codigo,
                   SUM(unidades)   AS unidades_vendidas,
                   SUM(venta_neta) AS venta_neta
            FROM ventas
            WHERE codigodepo  = $depo
              AND fecha_hasta >= $desde::DATE
              AND fecha_desde <= $hasta::DATE
            GROUP BY codigo
        """
    # prorrateado: períodos solapados ponderados por días dentro de la ventana
    return """
        SELECT codigo,
               SUM(unidades   * factor) AS unidades_vendidas,
               SUM(venta_neta * factor) AS venta_neta
        FROM (
            SELECT v.codigo, v.unidades, v.venta_neta,
                   (date_diff('day',
                              GREATEST(v.fecha_desde, $desde::DATE),
                              LEAST(v.fecha_hasta, $hasta::DATE)) + 1)::DOUBLE
                   / NULLIF(date_diff('day', v.fecha_desde, v.fecha_hasta) + 1, 0)
                   AS factor
            FROM ventas v
            WHERE v.codigodepo  = $depo
              AND v.fecha_hasta >= $desde::DATE
              AND v.fecha_desde <= $hasta::DATE
        )
        GROUP BY codigo
    """


def listar_categorias(proyecto: str) -> list[str]:
    """Categorías ordenadas según `tipos_categoria` (para la UI)."""
    conn = _get_connection(proyecto)
    try:
        rows = conn.execute("""
            SELECT categoria
            FROM tipos_categoria
            GROUP BY categoria
            ORDER BY MIN(orden), categoria
        """).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Ensamblado (pandas)
# ---------------------------------------------------------------------------

def _ensamblar(df_comp, df_val, df_ventas, df_art, cats_orden,
               modo, desde, hasta) -> pd.DataFrame:
    unit_col = "costo" if modo == "costo" else "lista_1"
    val = (df_val.set_index("codigo")[unit_col]
           if not df_val.empty else pd.Series(dtype="float64"))

    universo = sorted(set(df_comp["codigo"]) | set(df_ventas["codigo"]))

    # --- pivot de categorías valorizadas + merma por SKU --------------------
    if not df_comp.empty:
        df_comp = df_comp.copy()
        df_comp["unit"] = df_comp["codigo"].map(val)
        df_comp["neto_valorizado"] = df_comp["neto_unidades"] * df_comp["unit"]

        df_comp["merma_contrib"] = 0.0
        mask = df_comp["es_merma"] & (df_comp["neto_valorizado"] < 0)
        df_comp.loc[mask, "merma_contrib"] = -df_comp.loc[mask, "neto_valorizado"]

        pivot = df_comp.pivot_table(
            index="codigo", columns="categoria",
            values="neto_valorizado", aggfunc="sum",
        )
        merma_total = df_comp.groupby("codigo")["merma_contrib"].sum()
    else:
        pivot = pd.DataFrame()
        merma_total = pd.Series(dtype="float64")

    # Columnas-categoría: orden del catálogo + cualquier extra que aparezca
    cats = list(cats_orden)
    for c in pivot.columns:
        if c not in cats:
            cats.append(c)

    res = pd.DataFrame(index=pd.Index(universo, name="codigo"))
    for c in cats:
        res[c] = pivot[c] if c in pivot.columns else 0.0
    res[cats] = res[cats].fillna(0.0)

    res["merma_total_valorizada"] = merma_total
    res["merma_total_valorizada"] = res["merma_total_valorizada"].fillna(0.0)

    # --- ventas (denominador) ----------------------------------------------
    dv = df_ventas.set_index("codigo")
    res["unidades_vendidas"] = (
        dv["unidades_vendidas"].reindex(res.index).fillna(0.0)
        if not dv.empty else 0.0
    )
    if modo == "costo":
        res["venta_neta"] = res["unidades_vendidas"] * res.index.map(val)
    else:
        res["venta_neta"] = (
            dv["venta_neta"].reindex(res.index)
            if not dv.empty else pd.Series(index=res.index, dtype="float64")
        )
    res["venta_neta"] = res["venta_neta"].fillna(0.0)

    # --- % merma sobre ventas ----------------------------------------------
    res["pct_merma_sobre_ventas"] = pd.NA
    m = res["venta_neta"] > 0
    res.loc[m, "pct_merma_sobre_ventas"] = (
        res.loc[m, "merma_total_valorizada"] / res.loc[m, "venta_neta"] * 100
    ).round(4)

    # --- metadatos + descripción -------------------------------------------
    res["valor_unitario"] = res.index.map(val)
    res = res.reset_index().merge(df_art, on="codigo", how="left")
    res["modo_valorizacion"] = modo
    res["fecha_desde"] = pd.to_datetime(desde).date()
    res["fecha_hasta"] = pd.to_datetime(hasta).date()

    cols = (
        ["codigo", "descripcion"] + cats +
        ["merma_total_valorizada", "unidades_vendidas", "venta_neta",
         "pct_merma_sobre_ventas", "valor_unitario", "modo_valorizacion",
         "fecha_desde", "fecha_hasta"]
    )
    res = (res[cols]
           .sort_values("merma_total_valorizada", ascending=False)
           .reset_index(drop=True))
    res.attrs["categorias"] = cats
    return res


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def calcular_merma(
    proyecto: str,
    codigodepo: str,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    criterio_ventas: str = "contenido",
) -> pd.DataFrame:
    """
    Calcula merma por SKU para una sucursal y rango de fechas.

    Args:
        proyecto:          Nombre del proyecto
        codigodepo:        Código de sucursal
        fecha_desde:       Inicio del período (YYYY-MM-DD), inclusive
        fecha_hasta:       Fin del período (YYYY-MM-DD), inclusive
        modo_valorizacion: "costo" (default) o "lista_1"
        criterio_ventas:   "contenido" (default), "solapado" o "prorrateado"

    Returns:
        DataFrame ancho (una fila por SKU). `df.attrs["categorias"]` lista
        las columnas-categoría en orden.
    """
    if modo_valorizacion not in MODOS_VALORIZACION:
        raise ValueError(
            f"modo_valorizacion debe ser uno de {MODOS_VALORIZACION}, no '{modo_valorizacion}'"
        )
    if criterio_ventas not in CRITERIOS_VENTAS:
        raise ValueError(
            f"criterio_ventas debe ser uno de {CRITERIOS_VENTAS}, no '{criterio_ventas}'"
        )

    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError(f"fecha_desde ({desde}) no puede ser posterior a fecha_hasta ({hasta})")

    params = {"depo": codigodepo, "desde": desde, "hasta": hasta}
    conn = _get_connection(proyecto)
    try:
        # Componentes: neto por SKU y categoría (LEFT JOIN para no perder
        # subtipos sin mapear → caen en '(sin categoría)', es_merma=FALSE)
        df_comp = conn.execute("""
            WITH rango AS (
                SELECT codigo, tipo, SUM(diferencia) AS neto_unidades
                FROM movimientos
                WHERE codigodepo = $depo
                  AND fecha >= $desde::DATE
                  AND fecha <= $hasta::DATE
                GROUP BY codigo, tipo
            )
            SELECT r.codigo,
                   COALESCE(t.categoria, '(sin categoría)') AS categoria,
                   COALESCE(t.es_merma, FALSE)              AS es_merma,
                   SUM(r.neto_unidades)                     AS neto_unidades
            FROM rango r
            LEFT JOIN tipos_categoria t ON r.tipo = t.tipo
            GROUP BY 1, 2, 3
        """, params).df()

        # Valorización: snapshot más reciente <= fecha_hasta por SKU
        df_val = conn.execute("""
            SELECT codigo, costo, lista_1
            FROM stock_sucursal
            WHERE codigodepo = $depo
              AND fecha_snapshot <= $hasta::DATE
            QUALIFY row_number() OVER (
                PARTITION BY codigo
                ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
            ) = 1
        """, {"depo": codigodepo, "hasta": hasta}).df()

        # Ventas (denominador)
        df_ventas = conn.execute(_sql_ventas(criterio_ventas), params).df()

        # Descripción de artículos
        df_art = conn.execute("SELECT codigo, descripcion FROM articulos").df()

        # Orden de categorías del catálogo
        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]
    finally:
        conn.close()

    return _ensamblar(df_comp, df_val, df_ventas, df_art, cats_orden,
                      modo_valorizacion, desde, hasta)
