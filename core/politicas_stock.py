"""
core/politicas_stock.py — Políticas de inventario: Mín / Ópt / Máx por SKU × sucursal

Modelo de revisión periódica con parámetros estimados de los propios datos:

  d        demanda diaria promedio (ventana de demanda, semanas con cero incluidas)
  σd       desvío de la demanda diaria (derivado del desvío semanal / √7)
  T        ciclo de reposición: mediana de días entre llegadas (movimientos de
           categoría 'Remitido' con diferencia > 0). Fallbacks en cascada:
           SKU×sucursal → SKU (todas las sucursales) → rubro → default.
  L        lead time (parámetro): días desde que se dispara el pedido hasta
           que llega. Default 7.
  z        nivel de servicio por clase ABC (A 95% → 1.65, B 90% → 1.28,
           C 80% → 0.84). La clase se calcula dinámicamente por participación
           en la venta valorizada (80/95) por sucursal.

Fórmulas (revisión periódica, order-up-to):
  stock_seguridad = z × σd × √(L + T)
  minimo          = d × L + stock_seguridad          (punto de pedido)
  optimo          = d × (L + T) + stock_seguridad    (nivel objetivo al reponer)
  maximo          = optimo + stock_seguridad         (tope de tolerancia)

Clasificación XYZ (regularidad de la demanda, CV semanal):
  X ≤ 0.5 (estable) · Y ≤ 1.0 (variable) · Z > 1.0 (errática/esporádica)

Estados sugeridos:
  reponer      stock <= minimo (con demanda) → compra sugerida hasta el óptimo,
               redondeada a bultos (uxb) si está disponible
  ok           dentro de banda
  exceso       stock > maximo
  sin_demanda  stock > 0 sin ventas en la ventana

Uso:
    from core.politicas_stock import calcular_politicas, resumen_politicas

    df = calcular_politicas("cliente_x", codigodepo=None, dias_demanda=90)
"""

import math

import duckdb
import pandas as pd

from core.merma import _get_connection, _mapa_estructura

Z_POR_CLASE = {"A": 1.65, "B": 1.28, "C": 0.84}
ESTADOS_POLITICA = ("reponer", "ok", "exceso", "sin_demanda")


def calcular_politicas(
    proyecto: str,
    codigodepo: str | None = None,
    fecha_stock: str | None = None,
    dias_demanda: int = 90,
    dias_reposicion: int = 365,
    lead_time_dias: float = 7,
    ciclo_default: float = 30,
    z_por_clase: dict | None = None,
    incluir_logistica: bool = False,
) -> pd.DataFrame:
    """
    Calcula las políticas Mín/Ópt/Máx por SKU × sucursal.

    Returns:
        DataFrame por (codigo, codigodepo) con demanda, clase ABC/XYZ, ciclo,
        bandas, estado y compra sugerida. attrs: fecha_stock, dias_demanda,
        lead_time_dias.
    """
    if dias_demanda < 28:
        raise ValueError("dias_demanda debe ser al menos 28 (4 semanas)")
    z_por_clase = z_por_clase or Z_POR_CLASE

    conn = _get_connection(proyecto)
    try:
        # --- snapshot -----------------------------------------------------
        if fecha_stock:
            fs = conn.execute(
                "SELECT MAX(fecha_snapshot) FROM stock_sucursal WHERE fecha_snapshot <= ?",
                [str(pd.to_datetime(fecha_stock).date())],
            ).fetchone()[0]
        else:
            fs = conn.execute("SELECT MAX(fecha_snapshot) FROM stock_sucursal").fetchone()[0]
        if fs is None:
            raise ValueError("No hay snapshots de stock cargados.")
        fs = str(fs)

        filtro_depo = "AND m.codigodepo = $depo" if codigodepo else ""
        params = {"fs": fs, "dias": dias_demanda}
        if codigodepo:
            params["depo"] = codigodepo

        # --- demanda semanal (suma y suma de cuadrados para el desvío) -----
        df_dem = conn.execute(f"""
            WITH sem AS (
                SELECT m.codigo, m.codigodepo,
                       date_trunc('week', m.fecha) AS semana,
                       -SUM(m.diferencia) AS u
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
                WHERE m.fecha >  $fs::DATE - INTERVAL ($dias) DAY
                  AND m.fecha <= $fs::DATE
                  {filtro_depo}
                GROUP BY 1, 2, 3
            )
            SELECT codigo, codigodepo,
                   SUM(u)::DOUBLE     AS total_unidades,
                   SUM(u * u)::DOUBLE AS suma_cuadrados
            FROM sem
            GROUP BY 1, 2
        """, params).df()

        # --- llegadas de reposición (categoría Remitido, entrante) ----------
        params_rep = {"fs": fs, "dias": dias_reposicion}
        if codigodepo:
            params_rep["depo"] = codigodepo
        df_rep = conn.execute(f"""
            WITH llegadas AS (
                SELECT m.codigo, m.codigodepo, m.fecha
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo AND t.categoria = 'Remitido'
                WHERE m.diferencia > 0
                  AND m.fecha >  $fs::DATE - INTERVAL ($dias) DAY
                  AND m.fecha <= $fs::DATE
                  {filtro_depo}
                GROUP BY 1, 2, 3
            ),
            gaps AS (
                SELECT codigo, codigodepo,
                       date_diff('day',
                           lag(fecha) OVER (PARTITION BY codigo, codigodepo ORDER BY fecha),
                           fecha) AS gap
                FROM llegadas
            )
            SELECT codigo, codigodepo,
                   median(gap)                          AS ciclo_dias,
                   COUNT(gap) + 1                       AS llegadas
            FROM gaps
            GROUP BY 1, 2
        """, params_rep).df()

        # --- stock actual + precios + uxb -----------------------------------
        filtro_depo_s = "AND codigodepo = $depo" if codigodepo else ""
        params_stock = {"fs": fs}
        if codigodepo:
            params_stock["depo"] = codigodepo
        df_stock = conn.execute(f"""
            SELECT codigo, codigodepo, stock, costo, lista_1, uxb
            FROM stock_sucursal
            WHERE fecha_snapshot = $fs::DATE {filtro_depo_s}
        """, params_stock).df()

        df_dep = conn.execute(
            "SELECT codigodepo, nombre, es_logistica FROM depositos").df()
        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    # --- base: stock ∪ demanda -----------------------------------------------
    df = df_stock.merge(df_dem, on=["codigo", "codigodepo"], how="outer")
    for c in ("stock", "total_unidades", "suma_cuadrados"):
        df[c] = df[c].fillna(0.0).astype(float)
    for c in ("costo", "lista_1", "uxb"):
        df[c] = df[c].astype(float)

    df = df.merge(df_dep, on="codigodepo", how="left")
    if not incluir_logistica:
        df = df[~df["es_logistica"].fillna(False)]
    df = df[(df["stock"] > 0) | (df["total_unidades"] > 0)].copy()

    # --- demanda diaria y desvío ------------------------------------------------
    n_sem = max(dias_demanda // 7, 2)
    media_sem = df["total_unidades"] / n_sem
    var_sem = ((df["suma_cuadrados"] - n_sem * media_sem**2) / (n_sem - 1)).clip(lower=0)
    df["demanda_diaria"] = (df["total_unidades"] / dias_demanda).clip(lower=0)
    df["sigma_diaria"] = (var_sem ** 0.5) / math.sqrt(7)

    # --- clase ABC (participación en venta valorizada, por sucursal) --------------
    df["venta_valorizada"] = (df["total_unidades"].clip(lower=0) * df["lista_1"]).fillna(0.0)
    df["clase_abc"] = "C"
    for depo, grupo in df.groupby("codigodepo"):
        total = grupo["venta_valorizada"].sum()
        if total <= 0:
            continue
        orden = grupo.sort_values("venta_valorizada", ascending=False)
        acum = orden["venta_valorizada"].cumsum() / total
        df.loc[acum[acum <= 0.80].index, "clase_abc"] = "A"
        df.loc[acum[(acum > 0.80) & (acum <= 0.95)].index, "clase_abc"] = "B"

    # --- clase XYZ (CV de la demanda semanal) --------------------------------------
    cv = pd.Series(pd.NA, index=df.index, dtype="Float64")
    con_dem = media_sem > 0
    cv[con_dem] = (var_sem[con_dem] ** 0.5) / media_sem[con_dem]
    df["clase_xyz"] = "Z"
    df.loc[cv <= 1.0, "clase_xyz"] = "Y"
    df.loc[cv <= 0.5, "clase_xyz"] = "X"

    # --- ciclo de reposición con fallbacks -------------------------------------------
    df = df.merge(df_rep, on=["codigo", "codigodepo"], how="left")
    ciclo_sku = df_rep.groupby("codigo")["ciclo_dias"].median()
    df["ciclo_origen"] = "sku_sucursal"
    falta = df["ciclo_dias"].isna()
    df.loc[falta, "ciclo_dias"] = df.loc[falta, "codigo"].map(ciclo_sku)
    df.loc[falta & df["ciclo_dias"].notna(), "ciclo_origen"] = "sku_global"

    # atributos antes del fallback por rubro
    df = df.merge(df_art, on="codigo", how="left")
    mapa = _mapa_estructura(df_est)
    df["_clave"] = df["rubro"].astype("string").str.strip()
    df = df.merge(mapa, on="_clave", how="left").drop(columns=["_clave"])
    df["rubro"] = df["rubro_desc"].fillna(df["rubro"])
    df = df.drop(columns=["rubro_desc"])

    ciclo_rubro = df.groupby("rubro")["ciclo_dias"].median()
    falta = df["ciclo_dias"].isna()
    df.loc[falta, "ciclo_dias"] = df.loc[falta, "rubro"].map(ciclo_rubro)
    df.loc[falta & df["ciclo_dias"].notna(), "ciclo_origen"] = "rubro"

    falta = df["ciclo_dias"].isna()
    df.loc[falta, "ciclo_dias"] = ciclo_default
    df.loc[falta, "ciclo_origen"] = "default"
    df["ciclo_dias"] = df["ciclo_dias"].astype(float).clip(lower=1)

    # --- bandas ---------------------------------------------------------------------
    z = df["clase_abc"].map(z_por_clase).fillna(min(z_por_clase.values()))
    proteccion = (lead_time_dias + df["ciclo_dias"]) ** 0.5
    df["stock_seguridad"] = (z * df["sigma_diaria"] * proteccion).round(1)
    df["minimo"] = (df["demanda_diaria"] * lead_time_dias + df["stock_seguridad"]).round(1)
    df["optimo"] = (df["demanda_diaria"] * (lead_time_dias + df["ciclo_dias"])
                    + df["stock_seguridad"]).round(1)
    df["maximo"] = (df["optimo"] + df["stock_seguridad"]).round(1)

    # --- estado y compra sugerida -------------------------------------------------------
    df["estado"] = "ok"
    df.loc[df["stock"] > df["maximo"], "estado"] = "exceso"
    df.loc[(df["stock"] <= df["minimo"]) & (df["demanda_diaria"] > 0), "estado"] = "reponer"
    df.loc[(df["demanda_diaria"] <= 0) & (df["stock"] > 0), "estado"] = "sin_demanda"

    df["compra_sugerida"] = 0.0
    rep = df["estado"] == "reponer"
    bruta = (df.loc[rep, "optimo"] - df.loc[rep, "stock"]).clip(lower=0)
    # Redondear a bultos (uxb) solo si el bulto cabe en el óptimo — si el
    # bulto excede el objetivo, se sugiere la cantidad exacta (y conviene
    # revisar si ese SKU debe stockearse en esa sucursal).
    uxb = df.loc[rep, "uxb"].fillna(1).clip(lower=1)
    uxb = uxb.where(uxb <= df.loc[rep, "optimo"], 1)
    df.loc[rep, "compra_sugerida"] = (bruta / uxb).apply(math.ceil) * uxb
    df["compra_valorizada"] = (df["compra_sugerida"] * df["costo"]).fillna(0.0)

    df["exceso_unidades"] = 0.0
    exc = df["estado"] == "exceso"
    df.loc[exc, "exceso_unidades"] = df.loc[exc, "stock"] - df.loc[exc, "maximo"]
    df["exceso_valorizado"] = (df["exceso_unidades"] * df["costo"]).fillna(0.0)

    cols = [
        "codigo", "descripcion", "rubro", "marca", "super_rubro", "gran_super_rubro",
        "codigodepo", "nombre", "clase_abc", "clase_xyz",
        "stock", "demanda_diaria", "sigma_diaria",
        "ciclo_dias", "ciclo_origen", "llegadas",
        "stock_seguridad", "minimo", "optimo", "maximo",
        "estado", "compra_sugerida", "compra_valorizada",
        "exceso_unidades", "exceso_valorizado",
        "costo", "lista_1", "uxb",
    ]
    df = df[cols].rename(columns={"nombre": "sucursal"})

    orden = {e: i for i, e in enumerate(ESTADOS_POLITICA)}
    df["_o"] = df["estado"].map(orden)
    df = (df.sort_values(["_o", "compra_valorizada", "exceso_valorizado"],
                         ascending=[True, False, False])
          .drop(columns=["_o"]).reset_index(drop=True))

    df.attrs["fecha_stock"] = fs
    df.attrs["dias_demanda"] = dias_demanda
    df.attrs["lead_time_dias"] = lead_time_dias
    return df


def resumen_politicas(df: pd.DataFrame) -> dict:
    """KPIs agregados de las políticas de stock."""
    por_estado = df["estado"].value_counts().to_dict()
    return {
        "pares":             len(df),
        "por_estado":        {e: int(por_estado.get(e, 0)) for e in ESTADOS_POLITICA},
        "compra_sugerida":   float(df["compra_valorizada"].sum()),
        "exceso_valorizado": float(df["exceso_valorizado"].sum()),
        "ciclo_mediano":     (float(df["ciclo_dias"].median())
                              if df["ciclo_dias"].notna().any() else None),
        "matriz_abc_xyz":    df.groupby(["clase_abc", "clase_xyz"]).size().to_dict(),
    }
