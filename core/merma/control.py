"""
core/control_merma.py — Control y auditoría de la merma

Tres análisis sobre `movimientos` + `tipos_categoria` + `stock_sucursal`:

  1. evolucion_mensual():   merma/venta valorizada por mes → tendencia y %.
  2. movimientos_outliers(): movimientos individuales de categorías de merma
                             ordenados por impacto $ — detecta ajustes anómalos
                             (ej: un CS de $363K que domina el período).
  3. ajustes_por():          ranking de merma generada por usuario o por
                             motivo de ajuste (tipo_aj), × sucursal — control
                             de errores de operación / fraude.

Valorización: igual que core.merma — siempre desde stock_sucursal (costo,
lista_1 o mixto: ventas a lista_1, resto a costo), snapshot elegible
(default: el más reciente).
"""

import pandas as pd

from core.comun import (
    conectar, validar_modo_valorizacion, precios_snapshot,
    MODOS_VALORIZACION_MERMA, precio_unitario_fila,
)


# ---------------------------------------------------------------------------
# 1. Evolución mensual
# ---------------------------------------------------------------------------

def evolucion_mensual(
    proyecto: str,
    codigodepo: str | list[str] | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Merma y venta valorizadas por mes. La merma es el neto con signo de las
    categorías es_merma (negado, se compensan entre sí), agregado por mes;
    la venta idem con es_venta.

    Args:
        codigodepo: sucursal, lista de sucursales, o None = todas.

    Returns:
        DataFrame por mes: columnas $ por categoría de merma, merma_total,
        merma_unidades, venta_neta y pct. attrs: "categorias_merma".
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    conn = conectar(proyecto)
    try:
        depos = None
        if codigodepo:
            depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
        filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos else ""
        filtro_desde = "AND fecha >= $desde::DATE" if fecha_desde else ""
        filtro_hasta = "AND fecha <= $hasta::DATE" if fecha_hasta else ""
        params = {}
        if depos:
            params["depos"] = depos
        if fecha_desde:
            params["desde"] = str(pd.to_datetime(fecha_desde).date())
        if fecha_hasta:
            params["hasta"] = str(pd.to_datetime(fecha_hasta).date())

        df_g = conn.execute(f"""
            WITH rango AS (
                SELECT date_trunc('month', fecha) AS mes, codigo, tipo,
                       SUM(diferencia) AS neto_unidades
                FROM movimientos
                WHERE 1=1 {filtro_depo} {filtro_desde} {filtro_hasta}
                GROUP BY 1, 2, 3
            )
            SELECT r.mes, r.codigo,
                   COALESCE(t.categoria, '(sin categoría)') AS categoria,
                   COALESCE(t.es_merma, FALSE)              AS es_merma,
                   COALESCE(t.es_venta, FALSE)              AS es_venta,
                   SUM(r.neto_unidades)                     AS neto_unidades
            FROM rango r
            LEFT JOIN tipos_categoria t ON r.tipo = t.tipo
            GROUP BY 1, 2, 3, 4, 5
        """, params if params else []).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
        cats_merma = [r[0] for r in conn.execute("""
            SELECT categoria FROM tipos_categoria WHERE es_merma
            GROUP BY categoria ORDER BY MIN(orden), categoria
        """).fetchall()]
    finally:
        conn.close()

    if df_g.empty:
        vacio = pd.DataFrame(columns=["mes"])
        vacio.attrs["categorias_merma"] = cats_merma
        return vacio

    df_g["neto_valorizado"] = df_g["neto_unidades"] * precio_unitario_fila(
        df_g["codigo"], modo_valorizacion, df_val, es_venta=df_g["es_venta"])

    # Merma = neto (con signo) de es_merma, negado; las categorías se compensan
    # y aportan su neto negado (Inventario suma, un Ajuste positivo resta).
    es_m = df_g[df_g["es_merma"]]
    piv = (-es_m.pivot_table(index="mes", columns="categoria",
                             values="neto_valorizado", aggfunc="sum")
           if not es_m.empty else pd.DataFrame())

    g = df_g.groupby("mes")
    res = pd.DataFrame(index=g.size().index)
    for c in cats_merma:
        res[c] = piv[c] if c in getattr(piv, "columns", []) else 0.0
    res[cats_merma] = res[cats_merma].fillna(0.0)
    res["merma_total"]    = (-es_m.groupby("mes")["neto_valorizado"].sum()).reindex(res.index).fillna(0.0)
    res["merma_unidades"] = (-es_m.groupby("mes")["neto_unidades"].sum()).reindex(res.index).fillna(0.0)

    dv = df_g[df_g["es_venta"]].groupby("mes")["neto_valorizado"].sum()
    res["venta_neta"] = (-dv).reindex(res.index).fillna(0.0)

    res["pct_merma_sobre_ventas"] = pd.NA
    ok = res["venta_neta"] > 0
    res.loc[ok, "pct_merma_sobre_ventas"] = (
        res.loc[ok, "merma_total"] / res.loc[ok, "venta_neta"] * 100
    ).round(4)

    res = res.reset_index().rename(columns={"index": "mes"})
    res["mes"] = pd.to_datetime(res["mes"]).dt.strftime("%Y-%m")
    res = res.sort_values("mes").reset_index(drop=True)
    res.attrs["categorias_merma"] = cats_merma
    return res


# ---------------------------------------------------------------------------
# 2. Outliers: movimientos individuales de mayor impacto
# ---------------------------------------------------------------------------

def movimientos_outliers(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    codigodepo: str | list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    top_n: int = 50,
) -> pd.DataFrame:
    """
    Movimientos individuales de categorías de merma, ordenados por impacto
    valorizado absoluto. Incluye el % que cada movimiento representa sobre
    la merma total del período (para dimensionar el outlier).

    Args:
        codigodepo: sucursal, lista de sucursales, o None = todas.
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    conn = conectar(proyecto)
    try:
        depos = None
        if codigodepo:
            depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
        filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if depos else ""
        params = {
            "desde": str(pd.to_datetime(fecha_desde).date()),
            "hasta": str(pd.to_datetime(fecha_hasta).date()),
        }
        if depos:
            params["depos"] = depos

        df = conn.execute(f"""
            SELECT m.fecha, m.codigodepo, d.nombre AS sucursal,
                   m.tipomov, m.tipo, t.categoria, m.numero, m.usuario,
                   m.codigo, a.descripcion,
                   m.diferencia::DOUBLE AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_merma
            LEFT JOIN depositos d  ON m.codigodepo = d.codigodepo
            LEFT JOIN articulos a  ON m.codigo = a.codigo
            WHERE m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
    finally:
        conn.close()

    if df.empty:
        return df

    # Sólo ve movimientos de categorías es_merma (nunca de venta): en modo
    # "mixto" el precio resuelve siempre a costo (es_venta=None).
    unit = precio_unitario_fila(df["codigo"], modo_valorizacion, df_val)
    df["valor"] = (df["unidades"] * unit.astype(float)).fillna(0.0)

    merma_periodo = float(-df.loc[df["valor"] < 0, "valor"].sum())
    df["pct_merma_periodo"] = pd.NA
    if merma_periodo > 0:
        neg = df["valor"] < 0
        df.loc[neg, "pct_merma_periodo"] = (
            -df.loc[neg, "valor"] / merma_periodo * 100
        ).round(2)

    df["abs_valor"] = df["valor"].abs()
    df = (df.sort_values("abs_valor", ascending=False)
          .head(top_n)
          .drop(columns=["abs_valor"])
          .reset_index(drop=True))
    df.attrs["merma_periodo"] = merma_periodo
    return df


# ---------------------------------------------------------------------------
# 3. Ajustes por usuario
# ---------------------------------------------------------------------------

def ajustes_por(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    agrupar_por: str = "usuario",
    codigodepo: str | list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Merma generada por usuario o por motivo de ajuste, × sucursal (movimientos
    de categorías es_merma). Faltantes y sobrantes valorizados por separado:
    mucho volumen en ambos sentidos también es una señal (correcciones
    cruzadas).

    Args:
        agrupar_por: "usuario" (quién hizo el movimiento — control de errores
                     de operación / fraude) o "tipo_aj" (motivo del ajuste que
                     informa el ERP: INVENTARIO, ANULACION, RECARGA...).
        codigodepo:  sucursal, lista de sucursales, o None = todas.

    Returns:
        DataFrame: la columna de agrupación (nombrada "usuario" o "tipo_aj"
        según `agrupar_por`), codigodepo, sucursal, movimientos, skus,
        faltante_valorizado, sobrante_valorizado, neto_valorizado, pct_faltante.
    """
    if agrupar_por not in ("usuario", "tipo_aj"):
        raise ValueError(f"agrupar_por debe ser 'usuario' o 'tipo_aj', no '{agrupar_por}'")
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    conn = conectar(proyecto)
    try:
        depos = None
        if codigodepo:
            depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
        filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if depos else ""
        params = {
            "desde": str(pd.to_datetime(fecha_desde).date()),
            "hasta": str(pd.to_datetime(fecha_hasta).date()),
        }
        if depos:
            params["depos"] = depos

        sin_dato = "(sin usuario)" if agrupar_por == "usuario" else "(sin motivo)"
        df = conn.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(m.{agrupar_por}), ''), '{sin_dato}') AS {agrupar_por},
                   m.codigodepo, d.nombre AS sucursal, t.categoria,
                   m.codigo, m.diferencia::DOUBLE AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_merma
            LEFT JOIN depositos d  ON m.codigodepo = d.codigodepo
            WHERE m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              {filtro_depo}
        """, params).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=[agrupar_por, "codigodepo", "sucursal"])

    # Sólo ve movimientos de categorías es_merma (nunca de venta): en modo
    # "mixto" el precio resuelve siempre a costo (es_venta=None).
    unit = precio_unitario_fila(df["codigo"], modo_valorizacion, df_val)
    df["valor"] = (df["unidades"] * unit.astype(float)).fillna(0.0)
    df["faltante"] = (-df["valor"]).clip(lower=0)
    df["sobrante"] = df["valor"].clip(lower=0)

    res = (df.groupby([agrupar_por, "codigodepo", "sucursal"], dropna=False)
           .agg(
               movimientos=("codigo", "size"),
               skus=("codigo", "nunique"),
               faltante_valorizado=("faltante", "sum"),
               sobrante_valorizado=("sobrante", "sum"),
               neto_valorizado=("valor", "sum"),
           )
           .reset_index()
           .sort_values("faltante_valorizado", ascending=False)
           .reset_index(drop=True))

    total = res["faltante_valorizado"].sum()
    res["pct_faltante"] = pd.NA
    if total > 0:
        res["pct_faltante"] = (res["faltante_valorizado"] / total * 100).round(2)
    return res
