"""
core/inventario/pedido_general.py — Pedido general de distribución (pull)

Versión multi-sucursal de `core.inventario.reposicion`: en vez de perfilar
una sucursal a la vez, cruza venta promedio ajustada y stock de VARIAS
sucursales en una sola pasada (una sola llamada a `serie_stock` con la
lista completa, no una por sucursal) para armar una hoja de distribución
consolidada — cuánto le conviene pedir a cada sucursal y cuánto hay que
despachar en total desde el CD.

No reemplaza ni modifica `core.inventario.reposicion` (que sigue siendo la
fuente para "Perfil de reposición" y "Pedido desde sucursal", una sucursal
por vez): comparte la misma fórmula de venta ajustada y de pedido por días,
pero agrupando por (codigo, codigodepo) en vez de solo codigo.

Uso:
    from core.inventario.pedido_general import perfil_pedido_general, tabla_pedido_general

    perfil = perfil_pedido_general("cliente_x", ["008", "010"],
                                   "2026-01-01", "2026-07-31",
                                   codigos=["1111033", "1111001"])
    tabla = tabla_pedido_general(perfil, dias_cobertura=15, etiquetas=suc_corto)
"""

import pandas as pd

from core.comun import conectar, DEPOSITOS_DISTRIBUCION
from core.inventario.series import serie_stock

# Misma constante y mismo criterio que core.inventario.reposicion._DIAS_POR_MES
_DIAS_POR_MES = 30.4375

# Solo se expone el stock del CD principal en la tabla ancha — mismo recorte
# que ya usa el reporte de "Pedido desde sucursal" (ver templates/reporte_pedido_sucursal.html).
_CD_PRINCIPAL = DEPOSITOS_DISTRIBUCION[0]


def _ventas_por_codigo_depo(
    proyecto: str, sucursales: list[str], fecha_desde: str, fecha_hasta: str,
    codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Ventas totales del período por (codigo, codigodepo), para varias
    sucursales a la vez — mismo criterio que
    `reposicion._ventas_por_codigo` (es_venta, signo volteado), pero
    agrupando también por codigodepo en una sola consulta.
    """
    cols = ["codigo", "codigodepo", "ventas_totales_periodo"]
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depos": list(sucursales), "desde": fecha_desde, "hasta": fecha_hasta}
        if codigos:
            conn.register("_codigos_ventas_gen",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND m.codigo IN (SELECT codigo FROM _codigos_ventas_gen)"
        df = conn.execute(f"""
            SELECT m.codigo, m.codigodepo,
                   (-1 * SUM(m.diferencia))::DOUBLE AS ventas_totales_periodo
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            WHERE m.codigodepo IN (SELECT unnest($depos))
              AND m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              AND t.es_venta
              {filtro_cod}
            GROUP BY m.codigo, m.codigodepo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    return df[cols]


def _stock_disponible_multi(
    proyecto: str, sucursales: list[str], fecha_stock: str | None,
    codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Stock del snapshot elegido por (codigo, codigodepo), para las
    sucursales pedidas MÁS los centros de distribución
    (`DEPOSITOS_DISTRIBUCION`) — mismo patrón que
    `reposicion._stock_disponible`, pero sin pivotear a columnas fijas
    todavía (eso lo hace el caller, una vez para el stock de cada
    sucursal y otra para el del CD). También trae uxb/costo/lista_1 (mismo
    origen, `stock_sucursal`).
    """
    cols = ["codigo", "codigodepo", "stock", "uxb", "costo", "lista_1"]
    if not fecha_stock:
        return pd.DataFrame(columns=cols)

    depos = sorted({*sucursales, *DEPOSITOS_DISTRIBUCION})
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depos": depos, "fs": fecha_stock}
        if codigos:
            conn.register("_codigos_stock_gen",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND codigo IN (SELECT codigo FROM _codigos_stock_gen)"
        df = conn.execute(f"""
            SELECT codigo, codigodepo, SUM(stock)::DOUBLE AS stock,
                   MAX(uxb)::DOUBLE AS uxb,
                   MAX(costo)::DOUBLE AS costo,
                   MAX(lista_1)::DOUBLE AS lista_1
            FROM stock_sucursal
            WHERE codigodepo IN (SELECT unnest($depos))
              AND fecha_snapshot = $fs::DATE
              {filtro_cod}
            GROUP BY codigo, codigodepo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    return df[cols]


def perfil_pedido_general(
    proyecto: str,
    sucursales: list[str],
    fecha_desde: str,
    fecha_hasta: str,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Perfil de pedido por SKU × sucursal, para varias sucursales a la vez:
    venta promedio ajustada (misma fórmula que
    `reposicion.perfil_reposicion` — ventas del período sobre los días con
    stock disponible, no sobre el período entero) y stock, tanto de cada
    sucursal como del CD principal.

    Reconstruye el stock UNA sola vez para todas las sucursales
    (`serie_stock` ya acepta una lista), en vez de perfilar sucursal por
    sucursal.

    Args:
        proyecto:    contexto.
        sucursales:  sucursales a incluir (orden preservado en attrs, para
                    que la tabla ancha respete el orden elegido por el
                    usuario).
        fecha_desde/hasta: período a analizar.
        codigos:     subconjunto de SKUs (None = todos los que tengan
                    movimiento en el período, en cualquiera de las
                    sucursales pedidas).

    Returns:
        DataFrame LARGO (una fila por SKU × sucursal): codigo, codigodepo,
        descripcion, rubro, marca, uxb, costo, lista_1,
        ventas_totales_periodo, dias_con_stock, ventas_promedio_ajustado,
        stock_sucursal, stock_cd_<código> (uno por depósito de
        DEPOSITOS_DISTRIBUCION). attrs: desde, hasta, fecha_stock,
        sucursales (en el orden pedido).
    """
    cols_cd = [f"stock_cd_{d}" for d in DEPOSITOS_DISTRIBUCION]
    cols = ["codigo", "codigodepo", "descripcion", "rubro", "marca", "uxb",
            "costo", "lista_1", "ventas_totales_periodo", "dias_con_stock",
            "ventas_promedio_ajustado", "stock_sucursal", *cols_cd]

    serie = serie_stock(proyecto, sucursales, fecha_desde, fecha_hasta, codigos=codigos)
    attrs = dict(desde=serie.attrs.get("desde"), hasta=serie.attrs.get("hasta"),
                 fecha_stock=serie.attrs.get("fecha_stock"), sucursales=list(sucursales))
    if serie.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    tmp = serie[["codigo", "codigodepo", "vigencia_dias", "stock"]].copy()
    tmp["dias_con_stock"] = tmp["vigencia_dias"].where(tmp["stock"] > 0, 0)
    dias_stock = tmp.groupby(["codigo", "codigodepo"])["dias_con_stock"].sum().reset_index()

    ventas = _ventas_por_codigo_depo(proyecto, sucursales, serie.attrs["desde"],
                                     serie.attrs["hasta"], codigos)
    stock_multi = _stock_disponible_multi(proyecto, sucursales, serie.attrs.get("fecha_stock"),
                                          codigos)

    df = dias_stock.merge(ventas, on=["codigo", "codigodepo"], how="left")
    df["ventas_totales_periodo"] = df["ventas_totales_periodo"].fillna(0.0)

    # Venta ajustada: se enmascara el divisor a NaN ANTES de dividir (no
    # después con .where()) — con pocas filas, pandas puede evaluar la
    # división elemento a elemento como un 0.0/0.0 de Python puro, que
    # lanza ZeroDivisionError en vez de devolver NaN (mismo cuidado que
    # reposicion.perfil_reposicion).
    dias_con_stock_div = df["dias_con_stock"].where(df["dias_con_stock"] > 0)
    df["ventas_promedio_ajustado"] = (
        df["ventas_totales_periodo"] / (dias_con_stock_div / _DIAS_POR_MES)
    )

    info_prod = serie.groupby("codigo")[["descripcion", "rubro", "marca"]].first().reset_index()
    df = df.merge(info_prod, on="codigo", how="left")

    if not stock_multi.empty:
        stock_suc = (stock_multi[stock_multi["codigodepo"].isin(sucursales)]
                     [["codigo", "codigodepo", "stock"]]
                     .rename(columns={"stock": "stock_sucursal"}))
        df = df.merge(stock_suc, on=["codigo", "codigodepo"], how="left")

        cd = stock_multi[stock_multi["codigodepo"].isin(DEPOSITOS_DISTRIBUCION)]
        piv = cd.pivot_table(index="codigo", columns="codigodepo", values="stock", aggfunc="sum")
        stock_cd = pd.DataFrame({"codigo": piv.index})
        for d in DEPOSITOS_DISTRIBUCION:
            stock_cd[f"stock_cd_{d}"] = piv[d].values if d in piv.columns else 0.0
        df = df.merge(stock_cd, on="codigo", how="left")

        producto_attrs = stock_multi.groupby("codigo")[["uxb", "costo", "lista_1"]].max().reset_index()
        df = df.merge(producto_attrs, on="codigo", how="left")
    else:
        df["stock_sucursal"] = pd.NA
        for c in [*cols_cd, "uxb", "costo", "lista_1"]:
            df[c] = pd.NA

    df["stock_sucursal"] = df["stock_sucursal"].fillna(0.0)
    df[cols_cd] = df[cols_cd].fillna(0.0)

    df = df[cols].sort_values(["codigo", "codigodepo"]).reset_index(drop=True)
    df.attrs.update(attrs)
    return df


def pedido_general_por_dias(perfil_largo: pd.DataFrame, dias: float) -> pd.Series:
    """
    Pedido recomendado (crudo) para cubrir `dias` de venta, por fila de
    `perfil_pedido_general` (cada una es un SKU en una sucursal): venta
    diaria ajustada por esa cantidad de días, menos el stock actual de esa
    sucursal. Igual fórmula que `reposicion.pedido_por_dias` — puede dar
    NaN (sin venta ajustada calculable) o negativo (ya sobra stock); no se
    ajusta acá, eso lo hace `tabla_pedido_general` para la vista final.
    """
    tasa_diaria = perfil_largo["ventas_promedio_ajustado"] / _DIAS_POR_MES
    return tasa_diaria * dias - perfil_largo["stock_sucursal"]


def _pedido_visible(pedido: pd.Series) -> pd.Series:
    """
    Transforma el pedido crudo a lo que se muestra: sin datos para
    calcularlo (NaN — sin movimientos de esa sucursal en el período, o sin
    venta ajustada con la que dividir) pasa a **-1**, un negativo real (ya
    sobra stock para la cobertura elegida) pasa a **vacío** (para que en
    Excel/CSV la celda quede en blanco), y el resto queda igual.
    """
    out = pedido.astype(float).copy()
    negativos = pedido.notna() & (pedido < 0)
    out[negativos] = float("nan")
    out[pedido.isna()] = -1.0
    return out


def tabla_pedido_general(
    perfil_largo: pd.DataFrame, dias_cobertura: float, etiquetas: dict,
) -> pd.DataFrame:
    """
    Pivotea el perfil largo (SKU × sucursal) a la hoja de distribución
    ancha: una fila por SKU, con la venta promedio y el stock de cada
    sucursal elegida en columnas propias (etiquetadas con `etiquetas`,
    típicamente el rótulo corto de `ui.comun.etiquetas_sucursal`), el
    stock del CD principal, el pedido recomendado por sucursal
    (ver `_pedido_visible`) y un pedido consolidado — la suma, entre
    sucursales, de los pedidos recomendados positivos únicamente (una
    sucursal con sobrante no resta del total a despachar).

    Args:
        perfil_largo: salida de perfil_pedido_general (usa attrs["sucursales"]
                     para fijar el orden de las columnas por sucursal).
        dias_cobertura: días de cobertura objetivo, igual para todas las
                       sucursales.
        etiquetas:    dict codigodepo -> rótulo corto, para nombrar las
                     columnas (p. ej. "vta_prom_San Juan").
    """
    df = perfil_largo.copy()
    df["pedido_recomendado"] = pedido_general_por_dias(df, dias_cobertura)

    consolidado = (
        df.assign(pedido_pos=df["pedido_recomendado"].clip(lower=0).fillna(0.0))
          .groupby("codigo")["pedido_pos"].sum()
    )

    col_cd = f"stock_cd_{_CD_PRINCIPAL}"
    base = (df.drop_duplicates("codigo")
              .set_index("codigo")[["descripcion", "marca", "rubro", "uxb",
                                    "costo", "lista_1", col_cd]])

    orden_sucursales = perfil_largo.attrs.get("sucursales") or sorted(df["codigodepo"].unique())
    abrevs = {suc: etiquetas.get(suc, str(suc)) for suc in orden_sucursales}

    out = base.copy()
    cols_vta, cols_stock, cols_pedido = [], [], []
    for suc in orden_sucursales:
        abrev = abrevs[suc]
        sub = df[df["codigodepo"] == suc].set_index("codigo")

        c_vta, c_stock, c_pedido = f"vta_prom_{abrev}", f"stock_{abrev}", f"pedido_{abrev}"
        out[c_vta] = sub["ventas_promedio_ajustado"].reindex(out.index)
        out[c_stock] = sub["stock_sucursal"].reindex(out.index).fillna(0.0)
        out[c_pedido] = _pedido_visible(sub["pedido_recomendado"].reindex(out.index))
        cols_vta.append(c_vta); cols_stock.append(c_stock); cols_pedido.append(c_pedido)

    out["pedido_consolidado"] = consolidado.reindex(out.index).fillna(0.0)

    orden_final = (["descripcion", "marca", "rubro", "uxb", "costo", "lista_1"]
                  + cols_vta + [col_cd] + cols_stock + cols_pedido + ["pedido_consolidado"])
    out = out[orden_final].reset_index()
    return out.sort_values("pedido_consolidado", ascending=False).reset_index(drop=True)
