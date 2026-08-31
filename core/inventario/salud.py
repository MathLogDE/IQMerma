"""
core/salud_stock.py — Salud de inventario por SKU × sucursal

Cruza el stock (snapshot) con la demanda reciente (movimientos de venta) para
clasificar cada SKU × sucursal en un estado accionable:

    quiebre     stock <= 0 con demanda en la ventana  → venta que se pierde
    critico     cobertura < cobertura_min días        → riesgo de quiebre
    ok          cobertura dentro de la banda
    sobrestock  cobertura > cobertura_max días        → capital inmovilizado
    muerto      stock > 0 sin ventas en la ventana    → liquidar / redistribuir

Definiciones:
  - Demanda: unidades netas vendidas (categorías es_venta de tipos_categoria)
    en la ventana [fecha_stock - dias_ventana, fecha_stock].
  - venta_diaria = demanda / dias_ventana (clipped a >= 0).
  - cobertura_dias = stock / venta_diaria (None si no hay demanda).
  - stock_valorizado = stock × costo (capital inmovilizado a costo).
  - venta_perdida_diaria = venta_diaria × lista_1, solo para quiebres
    (facturación estimada que se pierde por día sin stock).

Los pares (SKU, sucursal) sin stock ni ventas en la ventana se excluyen.

Uso:
    from core.inventario.salud import analizar_stock

    df = analizar_stock(
        proyecto="cliente_x",
        codigodepo=None,          # None = todas las sucursales
        fecha_stock=None,         # None = último snapshot
        dias_ventana=90,
        cobertura_min=7,
        cobertura_max=60,
        incluir_logistica=False,  # excluye depósitos es_logistica
    )
"""

import pandas as pd

from core.comun import conectar, adjuntar_rubros

ESTADOS = ("quiebre", "critico", "ok", "sobrestock", "muerto")


def analizar_stock(
    proyecto: str,
    codigodepo: str | None = None,
    fecha_stock: str | None = None,
    dias_ventana: int = 90,
    cobertura_min: float = 7,
    cobertura_max: float = 60,
    incluir_logistica: bool = False,
) -> pd.DataFrame:
    """
    Analiza la salud del stock por SKU × sucursal.

    Returns:
        DataFrame con una fila por (codigo, codigodepo): stock, demanda,
        cobertura, estado, valorizaciones y atributos del SKU.
        attrs: "fecha_stock" (snapshot usado), "dias_ventana".
    """
    if dias_ventana <= 0:
        raise ValueError("dias_ventana debe ser positivo")
    if cobertura_min >= cobertura_max:
        raise ValueError("cobertura_min debe ser menor que cobertura_max")

    conn = conectar(proyecto)
    try:
        # --- snapshot de stock a usar ---------------------------------------
        if fecha_stock:
            fs = str(pd.to_datetime(fecha_stock).date())
            fila = conn.execute(
                "SELECT MAX(fecha_snapshot) FROM stock_sucursal WHERE fecha_snapshot <= ?",
                [fs],
            ).fetchone()
        else:
            fila = conn.execute("SELECT MAX(fecha_snapshot) FROM stock_sucursal").fetchone()
        fecha_snap = fila[0]
        if fecha_snap is None:
            raise ValueError("No hay snapshots de stock cargados.")
        fecha_snap = str(fecha_snap)

        filtro_depo = "AND codigodepo = $depo" if codigodepo else ""
        params_stock = {"fs": fecha_snap}
        if codigodepo:
            params_stock["depo"] = codigodepo

        df_stock = conn.execute(f"""
            SELECT codigo, codigodepo, stock, costo, lista_1
            FROM stock_sucursal
            WHERE fecha_snapshot = $fs::DATE
              {filtro_depo}
        """, params_stock).df()

        # --- demanda: ventas (es_venta) en la ventana ------------------------
        params_dem = {"fs": fecha_snap, "dias": dias_ventana}
        if codigodepo:
            params_dem["depo"] = codigodepo

        df_dem = conn.execute(f"""
            SELECT m.codigo, m.codigodepo,
                   -SUM(m.diferencia) AS unidades_vendidas
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            WHERE t.es_venta
              AND m.fecha >  $fs::DATE - INTERVAL ($dias) DAY
              AND m.fecha <= $fs::DATE
              {("AND m.codigodepo = $depo" if codigodepo else "")}
            GROUP BY 1, 2
        """, params_dem).df()

        # --- referencias -------------------------------------------------------
        df_dep = conn.execute(
            "SELECT codigodepo, nombre, es_logistica FROM depositos"
        ).df()
        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos"
        ).df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()
    finally:
        conn.close()

    # --- join stock × demanda (outer: quiebres sin fila de stock también) ----
    df = df_stock.merge(df_dem, on=["codigo", "codigodepo"], how="outer")
    df["stock"] = df["stock"].fillna(0.0).astype(float)
    df["unidades_vendidas"] = df["unidades_vendidas"].fillna(0.0).astype(float)

    # excluir depósitos logísticos (los CD no venden: no aplica quiebre/muerto)
    df = df.merge(df_dep, on="codigodepo", how="left")
    if not incluir_logistica:
        df = df[~df["es_logistica"].fillna(False)]

    # descartar pares sin stock ni ventas
    df = df[(df["stock"] > 0) | (df["unidades_vendidas"] > 0)].copy()

    # --- métricas --------------------------------------------------------------
    df["venta_diaria"] = (df["unidades_vendidas"] / dias_ventana).clip(lower=0)
    df["cobertura_dias"] = pd.NA
    con_dem = df["venta_diaria"] > 0
    df.loc[con_dem, "cobertura_dias"] = (
        df.loc[con_dem, "stock"] / df.loc[con_dem, "venta_diaria"]
    ).round(1)

    df["stock_valorizado"] = (df["stock"] * df["costo"].astype(float)).fillna(0.0)

    # --- estado ---------------------------------------------------------------
    df["estado"] = "ok"
    cob = df["cobertura_dias"].astype("Float64")
    df.loc[cob > cobertura_max, "estado"] = "sobrestock"
    df.loc[cob < cobertura_min, "estado"] = "critico"
    df.loc[(df["stock"] > 0) & (df["venta_diaria"] <= 0), "estado"] = "muerto"
    df.loc[(df["stock"] <= 0) & (df["venta_diaria"] > 0), "estado"] = "quiebre"

    # venta perdida estimada (solo quiebres): facturación diaria a precio lista
    df["venta_perdida_diaria"] = 0.0
    q = df["estado"] == "quiebre"
    df.loc[q, "venta_perdida_diaria"] = (
        df.loc[q, "venta_diaria"] * df.loc[q, "lista_1"].astype(float)
    ).fillna(0.0)

    # --- atributos del SKU -------------------------------------------------------
    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    cols = [
        "codigo", "descripcion", "rubro", "marca", "super_rubro", "gran_super_rubro",
        "codigodepo", "nombre", "estado",
        "stock", "unidades_vendidas", "venta_diaria", "cobertura_dias",
        "stock_valorizado", "venta_perdida_diaria",
        "costo", "lista_1",
    ]
    df = df[cols].rename(columns={"nombre": "sucursal"})

    orden = {e: i for i, e in enumerate(ESTADOS)}
    df["_o"] = df["estado"].map(orden)
    df = (df.sort_values(["_o", "venta_perdida_diaria", "stock_valorizado"],
                         ascending=[True, False, False])
          .drop(columns=["_o"])
          .reset_index(drop=True))

    df.attrs["fecha_stock"] = fecha_snap
    df.attrs["dias_ventana"] = dias_ventana
    return df


def resumen_salud(df: pd.DataFrame) -> dict:
    """KPIs agregados del análisis de salud de stock."""
    por_estado = df["estado"].value_counts().to_dict()
    return {
        "pares":               len(df),
        "por_estado":          {e: int(por_estado.get(e, 0)) for e in ESTADOS},
        "venta_perdida_diaria": float(df["venta_perdida_diaria"].sum()),
        "stock_muerto_valor":  float(df.loc[df["estado"] == "muerto", "stock_valorizado"].sum()),
        "sobrestock_valor":    float(df.loc[df["estado"] == "sobrestock", "stock_valorizado"].sum()),
        "stock_valor_total":   float(df["stock_valorizado"].sum()),
        "cobertura_mediana":   (float(df["cobertura_dias"].dropna().median())
                                if df["cobertura_dias"].notna().any() else None),
    }
