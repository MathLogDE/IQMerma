"""
core/merma.py — Motor de análisis de merma (v2: por selección de período)

Para una sucursal (o todas) y un rango de fechas, calcula por SKU la suma de
cada categoría de movimiento, en unidades y valorizada. Las categorías agrupan
subtipos del ERP (`tipo`) según la tabla configurable `tipos_categoria`.

Modelo (decisiones cerradas en MIGRACION_V2.md):
  - Fuente única: `movimientos` + `stock_sucursal` (+ catálogos).
  - Universo de SKUs: los que tengan algún movimiento en el rango/selección.
  - Por SKU y categoría: SUM(diferencia) (neto con signo), en unidades y
    valorizado.
  - Valorización: SIEMPRE desde `stock_sucursal`. El costo/lista_1 es global
    por SKU (una sola columna en el ERP), así que no se filtra por sucursal.
    `fecha_valorizacion` elige el snapshot (el más reciente <= esa fecha);
    si no se pasa, se usa el último snapshot disponible (valor actual).
  - Merma (numerador): parte negativa de las categorías marcadas es_merma.
  - Venta (denominador): unidades netas de las categorías marcadas es_venta
    (la salida, en positivo), valorizadas desde el stock.
  - % merma = merma_total_valorizada / venta_neta * 100.

Retorna un DataFrame ancho: una fila por SKU con atributos (rubro, marca,
super rubro, gran super rubro), una columna $ por categoría (neto con signo) y
su gemela en unidades (`<cat> (u)`), más totales y %.
  - df.attrs["categorias"]         → columnas-categoría ($) en orden
  - df.attrs["fecha_valorizacion"] → snapshot efectivamente usado

Uso:
    from core.merma import calcular_merma

    df = calcular_merma(
        proyecto="cliente_alfa",
        codigodepo="002",            # o None = todas las sucursales
        fecha_desde="2026-01-01",
        fecha_hasta="2026-03-31",
        modo_valorizacion="costo",   # o "lista_1"
        fecha_valorizacion=None,     # o "YYYY-MM-DD" (snapshot a usar)
    )
"""

import duckdb
import pandas as pd


# Helpers compartidos (definidos en core.comun; se re-exportan para
# compatibilidad con quien todavía importa desde core.merma).
from core.comun import (  # noqa: E402
    MODOS_VALORIZACION,
    conectar as _get_connection,
    mapa_estructura as _mapa_estructura,
    listar_categorias,
    listar_fechas_valorizacion,
)


# ---------------------------------------------------------------------------
# Ensamblado (pandas)
# ---------------------------------------------------------------------------

def _ensamblar(df_comp, df_val, df_art, df_est, cats_orden,
               modo, desde, hasta) -> pd.DataFrame:
    unit_col = "costo" if modo == "costo" else "lista_1"
    val = (df_val.set_index("codigo")[unit_col]
           if not df_val.empty else pd.Series(dtype="float64"))

    universo = sorted(set(df_comp["codigo"]))

    # --- pivots por categoría: unidades y valorizado -------------------------
    if not df_comp.empty:
        df_comp = df_comp.copy()
        df_comp["unit"] = df_comp["codigo"].map(val)
        df_comp["neto_valorizado"] = df_comp["neto_unidades"] * df_comp["unit"]

        # contribución a merma ($ y unidades): parte negativa de cats es_merma
        df_comp["merma_contrib"] = 0.0
        mask = df_comp["es_merma"] & (df_comp["neto_valorizado"] < 0)
        df_comp.loc[mask, "merma_contrib"] = -df_comp.loc[mask, "neto_valorizado"]

        df_comp["merma_contrib_u"] = 0.0
        mask_u = df_comp["es_merma"] & (df_comp["neto_unidades"] < 0)
        df_comp.loc[mask_u, "merma_contrib_u"] = -df_comp.loc[mask_u, "neto_unidades"]

        pivot_val = df_comp.pivot_table(
            index="codigo", columns="categoria",
            values="neto_valorizado", aggfunc="sum",
        )
        pivot_uni = df_comp.pivot_table(
            index="codigo", columns="categoria",
            values="neto_unidades", aggfunc="sum",
        )
        merma_total   = df_comp.groupby("codigo")["merma_contrib"].sum()
        merma_total_u = df_comp.groupby("codigo")["merma_contrib_u"].sum()
        # unidades vendidas = -(neto de categorías es_venta): la venta es salida
        vs = df_comp[df_comp["es_venta"]].groupby("codigo")["neto_unidades"].sum()
    else:
        pivot_val = pd.DataFrame()
        pivot_uni = pd.DataFrame()
        merma_total = merma_total_u = vs = pd.Series(dtype="float64")

    # Columnas-categoría: orden del catálogo + cualquier extra que aparezca
    cats = list(cats_orden)
    for c in pivot_val.columns:
        if c not in cats:
            cats.append(c)

    res = pd.DataFrame(index=pd.Index(universo, name="codigo"))
    for c in cats:
        res[c] = pivot_val[c] if c in pivot_val.columns else 0.0
        res[f"{c} (u)"] = pivot_uni[c] if c in pivot_uni.columns else 0.0
    cols_num = cats + [f"{c} (u)" for c in cats]
    res[cols_num] = res[cols_num].fillna(0.0)

    res["merma_total_valorizada"] = merma_total.reindex(res.index).fillna(0.0)
    res["merma_total_unidades"]   = merma_total_u.reindex(res.index).fillna(0.0)

    # --- venta (denominador): unidades vendidas × precio de stock -----------
    res["unidades_vendidas"] = (-vs).reindex(res.index).fillna(0.0)
    res["venta_neta"] = (res["unidades_vendidas"] * res.index.map(val)).fillna(0.0)

    # --- % merma sobre ventas ------------------------------------------------
    res["pct_merma_sobre_ventas"] = pd.NA
    m = res["venta_neta"] > 0
    res.loc[m, "pct_merma_sobre_ventas"] = (
        res.loc[m, "merma_total_valorizada"] / res.loc[m, "venta_neta"] * 100
    ).round(4)

    # --- atributos: descripción, rubro, marca, jerarquía ---------------------
    res["valor_unitario"] = res.index.map(val)
    res = res.reset_index().merge(df_art, on="codigo", how="left")

    mapa = _mapa_estructura(df_est)
    res["_clave"] = res["rubro"].astype("string").str.strip()
    res = res.merge(mapa, on="_clave", how="left").drop(columns=["_clave"])
    res["rubro"] = res["rubro_desc"].fillna(res["rubro"])
    res = res.drop(columns=["rubro_desc"])

    res["modo_valorizacion"] = modo
    res["fecha_desde"] = pd.to_datetime(desde).date()
    res["fecha_hasta"] = pd.to_datetime(hasta).date()

    cols = (
        ["codigo", "descripcion", "rubro", "marca", "super_rubro", "gran_super_rubro"]
        + [x for c in cats for x in (c, f"{c} (u)")]
        + ["merma_total_valorizada", "merma_total_unidades",
           "unidades_vendidas", "venta_neta", "pct_merma_sobre_ventas",
           "valor_unitario", "modo_valorizacion", "fecha_desde", "fecha_hasta"]
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
    codigodepo: str | None,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Calcula merma por SKU para una sucursal (o todas) y un rango de fechas.

    Args:
        proyecto:           Nombre del proyecto
        codigodepo:         Código de sucursal, o None = todas las sucursales
        fecha_desde:        Inicio del período (YYYY-MM-DD), inclusive
        fecha_hasta:        Fin del período (YYYY-MM-DD), inclusive
        modo_valorizacion:  "costo" (default) o "lista_1"
        fecha_valorizacion: Fecha del snapshot de stock a usar para valorizar
                            (se toma el más reciente <= esa fecha). None =
                            último snapshot disponible (valor actual).

    Returns:
        DataFrame ancho (una fila por SKU). `df.attrs["categorias"]` lista las
        columnas-categoría; `df.attrs["fecha_valorizacion"]` es el snapshot usado.
    """
    if modo_valorizacion not in MODOS_VALORIZACION:
        raise ValueError(
            f"modo_valorizacion debe ser uno de {MODOS_VALORIZACION}, no '{modo_valorizacion}'"
        )

    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError(f"fecha_desde ({desde}) no puede ser posterior a fecha_hasta ({hasta})")

    conn = _get_connection(proyecto)
    try:
        # Componentes: neto por SKU y categoría (LEFT JOIN para no perder
        # subtipos sin mapear → caen en '(sin categoría)')
        filtro_depo = "AND codigodepo = $depo" if codigodepo else ""
        params = {"desde": desde, "hasta": hasta}
        if codigodepo:
            params["depo"] = codigodepo

        df_comp = conn.execute(f"""
            WITH rango AS (
                SELECT codigo, tipo, SUM(diferencia) AS neto_unidades
                FROM movimientos
                WHERE fecha >= $desde::DATE
                  AND fecha <= $hasta::DATE
                  {filtro_depo}
                GROUP BY codigo, tipo
            )
            SELECT r.codigo,
                   COALESCE(t.categoria, '(sin categoría)') AS categoria,
                   COALESCE(t.es_merma, FALSE)              AS es_merma,
                   COALESCE(t.es_venta, FALSE)              AS es_venta,
                   SUM(r.neto_unidades)                     AS neto_unidades
            FROM rango r
            LEFT JOIN tipos_categoria t ON r.tipo = t.tipo
            GROUP BY 1, 2, 3, 4
        """, params).df()

        # Valorización: snapshot elegido (o el último disponible). El precio es
        # global por SKU — no se filtra por sucursal.
        if fecha_valorizacion:
            fv = str(pd.to_datetime(fecha_valorizacion).date())
            df_val = conn.execute("""
                SELECT codigo, costo, lista_1
                FROM stock_sucursal
                WHERE fecha_snapshot <= $fv::DATE
                QUALIFY row_number() OVER (
                    PARTITION BY codigo
                    ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
                ) = 1
            """, {"fv": fv}).df()
        else:
            df_val = conn.execute("""
                SELECT codigo, costo, lista_1
                FROM stock_sucursal
                QUALIFY row_number() OVER (
                    PARTITION BY codigo
                    ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
                ) = 1
            """).df()

        # Snapshot efectivamente usado (para trazabilidad en la UI)
        fv_usada = conn.execute(
            "SELECT MAX(fecha_snapshot) FROM stock_sucursal"
            + (" WHERE fecha_snapshot <= ?" if fecha_valorizacion else ""),
            [fv] if fecha_valorizacion else [],
        ).fetchone()[0]

        # Atributos de artículos + jerarquía de rubros
        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos"
        ).df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()

        # Orden de categorías del catálogo
        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]
    finally:
        conn.close()

    df = _ensamblar(df_comp, df_val, df_art, df_est, cats_orden,
                    modo_valorizacion, desde, hasta)
    df.attrs["fecha_valorizacion"] = str(fv_usada) if fv_usada else None
    return df


# ---------------------------------------------------------------------------
# Comparativa por sucursal
# ---------------------------------------------------------------------------

def merma_por_sucursal(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Comparativa agregada por sucursal para un rango de fechas: una fila por
    sucursal con la merma/venta por categoría ($ y unidades), totales y %.

    Misma semántica que calcular_merma (la merma se computa a nivel SKU —
    parte negativa por SKU y categoría — y recién ahí se agrega por sucursal,
    para que los sobrantes de un SKU no tapen los faltantes de otro).

    Args:
        codigos: lista opcional de SKUs a incluir (para respetar filtros de
                 rubro/marca de la UI). None = todos.

    Returns:
        DataFrame: codigodepo, nombre, skus, skus_con_merma, columnas por
        categoría ($ y "(u)"), merma_total_valorizada, merma_total_unidades,
        unidades_vendidas, venta_neta, pct_merma_sobre_ventas.
        attrs: "categorias", "fecha_valorizacion".
    """
    if modo_valorizacion not in MODOS_VALORIZACION:
        raise ValueError(
            f"modo_valorizacion debe ser uno de {MODOS_VALORIZACION}, no '{modo_valorizacion}'"
        )
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())

    conn = _get_connection(proyecto)
    try:
        df_g = conn.execute("""
            WITH rango AS (
                SELECT codigodepo, codigo, tipo, SUM(diferencia) AS neto_unidades
                FROM movimientos
                WHERE fecha >= $desde::DATE AND fecha <= $hasta::DATE
                GROUP BY 1, 2, 3
            )
            SELECT r.codigodepo, r.codigo,
                   COALESCE(t.categoria, '(sin categoría)') AS categoria,
                   COALESCE(t.es_merma, FALSE)              AS es_merma,
                   COALESCE(t.es_venta, FALSE)              AS es_venta,
                   SUM(r.neto_unidades)                     AS neto_unidades
            FROM rango r
            LEFT JOIN tipos_categoria t ON r.tipo = t.tipo
            GROUP BY 1, 2, 3, 4, 5
        """, {"desde": desde, "hasta": hasta}).df()

        if fecha_valorizacion:
            fv = str(pd.to_datetime(fecha_valorizacion).date())
            df_val = conn.execute("""
                SELECT codigo, costo, lista_1 FROM stock_sucursal
                WHERE fecha_snapshot <= $fv::DATE
                QUALIFY row_number() OVER (
                    PARTITION BY codigo
                    ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
                ) = 1
            """, {"fv": fv}).df()
        else:
            df_val = conn.execute("""
                SELECT codigo, costo, lista_1 FROM stock_sucursal
                QUALIFY row_number() OVER (
                    PARTITION BY codigo
                    ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
                ) = 1
            """).df()

        fv_usada = conn.execute(
            "SELECT MAX(fecha_snapshot) FROM stock_sucursal"
            + (" WHERE fecha_snapshot <= ?" if fecha_valorizacion else ""),
            [fv] if fecha_valorizacion else [],
        ).fetchone()[0]

        df_dep = conn.execute(
            "SELECT codigodepo, nombre, es_logistica FROM depositos"
        ).df()
        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]
    finally:
        conn.close()

    if codigos is not None:
        df_g = df_g[df_g["codigo"].isin(set(codigos))]

    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"
    val = (df_val.set_index("codigo")[unit_col]
           if not df_val.empty else pd.Series(dtype="float64"))

    if df_g.empty:
        vacio = pd.DataFrame(columns=["codigodepo", "nombre"])
        vacio.attrs["categorias"] = cats_orden
        vacio.attrs["fecha_valorizacion"] = str(fv_usada) if fv_usada else None
        return vacio

    df_g = df_g.copy()
    df_g["unit"] = df_g["codigo"].map(val)
    df_g["neto_valorizado"] = df_g["neto_unidades"] * df_g["unit"]

    df_g["merma_contrib"] = 0.0
    m = df_g["es_merma"] & (df_g["neto_valorizado"] < 0)
    df_g.loc[m, "merma_contrib"] = -df_g.loc[m, "neto_valorizado"]
    df_g["merma_contrib_u"] = 0.0
    mu = df_g["es_merma"] & (df_g["neto_unidades"] < 0)
    df_g.loc[mu, "merma_contrib_u"] = -df_g.loc[mu, "neto_unidades"]

    # pivots por sucursal
    piv_val = df_g.pivot_table(index="codigodepo", columns="categoria",
                               values="neto_valorizado", aggfunc="sum")
    piv_uni = df_g.pivot_table(index="codigodepo", columns="categoria",
                               values="neto_unidades", aggfunc="sum")

    cats = list(cats_orden)
    for c in piv_val.columns:
        if c not in cats:
            cats.append(c)

    g = df_g.groupby("codigodepo")
    res = pd.DataFrame(index=piv_val.index)
    for c in cats:
        res[c] = piv_val[c] if c in piv_val.columns else 0.0
        res[f"{c} (u)"] = piv_uni[c] if c in piv_uni.columns else 0.0
    cols_num = cats + [f"{c} (u)" for c in cats]
    res[cols_num] = res[cols_num].fillna(0.0)

    res["skus"] = g["codigo"].nunique()
    con_merma = df_g[df_g["merma_contrib"] > 0].groupby("codigodepo")["codigo"].nunique()
    res["skus_con_merma"] = con_merma.reindex(res.index).fillna(0).astype(int)

    res["merma_total_valorizada"] = g["merma_contrib"].sum()
    res["merma_total_unidades"]   = g["merma_contrib_u"].sum()

    dv = df_g[df_g["es_venta"]].groupby("codigodepo")
    res["unidades_vendidas"] = (-dv["neto_unidades"].sum()).reindex(res.index).fillna(0.0)
    res["venta_neta"]        = (-dv["neto_valorizado"].sum()).reindex(res.index).fillna(0.0)

    res["pct_merma_sobre_ventas"] = pd.NA
    mv = res["venta_neta"] > 0
    res.loc[mv, "pct_merma_sobre_ventas"] = (
        res.loc[mv, "merma_total_valorizada"] / res.loc[mv, "venta_neta"] * 100
    ).round(4)

    res = res.reset_index().merge(df_dep, on="codigodepo", how="left")
    res["es_logistica"] = res["es_logistica"].fillna(False)
    cols = (["codigodepo", "nombre", "es_logistica", "skus", "skus_con_merma"]
            + [x for c in cats for x in (c, f"{c} (u)")]
            + ["merma_total_valorizada", "merma_total_unidades",
               "unidades_vendidas", "venta_neta", "pct_merma_sobre_ventas"])
    res = (res[cols]
           .sort_values("merma_total_valorizada", ascending=False)
           .reset_index(drop=True))
    res.attrs["categorias"] = cats
    res.attrs["fecha_valorizacion"] = str(fv_usada) if fv_usada else None
    return res
