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
  - Merma (numerador): neto con signo de las categorías es_merma por SKU,
    negado (faltante suma, ajuste positivo resta). Se compensan dentro del
    mismo código; al totalizar se anulan.
  - Venta (denominador): unidades netas de las categorías marcadas es_venta
    (la salida, en positivo), valorizadas desde el stock.
  - % merma = merma_total_valorizada / venta_neta * 100.

Retorna un DataFrame ancho: una fila por SKU con atributos (rubro, marca,
super rubro, gran super rubro), una columna $ por categoría y su gemela en
unidades (`<cat> (u)`), más totales y %. Las columnas de categorías es_merma
(Inventario, Ajustes) ya vienen con el signo negado (igual que
merma_total_valorizada): un faltante suma, un ajuste positivo resta, y las
categorías es_merma de un mismo SKU suman directo a merma_total_valorizada
(ej. Inventario + Ajustes = Merma). El resto de las categorías (Ventas,
Remitido...) conserva el signo crudo del ERP.
  - df.attrs["categorias"]          → columnas-categoría ($) en orden
  - df.attrs["fecha_valorizacion"]  → snapshot efectivamente usado
  - df.attrs["skus_inv"]            → SKUs con conteo de inventario
                                      (tipo='INV') en el período/selección
  - df.attrs["pct_skus_contados"]   → skus_inv / SKUs con movimientos * 100
  - columna "tuvo_inv"              → bool por SKU, mismo criterio (para
                                      poder recortar el % por rubro/marca)

Uso:
    from core.merma.analisis import calcular_merma

    df = calcular_merma(
        proyecto="cliente_alfa",
        codigodepo="002",            # o lista, o None = todas las sucursales
        fecha_desde="2026-01-01",
        fecha_hasta="2026-03-31",
        modo_valorizacion="costo",   # o "lista_1" o "mixto"
        fecha_valorizacion=None,     # o "YYYY-MM-DD" (snapshot a usar)
    )
"""

import duckdb
import pandas as pd


# Helpers compartidos (definidos en core.comun; se re-exportan para
# compatibilidad con quien todavía importa desde core.merma).
from core.comun import (  # noqa: E402
    MODOS_VALORIZACION,
    MODOS_VALORIZACION_MERMA,
    conectar,
    validar_modo_valorizacion,
    precio_unitario_fila,
    precios_snapshot,
    snapshot_usado,
    adjuntar_rubros,
    listar_categorias,
    listar_fechas_valorizacion,
)


# ---------------------------------------------------------------------------
# Ensamblado (pandas)
# ---------------------------------------------------------------------------

def _ensamblar(df_comp, df_val, df_art, df_est, cats_orden,
               modo, desde, hasta) -> pd.DataFrame:
    # Precio "de venta" para venta_neta / valor_unitario: costo en modo
    # "costo", lista_1 en modo "lista_1" y en "mixto" (ahí las ventas van
    # siempre a precio de lista — ver precio_unitario_fila).
    val_venta_col = "costo" if modo == "costo" else "lista_1"
    val_venta = (df_val.set_index("codigo")[val_venta_col]
                if not df_val.empty else pd.Series(dtype="float64"))

    universo = sorted(set(df_comp["codigo"]))

    # --- pivots por categoría: unidades y valorizado -------------------------
    if not df_comp.empty:
        df_comp = df_comp.copy()
        df_comp["unit"] = precio_unitario_fila(
            df_comp["codigo"], modo, df_val, es_venta=df_comp["es_venta"])
        df_comp["neto_valorizado"] = df_comp["neto_unidades"] * df_comp["unit"]

        pivot_val = df_comp.pivot_table(
            index="codigo", columns="categoria",
            values="neto_valorizado", aggfunc="sum",
        )
        pivot_uni = df_comp.pivot_table(
            index="codigo", columns="categoria",
            values="neto_unidades", aggfunc="sum",
        )
        # Merma por SKU = neto (con signo) de las categorías es_merma, negado
        # para que un faltante sea positivo. Las categorías se compensan entre
        # sí dentro del mismo SKU (INV vs CS del mismo código); al totalizar,
        # los netos se anulan. Puede ser negativa (sobrante neto del SKU).
        es_m = df_comp[df_comp["es_merma"]]
        merma_total   = -es_m.groupby("codigo")["neto_valorizado"].sum()
        merma_total_u = -es_m.groupby("codigo")["neto_unidades"].sum()
        # unidades/valor vendido = -(neto de categorías es_venta): la venta es
        # salida. Se toma de neto_valorizado (no unidades × precio único) para
        # que el modo "mixto" (precio por fila) valorice bien la venta.
        vs     = df_comp[df_comp["es_venta"]].groupby("codigo")["neto_unidades"].sum()
        vs_val = df_comp[df_comp["es_venta"]].groupby("codigo")["neto_valorizado"].sum()
        cats_merma = set(df_comp.loc[df_comp["es_merma"], "categoria"])
    else:
        pivot_val = pd.DataFrame()
        pivot_uni = pd.DataFrame()
        merma_total = merma_total_u = vs = vs_val = pd.Series(dtype="float64")
        cats_merma = set()

    # Columnas-categoría: orden del catálogo + cualquier extra que aparezca
    cats = list(cats_orden)
    for c in pivot_val.columns:
        if c not in cats:
            cats.append(c)

    res = pd.DataFrame(index=pd.Index(universo, name="codigo"))
    for c in cats:
        # Categorías es_merma se muestran negadas (faltante positivo, ajuste
        # positivo negativo) para que sumen directo a merma_total_valorizada
        # (INV + CS = Merma). El resto conserva el signo crudo del ERP.
        signo = -1.0 if c in cats_merma else 1.0
        res[c] = signo * pivot_val[c] if c in pivot_val.columns else 0.0
        res[f"{c} (u)"] = signo * pivot_uni[c] if c in pivot_uni.columns else 0.0
    cols_num = cats + [f"{c} (u)" for c in cats]
    res[cols_num] = res[cols_num].fillna(0.0)

    res["merma_total_valorizada"] = merma_total.reindex(res.index).fillna(0.0)
    res["merma_total_unidades"]   = merma_total_u.reindex(res.index).fillna(0.0)

    # --- venta (denominador): unidades vendidas × precio de stock -----------
    res["unidades_vendidas"] = (-vs).reindex(res.index).fillna(0.0)
    res["venta_neta"] = (-vs_val).reindex(res.index).fillna(0.0)

    # --- % merma sobre ventas ------------------------------------------------
    res["pct_merma_sobre_ventas"] = pd.NA
    m = res["venta_neta"] > 0
    res.loc[m, "pct_merma_sobre_ventas"] = (
        res.loc[m, "merma_total_valorizada"] / res.loc[m, "venta_neta"] * 100
    ).round(4)

    # --- atributos: descripción, rubro, marca, jerarquía ---------------------
    # Informativa: el precio "de venta" usado para valorizar venta_neta. En
    # modo "mixto" NO representa el precio real aplicado fila a fila (que
    # varía por categoría: lista_1 en ventas, costo en el resto).
    res["valor_unitario"] = res.index.map(val_venta)
    res = res.reset_index().merge(df_art, on="codigo", how="left")
    res = adjuntar_rubros(res, df_est)

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
    codigodepo: str | list[str] | None,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Calcula merma por SKU para una sucursal, varias, o todas, en un rango de
    fechas.

    Args:
        proyecto:           Nombre del proyecto
        codigodepo:         Código de sucursal, una lista de códigos, o
                            None = todas las sucursales
        fecha_desde:        Inicio del período (YYYY-MM-DD), inclusive
        fecha_hasta:        Fin del período (YYYY-MM-DD), inclusive
        modo_valorizacion:  "costo" (default), "lista_1" o "mixto" (ventas a
                            lista_1, resto —remitos, ajustes, inventario— a
                            costo).
        fecha_valorizacion: Fecha del snapshot de stock a usar para valorizar
                            (se toma el más reciente <= esa fecha). None =
                            último snapshot disponible (valor actual).

    Returns:
        DataFrame ancho (una fila por SKU). `df.attrs["categorias"]` lista las
        columnas-categoría; `df.attrs["fecha_valorizacion"]` es el snapshot usado.
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)

    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError(f"fecha_desde ({desde}) no puede ser posterior a fecha_hasta ({hasta})")

    conn = conectar(proyecto)
    try:
        # Componentes: neto por SKU y categoría (LEFT JOIN para no perder
        # subtipos sin mapear → caen en '(sin categoría)')
        depos = None
        if codigodepo:
            depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
        filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos else ""
        params = {"desde": desde, "hasta": hasta}
        if depos:
            params["depos"] = depos

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
        df_val = precios_snapshot(conn, fecha_valorizacion)

        # Snapshot efectivamente usado (para trazabilidad en la UI)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

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

        # SKUs contados: cuáles de los SKUs con movimientos en el período
        # tuvieron un conteo de inventario físico (tipo='INV', SIEMPRE por
        # `tipo` — el ERP no informa TIPOMOV='INV' — ver core/comun.py). Se
        # cuenta la presencia del conteo, no su neto: un SKU contado sin
        # diferencia (neto 0) sigue siendo un SKU contado.
        codigos_inv = conn.execute(f"""
            SELECT DISTINCT codigo FROM movimientos
            WHERE tipo = 'INV' AND fecha >= $desde::DATE AND fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()["codigo"]
    finally:
        conn.close()

    df = _ensamblar(df_comp, df_val, df_art, df_est, cats_orden,
                    modo_valorizacion, desde, hasta)
    df.attrs["fecha_valorizacion"] = fv_usada
    df["tuvo_inv"] = df["codigo"].isin(set(codigos_inv))
    skus_inv = int(df["tuvo_inv"].sum())
    df.attrs["skus_inv"] = skus_inv
    df.attrs["pct_skus_contados"] = (skus_inv / len(df) * 100) if len(df) > 0 else None
    return df


# ---------------------------------------------------------------------------
# Comparativa por sucursal
# ---------------------------------------------------------------------------

def _ensamblar_sucursal(df_g: pd.DataFrame, df_val: pd.DataFrame,
                        df_dep: pd.DataFrame, cats_orden: list[str],
                        df_inv: pd.DataFrame, modo: str) -> pd.DataFrame:
    """
    Ensamblado compartido por merma_por_sucursal y merma_por_sucursal_dinamica:
    de `df_g` (codigodepo, codigo, categoria, es_merma, es_venta, neto_unidades)
    arma la tabla ancha por sucursal (una fila por codigodepo). No setea
    `fecha_valorizacion` en attrs — eso lo agrega cada función llamante.
    """
    if df_g.empty:
        vacio = pd.DataFrame(columns=["codigodepo", "nombre"])
        vacio.attrs["categorias"] = list(cats_orden)
        return vacio

    df_g = df_g.copy()
    df_g["unit"] = precio_unitario_fila(
        df_g["codigo"], modo, df_val, es_venta=df_g["es_venta"])
    df_g["neto_valorizado"] = df_g["neto_unidades"] * df_g["unit"]

    # pivots por sucursal
    piv_val = df_g.pivot_table(index="codigodepo", columns="categoria",
                               values="neto_valorizado", aggfunc="sum")
    piv_uni = df_g.pivot_table(index="codigodepo", columns="categoria",
                               values="neto_unidades", aggfunc="sum")

    cats = list(cats_orden)
    for c in piv_val.columns:
        if c not in cats:
            cats.append(c)

    cats_merma = set(df_g.loc[df_g["es_merma"], "categoria"])

    g = df_g.groupby("codigodepo")
    res = pd.DataFrame(index=piv_val.index)
    for c in cats:
        # ver nota de _ensamblar: categorías es_merma se muestran negadas.
        signo = -1.0 if c in cats_merma else 1.0
        res[c] = signo * piv_val[c] if c in piv_val.columns else 0.0
        res[f"{c} (u)"] = signo * piv_uni[c] if c in piv_uni.columns else 0.0
    cols_num = cats + [f"{c} (u)" for c in cats]
    res[cols_num] = res[cols_num].fillna(0.0)

    # Merma = neto (con signo) de es_merma; los netos por SKU se anulan al sumar.
    es_m = df_g[df_g["es_merma"]]
    merma_sku = -es_m.groupby(["codigodepo", "codigo"])["neto_valorizado"].sum()
    con_merma = (merma_sku[merma_sku > 0].reset_index()
                 .groupby("codigodepo")["codigo"].nunique())

    res["skus"] = g["codigo"].nunique()
    res["skus_con_merma"] = con_merma.reindex(res.index).fillna(0).astype(int)
    skus_inv_suc = df_inv.groupby("codigodepo")["codigo"].nunique()
    res["skus_inv"] = skus_inv_suc.reindex(res.index).fillna(0).astype(int)
    res["pct_skus_contados"] = pd.NA
    msi = res["skus"] > 0
    res.loc[msi, "pct_skus_contados"] = (
        res.loc[msi, "skus_inv"] / res.loc[msi, "skus"] * 100
    ).round(2)
    res["merma_total_valorizada"] = (-es_m.groupby("codigodepo")["neto_valorizado"].sum()
                                     ).reindex(res.index).fillna(0.0)
    res["merma_total_unidades"]   = (-es_m.groupby("codigodepo")["neto_unidades"].sum()
                                     ).reindex(res.index).fillna(0.0)

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
    cols = (["codigodepo", "nombre", "es_logistica", "skus", "skus_con_merma",
             "skus_inv", "pct_skus_contados"]
            + [x for c in cats for x in (c, f"{c} (u)")]
            + ["merma_total_valorizada", "merma_total_unidades",
               "unidades_vendidas", "venta_neta", "pct_merma_sobre_ventas"])
    res = (res[cols]
           .sort_values("merma_total_valorizada", ascending=False)
           .reset_index(drop=True))
    res.attrs["categorias"] = cats
    return res


def merma_por_sucursal(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    codigos: list[str] | None = None,
    codigodepo: list[str] | str | None = None,
) -> pd.DataFrame:
    """
    Comparativa agregada por sucursal para un rango de fechas: una fila por
    sucursal con la merma/venta por categoría ($ y unidades), totales y %.

    Misma semántica que calcular_merma: la merma es el neto con signo de las
    categorías es_merma (negado), que se anula al sumar por sucursal.

    Args:
        codigos:    lista opcional de SKUs a incluir (para respetar filtros
                    de rubro/marca de la UI). None = todos.
        codigodepo: sucursal, lista de sucursales, o None = todas — para
                    acotar la comparativa al mismo subconjunto elegido en
                    la página.

    Returns:
        DataFrame: codigodepo, nombre, skus, skus_con_merma, skus_inv,
        pct_skus_contados, columnas por categoría ($ y "(u)"),
        merma_total_valorizada, merma_total_unidades, unidades_vendidas,
        venta_neta, pct_merma_sobre_ventas.
        attrs: "categorias", "fecha_valorizacion".
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())

    depos = None
    if codigodepo:
        depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos else ""
    params = {"desde": desde, "hasta": hasta}
    if depos:
        params["depos"] = depos

    conn = conectar(proyecto)
    try:
        df_g = conn.execute(f"""
            WITH rango AS (
                SELECT codigodepo, codigo, tipo, SUM(diferencia) AS neto_unidades
                FROM movimientos
                WHERE fecha >= $desde::DATE AND fecha <= $hasta::DATE
                  {filtro_depo}
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
        """, params).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

        df_dep = conn.execute(
            "SELECT codigodepo, nombre, es_logistica FROM depositos"
        ).df()
        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]

        # SKUs contados por sucursal: igual criterio que calcular_merma,
        # siempre por tipo='INV' (nunca tipomov).
        df_inv = conn.execute(f"""
            SELECT DISTINCT codigodepo, codigo FROM movimientos
            WHERE tipo = 'INV' AND fecha >= $desde::DATE AND fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()
    finally:
        conn.close()

    if codigos is not None:
        df_g = df_g[df_g["codigo"].isin(set(codigos))]
        df_inv = df_inv[df_inv["codigo"].isin(set(codigos))]

    res = _ensamblar_sucursal(df_g, df_val, df_dep, cats_orden, df_inv, modo_valorizacion)
    res.attrs["fecha_valorizacion"] = fv_usada
    return res


def merma_por_sucursal_dinamica(
    proyecto: str,
    tipo_comprobante: str,
    cantidad: int,
    fecha_hasta: str,
    codigodepo: str | list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Comparativa agregada por sucursal para el corte dinámico: usa la misma
    ventana propia por SKU que calcular_merma_dinamica (fecha de corte = la
    `cantidad`-ésima fecha con comprobante de `tipo_comprobante` hacia atrás
    desde `fecha_hasta`), pero agrupada por codigodepo en vez de por codigo
    — completa el reporte imprimible del corte dinámico con el detalle por
    sucursal.

    Args:
        codigos: subconjunto opcional de SKUs a incluir (para respetar los
                 filtros ya aplicados en la UI, igual que merma_por_sucursal).
                 None = todos los que alcancen `cantidad` comprobantes.

    Returns:
        Igual forma que merma_por_sucursal, más `dias_promedio_corte`
        (promedio, por sucursal, de cuántos días retrocedió el corte para
        cada SKU hasta juntar `cantidad` comprobantes de `tipo_comprobante`
        — solo sobre pares SKU+sucursal que alcanzaron la condición) y
        `n_pares_corte` (cantidad de esos pares, para poder ponderar un
        promedio general). attrs: categorias, fecha_valorizacion.
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    hasta = str(pd.to_datetime(fecha_hasta).date())

    cortes = fechas_corte_comprobante(proyecto, tipo_comprobante, cantidad,
                                      hasta, codigodepo)
    cortes_ok = cortes[cortes["suficientes"]].copy()
    cortes_ok["fecha_corte"] = pd.to_datetime(cortes_ok["fecha_corte"])

    depos = None
    if codigodepo:
        depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if depos else ""
    params: dict = {"hasta": hasta}
    if depos:
        params["depos"] = depos

    conn = conectar(proyecto)
    try:
        conn.register("_cortes", cortes_ok[["codigo", "codigodepo", "fecha_corte"]])

        df_g = conn.execute(f"""
            WITH rango AS (
                SELECT m.codigodepo, m.codigo, m.tipo, SUM(m.diferencia) AS neto_unidades
                FROM movimientos m
                JOIN _cortes c ON m.codigo = c.codigo AND m.codigodepo = c.codigodepo
                WHERE m.fecha > c.fecha_corte::DATE AND m.fecha <= $hasta::DATE
                  {filtro_depo}
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
        """, params).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

        df_dep = conn.execute(
            "SELECT codigodepo, nombre, es_logistica FROM depositos"
        ).df()
        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]

        df_inv = conn.execute(f"""
            SELECT DISTINCT m.codigodepo, m.codigo FROM movimientos m
            JOIN _cortes c ON m.codigo = c.codigo AND m.codigodepo = c.codigodepo
            WHERE m.tipo = 'INV' AND m.fecha > c.fecha_corte::DATE
              AND m.fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()
    finally:
        conn.close()

    if codigos is not None:
        df_g = df_g[df_g["codigo"].isin(set(codigos))]
        df_inv = df_inv[df_inv["codigo"].isin(set(codigos))]
        cortes_ok = cortes_ok[cortes_ok["codigo"].isin(set(codigos))]

    res = _ensamblar_sucursal(df_g, df_val, df_dep, cats_orden, df_inv, modo_valorizacion)
    res.attrs["fecha_valorizacion"] = fv_usada

    # Promedio de días que retrocedió el corte por sucursal (cuántos días
    # atrás, en general, hubo que ir para juntar `cantidad` comprobantes de
    # `tipo_comprobante` en esa sucursal) — solo sobre pares SKU+sucursal que
    # alcanzaron la condición.
    dias_suc = cortes_ok.assign(
        dias=(pd.Timestamp(hasta) - cortes_ok["fecha_corte"]).dt.days
    ).groupby("codigodepo").agg(
        dias_promedio_corte=("dias", "mean"),
        n_pares_corte=("dias", "count"),
    ).reset_index()
    res = res.merge(dias_suc, on="codigodepo", how="left")
    return res


# ---------------------------------------------------------------------------
# Corte dinámico por comprobante (ventana de fechas propia por SKU)
# ---------------------------------------------------------------------------
#
# A diferencia de calcular_merma (un único rango de fechas para todos los
# SKUs), acá cada SKU define su propio "desde": la fecha del N-ésimo
# comprobante de un tipo elegido (INV, CS, NCB, RI...) hacia atrás desde un
# "hasta" común, sin contar ese comprobante. Útil para preguntas del tipo
# "traeme la merma de cada SKU desde su segundo inventario físico para acá".

def fechas_corte_comprobante(
    proyecto: str,
    tipo: str,
    cantidad: int,
    fecha_hasta: str,
    codigodepo: str | list[str] | None = None,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Por (SKU, sucursal): fecha del comprobante N-ésimo de `tipo` hacia atrás
    desde `fecha_hasta`. Cada sucursal retrocede sobre **su propio**
    historial de forma aislada — el mismo SKU puede cortar en una fecha muy
    distinta en cada sucursal, según cuándo pasó cada una su N-ésimo
    comprobante — así que se particiona por (codigo, codigodepo), no solo
    por codigo.

    Se cuenta por **fecha distinta**, no por comprobante individual: si dos
    depósitos comparten sucursal (mismo codigodepo, dos números de
    comprobante el mismo día) eso ya sería el mismo grupo de partición; para
    una sola sucursal, contar por fecha distinta evita que varias líneas del
    mismo día se cuenten como vueltas separadas.

    Args:
        tipo:        subtipo ERP a buscar (columna `tipo` de movimientos,
                     p.ej. "INV", "NCB", "RI", "CS").
        cantidad:    cuántas fechas de comprobante retroceder (1 = la más
                     reciente).
        fecha_hasta: tope superior de búsqueda, inclusive.
        codigodepo:  sucursal, lista, o None = todas.
        codigos:     subconjunto opcional de SKUs (None = todos) — para
                     acotar la consulta cuando solo hace falta un SKU
                     puntual (p.ej. el drill-down de la UI).

    Returns:
        DataFrame: codigo, codigodepo, fecha_corte (fecha N-ésima para esa
        combinación — se excluye del período resultante; NaT si no hay
        suficientes), n_disponibles (fechas distintas de `tipo` hasta
        fecha_hasta en esa sucursal), suficientes (bool: n_disponibles >=
        cantidad).
    """
    if cantidad < 1:
        raise ValueError("cantidad debe ser >= 1")
    hasta = str(pd.to_datetime(fecha_hasta).date())
    depos = [codigodepo] if isinstance(codigodepo, str) else (list(codigodepo) if codigodepo else None)

    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos else ""
    filtro_cod = "AND codigo IN (SELECT unnest($codigos))" if codigos else ""
    params = {"tipo": tipo, "hasta": hasta, "cantidad": cantidad}
    if depos:
        params["depos"] = depos
    if codigos:
        params["codigos"] = list(codigos)

    conn = conectar(proyecto)
    try:
        df = conn.execute(f"""
            WITH fechas AS (
                SELECT DISTINCT codigo, codigodepo, fecha
                FROM movimientos
                WHERE tipo = $tipo AND fecha <= $hasta::DATE
                  {filtro_depo}
                  {filtro_cod}
            ),
            rankeados AS (
                SELECT codigo, codigodepo, fecha,
                       ROW_NUMBER() OVER (PARTITION BY codigo, codigodepo
                                          ORDER BY fecha DESC) AS rn
                FROM fechas
            )
            SELECT codigo, codigodepo,
                   MAX(fecha) FILTER (WHERE rn = $cantidad) AS fecha_corte,
                   COUNT(*)::INT AS n_disponibles
            FROM rankeados
            GROUP BY codigo, codigodepo
        """, params).df()
    finally:
        conn.close()

    df["suficientes"] = df["n_disponibles"] >= cantidad
    return df


def calcular_merma_dinamica(
    proyecto: str,
    tipo_comprobante: str,
    cantidad: int,
    fecha_hasta: str,
    codigodepo: str | list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Igual resultado que calcular_merma, pero la ventana de fechas es POR SKU
    **y por sucursal**: usa fechas_corte_comprobante() para fijar, por cada
    combinación (SKU, sucursal), fecha_desde = fecha del comprobante
    N-ésimo de esa sucursal (de `tipo_comprobante`) + 1 día — el propio
    comprobante de corte queda excluido. Cada sucursal retrocede sobre su
    propio historial de forma aislada (dos sucursales del mismo SKU pueden
    cortar en fechas muy distintas); los movimientos de cada combinación se
    traen con su propia ventana y se suman para el total del SKU, con
    `fecha_hasta` fija para todas.

    Una combinación (SKU, sucursal) sin `cantidad` comprobantes de ese tipo
    antes de `fecha_hasta` aporta 0 (no rompe el SKU: este igual aparece si
    otra sucursal seleccionada sí alcanzó la condición). Un SKU solo
    desaparece del todo si NINGUNA sucursal seleccionada alcanzó la
    condición.

    Returns:
        Igual forma que calcular_merma, más por SKU: `fecha_desde_min` /
        `fecha_desde_max` (rango de inicio de ventana entre las sucursales
        que aportaron; iguales si es una sola sucursal o todas cortan el
        mismo día), `n_sucursales_incluidas`, `n_sucursales_excluidas`
        (combinaciones de ese SKU que no alcanzaron la condición).
        df.attrs agrega: tipo_comprobante, cantidad, fecha_hasta,
        skus_excluidos (SKUs ausentes del resultado), pares_totales,
        pares_excluidos (a nivel SKU+sucursal, para transparencia).
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    hasta = str(pd.to_datetime(fecha_hasta).date())

    cortes = fechas_corte_comprobante(proyecto, tipo_comprobante, cantidad,
                                      hasta, codigodepo)
    pares_totales = len(cortes)
    pares_excluidos = int((~cortes["suficientes"]).sum())
    cortes_ok = cortes[cortes["suficientes"]].copy()
    cortes_ok["fecha_corte"] = pd.to_datetime(cortes_ok["fecha_corte"])
    cortes_ok["fecha_desde"] = cortes_ok["fecha_corte"] + pd.Timedelta(days=1)
    skus_con_alguna_sucursal = set(cortes_ok["codigo"])

    depos = None
    if codigodepo:
        depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if depos else ""
    params: dict = {"hasta": hasta}
    if depos:
        params["depos"] = depos

    conn = conectar(proyecto)
    try:
        conn.register("_cortes", cortes_ok[["codigo", "codigodepo", "fecha_corte"]])

        df_comp = conn.execute(f"""
            WITH rango AS (
                SELECT m.codigo, m.tipo, SUM(m.diferencia) AS neto_unidades
                FROM movimientos m
                JOIN _cortes c ON m.codigo = c.codigo AND m.codigodepo = c.codigodepo
                WHERE m.fecha > c.fecha_corte::DATE AND m.fecha <= $hasta::DATE
                  {filtro_depo}
                GROUP BY m.codigo, m.tipo
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

        df_val = precios_snapshot(conn, fecha_valorizacion)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos"
        ).df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()

        cats_orden = [
            r[0] for r in conn.execute("""
                SELECT categoria FROM tipos_categoria
                GROUP BY categoria ORDER BY MIN(orden), categoria
            """).fetchall()
        ]

        codigos_inv = conn.execute(f"""
            SELECT DISTINCT m.codigo FROM movimientos m
            JOIN _cortes c ON m.codigo = c.codigo AND m.codigodepo = c.codigodepo
            WHERE m.tipo = 'INV' AND m.fecha > c.fecha_corte::DATE
              AND m.fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()["codigo"]
    finally:
        conn.close()

    df = _ensamblar(df_comp, df_val, df_art, df_est, cats_orden,
                    modo_valorizacion, hasta, hasta)
    df.attrs["fecha_valorizacion"] = fv_usada
    df = df.drop(columns=["fecha_desde"])  # una sola fecha ya no aplica

    resumen_cortes = (cortes_ok.groupby("codigo")
                      .agg(fecha_desde_min=("fecha_desde", "min"),
                           fecha_desde_max=("fecha_desde", "max"),
                           n_sucursales_incluidas=("codigodepo", "nunique"))
                      .reset_index())
    excl_por_sku = (cortes[~cortes["suficientes"]].groupby("codigo")["codigodepo"]
                    .nunique().rename("n_sucursales_excluidas").reset_index())
    resumen_cortes = resumen_cortes.merge(excl_por_sku, on="codigo", how="left")
    resumen_cortes["n_sucursales_excluidas"] = (
        resumen_cortes["n_sucursales_excluidas"].fillna(0).astype(int))
    for c in ("fecha_desde_min", "fecha_desde_max"):
        resumen_cortes[c] = resumen_cortes[c].dt.date

    df = df.merge(resumen_cortes, on="codigo", how="left")

    df["tuvo_inv"] = df["codigo"].isin(set(codigos_inv))
    skus_inv = int(df["tuvo_inv"].sum())
    df.attrs["skus_inv"] = skus_inv
    df.attrs["pct_skus_contados"] = (skus_inv / len(df) * 100) if len(df) > 0 else None
    df.attrs["tipo_comprobante"] = tipo_comprobante
    df.attrs["cantidad"] = cantidad
    df.attrs["fecha_hasta"] = hasta
    # SKUs ausentes del resultado: ninguna sucursal alcanzó la condición, o
    # sí alcanzó pero no tuvo movimientos dentro de su ventana.
    sin_sucursal_suficiente = set(cortes["codigo"]) - skus_con_alguna_sucursal
    con_sucursal_sin_movimientos = skus_con_alguna_sucursal - set(df["codigo"])
    df.attrs["skus_excluidos"] = len(sin_sucursal_suficiente | con_sucursal_sin_movimientos)
    df.attrs["pares_totales"] = pares_totales
    df.attrs["pares_excluidos"] = pares_excluidos
    return df
