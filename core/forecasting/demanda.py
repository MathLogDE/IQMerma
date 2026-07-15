"""
core/forecast.py — Proyección de ventas (estacionalidad + tendencia)

Forecast mensual de unidades vendidas a nivel agregado (total, gran super
rubro, rubro o sucursal). A nivel SKU la demanda de esta base es errática
(clase Z) y un forecast puntual sería ruido — la proyección se hace donde
hay señal, y el valor $ se deriva valorizando las unidades a precio actual.

Método (transparente y robusto, sin dependencias nuevas):
  1. Serie mensual de unidades por grupo.
  2. Winsorización por MAD: los meses atípicos (>3.5·MAD de la mediana) se
     recortan para el AJUSTE — no dominan el modelo — pero se reportan en
     attrs["meses_atipicos"] para investigarlos (suelen ser lotes FB
     mayoristas o anulaciones masivas).
  3. Índices estacionales multiplicativos por mes calendario.
  4. Tendencia robusta (Theil-Sen: mediana de pendientes) sobre la serie
     desestacionalizada, AMORTIGUADA en la proyección (φ=0.8) para que una
     caída/suba reciente no se extrapole al infinito.
  5. Proyección = tendencia amortiguada × índice estacional, con banda de
     ±1.96·σ de los residuos del ajuste (aprox. 95%).
  6. Backtest: se re-ajusta el modelo sin los últimos K meses y se mide el
     MAPE contra los reales → medida honesta del error esperado.

Requiere al menos 12 meses de historia por grupo (ideal 24 para capturar
estacionalidad).

Uso:
    from core.forecasting.demanda import forecast_ventas

    df = forecast_ventas("cliente_x", nivel="gran_super_rubro",
                         horizonte=6, backtest=3)
"""

import numpy as np
import pandas as pd

from core.comun import conectar as _get_connection

NIVELES = ("total", "gran_super_rubro", "rubro", "sucursal")
METRICAS = ("ventas", "transferencias")
MIN_MESES = 12


# ---------------------------------------------------------------------------
# Serie mensual
# ---------------------------------------------------------------------------

def _serie_mensual(conn, codigodepo: str | None, nivel: str,
                   metrica: str = "ventas") -> pd.DataFrame:
    """
    Unidades por mes × grupo, según métrica:
      - "ventas":          salida neta de categorías es_venta, valorizada a
                           precio de lista actual.
      - "transferencias":  unidades RECIBIDAS (categoría Remitido con
                           diferencia > 0 — solo el lado entrante, para no
                           netear CD contra sucursal), valorizadas a costo.
    """
    filtro_depo = "AND m.codigodepo = $depo" if codigodepo else ""
    params = {"depo": codigodepo} if codigodepo else []

    if metrica == "ventas":
        sql = f"""
            SELECT date_trunc('month', m.fecha) AS mes,
                   m.codigo, m.codigodepo,
                   -SUM(m.diferencia) AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
            WHERE 1=1 {filtro_depo}
            GROUP BY 1, 2, 3
        """
        precio_col = "lista_1"
    else:
        sql = f"""
            SELECT date_trunc('month', m.fecha) AS mes,
                   m.codigo, m.codigodepo,
                   SUM(m.diferencia) AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.categoria = 'Remitido'
            WHERE m.diferencia > 0 {filtro_depo}
            GROUP BY 1, 2, 3
        """
        precio_col = "costo"

    df = conn.execute(sql, params).df()

    df_val = conn.execute(f"""
        SELECT codigo, {precio_col} AS precio FROM stock_sucursal
        QUALIFY row_number() OVER (
            PARTITION BY codigo
            ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
        ) = 1
    """).df()

    if df.empty:
        return pd.DataFrame(columns=["mes", "grupo", "unidades", "valor"])

    val = df_val.set_index("codigo")["precio"].astype(float)
    df["valor"] = (df["unidades"] * df["codigo"].map(val)).fillna(0.0)

    # grupo según nivel
    if nivel == "total":
        df["grupo"] = "Total"
    elif nivel == "sucursal":
        deps = conn.execute("SELECT codigodepo, nombre FROM depositos").df()
        nombres = deps.set_index("codigodepo")["nombre"]
        df["grupo"] = df["codigodepo"] + " — " + df["codigodepo"].map(nombres).fillna("")
    else:
        arts = conn.execute("SELECT codigo, rubro FROM articulos").df()
        est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()
        from core.comun import mapa_estructura as _mapa_estructura
        mapa = _mapa_estructura(est).set_index("_clave")
        rubro_art = arts.set_index("codigo")["rubro"].astype("string").str.strip()
        clave = df["codigo"].map(rubro_art)
        col = "rubro_desc" if nivel == "rubro" else "gran_super_rubro"
        df["grupo"] = clave.map(mapa[col]).fillna("Sin clasificar")

    out = (df.groupby(["mes", "grupo"], dropna=False)
           .agg(unidades=("unidades", "sum"), valor=("valor", "sum"))
           .reset_index())
    out["mes"] = pd.to_datetime(out["mes"])
    return out


# ---------------------------------------------------------------------------
# Modelo: estacionalidad multiplicativa + tendencia lineal
# ---------------------------------------------------------------------------

def _indices_estacionales(serie: pd.Series) -> pd.Series:
    """Índice por mes calendario (1-12), normalizado a media 1."""
    df = pd.DataFrame({"y": serie})
    df["anio"] = serie.index.year
    df["mes_cal"] = serie.index.month
    media_anio = df.groupby("anio")["y"].transform("mean")
    df["ratio"] = np.where(media_anio > 0, df["y"] / media_anio, np.nan)
    idx = df.groupby("mes_cal")["ratio"].mean()
    idx = idx.reindex(range(1, 13)).fillna(1.0)
    media = idx.mean()
    return idx / media if media > 0 else idx * 0 + 1.0


def _winsorizar(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Recorta outliers a mediana ± 3.5·MAD (escala normal) y piso en 0.
    Returns (serie recortada, máscara de meses atípicos).
    """
    med = float(np.median(y))
    mad = float(np.median(np.abs(y - med))) * 1.4826
    if mad <= 0:
        y_fit = np.clip(y, 0, None)
        return y_fit, np.zeros(len(y), dtype=bool)
    lo, hi = max(med - 3.5 * mad, 0.0), med + 3.5 * mad
    atipico = (y < lo) | (y > hi)
    return np.clip(y, lo, hi), atipico


def _theil_sen(t: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Tendencia robusta: mediana de las pendientes entre pares de puntos."""
    n = len(t)
    pendientes = [
        (y[j] - y[i]) / (t[j] - t[i])
        for i in range(n) for j in range(i + 1, n) if t[j] != t[i]
    ]
    b = float(np.median(pendientes)) if pendientes else 0.0
    a = float(np.median(y - b * t))
    return a, b


def _ajustar_y_proyectar(serie: pd.Series, horizonte: int, phi: float = 0.8):
    """
    Ajusta estacionalidad + tendencia robusta amortiguada y proyecta.
    Returns (fitted, forecast, sigma_residuos, mask_atipicos).
    """
    y_fit, atipicos = _winsorizar(serie.values.astype(float))
    serie_fit = pd.Series(y_fit, index=serie.index)

    idx_est = _indices_estacionales(serie_fit)
    est_hist = serie.index.month.map(idx_est).values
    est_hist = np.where(est_hist > 0, est_hist, 1.0)

    deses = y_fit / est_hist
    t = np.arange(len(serie), dtype=float)
    a, b = _theil_sen(t, deses)

    fitted = np.clip((a + b * t) * est_hist, 0, None)
    resid = y_fit - fitted
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 2 else 0.0

    # proyección con tendencia amortiguada: el aporte de la pendiente decae
    # φ^i por mes — una tendencia reciente no se extrapola linealmente.
    meses_fut = pd.date_range(
        serie.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    est_fut = meses_fut.month.map(idx_est).values
    t_ultimo = t[-1]
    nivel_base = a + b * t_ultimo
    acum_phi = np.cumsum(phi ** np.arange(1, horizonte + 1))
    forecast = np.clip((nivel_base + b * acum_phi) * est_fut, 0, None)

    return (pd.Series(fitted, index=serie.index),
            pd.Series(forecast, index=meses_fut),
            sigma, atipicos)


def _mape(reales: np.ndarray, previstos: np.ndarray) -> float | None:
    mask = reales > 0
    if not mask.any():
        return None
    return float(np.mean(np.abs(reales[mask] - previstos[mask]) / reales[mask]) * 100)


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def serie_mensual_real(
    proyecto: str,
    codigodepo: str | None = None,
    nivel: str = "total",
    metrica: str = "ventas",
) -> pd.DataFrame:
    """Serie mensual real (sin proyección) — para superponer métricas en la UI."""
    if nivel not in NIVELES:
        raise ValueError(f"nivel debe ser uno de {NIVELES}, no '{nivel}'")
    if metrica not in METRICAS:
        raise ValueError(f"metrica debe ser una de {METRICAS}, no '{metrica}'")
    conn = _get_connection(proyecto)
    try:
        base = _serie_mensual(conn, codigodepo, nivel, metrica)
    finally:
        conn.close()
    if not base.empty:
        base["mes"] = pd.to_datetime(base["mes"]).dt.strftime("%Y-%m")
    return base


def forecast_ventas(
    proyecto: str,
    codigodepo: str | None = None,
    nivel: str = "total",
    horizonte: int = 6,
    backtest: int = 3,
    metrica: str = "ventas",
) -> pd.DataFrame:
    """
    Proyección mensual de unidades por grupo.

    Args:
        proyecto:   Nombre del proyecto
        codigodepo: Sucursal (None = todas juntas; usar nivel="sucursal"
                    para desagregar por sucursal)
        nivel:      "total", "gran_super_rubro", "rubro" o "sucursal"
        horizonte:  Meses a proyectar
        backtest:   Meses finales reservados para medir el error (MAPE).
                    0 = sin backtest.
        metrica:    "ventas" (salida es_venta, a precio lista) o
                    "transferencias" (Remitido recibido, a costo) — proyectar
                    transferencias sirve para planificar el abastecimiento
                    del CD, y compararlas contra ventas muestra si una caída
                    de venta es de demanda o de abastecimiento.

    Returns:
        DataFrame largo: grupo, mes, tipo ('real'/'forecast'), unidades,
        banda_inf, banda_sup, valor (unidades × precio promedio del grupo).
        attrs: "mape" (dict por grupo), "grupos_excluidos" (historia corta),
        "meses_atipicos", "metrica".
    """
    if nivel not in NIVELES:
        raise ValueError(f"nivel debe ser uno de {NIVELES}, no '{nivel}'")
    if metrica not in METRICAS:
        raise ValueError(f"metrica debe ser una de {METRICAS}, no '{metrica}'")
    if horizonte < 1:
        raise ValueError("horizonte debe ser >= 1")

    conn = _get_connection(proyecto)
    try:
        base = _serie_mensual(conn, codigodepo, nivel, metrica)
    finally:
        conn.close()

    if base.empty:
        vacio = pd.DataFrame(columns=["grupo", "mes", "tipo", "unidades"])
        vacio.attrs["mape"] = {}
        vacio.attrs["grupos_excluidos"] = []
        return vacio

    # El último mes suele estar incompleto (carga parcial) → se descarta del
    # ajuste si su venta es < 50% de la mediana de los 6 meses previos.
    ultimo = base["mes"].max()
    tot_ult = base.loc[base["mes"] == ultimo, "unidades"].sum()
    previos = (base[base["mes"] < ultimo].groupby("mes")["unidades"].sum().tail(6))
    if len(previos) >= 3 and tot_ult < 0.5 * previos.median():
        base = base[base["mes"] < ultimo]

    filas, mapes, excluidos, atipicos_all = [], {}, [], {}
    for grupo, g in base.groupby("grupo", dropna=False):
        serie = (g.set_index("mes")["unidades"]
                 .resample("MS").sum().fillna(0.0))
        precio_medio = (g["valor"].sum() / g["unidades"].sum()
                        if g["unidades"].sum() > 0 else 0.0)

        if len(serie) < MIN_MESES:
            excluidos.append(str(grupo))
            continue

        # backtest
        if backtest and len(serie) >= MIN_MESES + backtest:
            _, fc_bt, _, _ = _ajustar_y_proyectar(serie.iloc[:-backtest], backtest)
            mapes[str(grupo)] = _mape(serie.iloc[-backtest:].values, fc_bt.values)

        fitted, fc, sigma, atipicos = _ajustar_y_proyectar(serie, horizonte)
        meses_at = [m.strftime("%Y-%m") for m in serie.index[atipicos]]
        if meses_at:
            atipicos_all[str(grupo)] = meses_at

        for mes, y in serie.items():
            filas.append({"grupo": grupo, "mes": mes, "tipo": "real",
                          "unidades": float(y),
                          "banda_inf": None, "banda_sup": None,
                          "valor": float(y) * precio_medio})
        for mes, y in fc.items():
            filas.append({"grupo": grupo, "mes": mes, "tipo": "forecast",
                          "unidades": float(y),
                          "banda_inf": max(float(y) - 1.96 * sigma, 0.0),
                          "banda_sup": float(y) + 1.96 * sigma,
                          "valor": float(y) * precio_medio})

    res = pd.DataFrame(filas)
    if not res.empty:
        res["mes"] = pd.to_datetime(res["mes"]).dt.strftime("%Y-%m")
        res = res.sort_values(["grupo", "mes"]).reset_index(drop=True)
    res.attrs["mape"] = mapes
    res.attrs["grupos_excluidos"] = excluidos
    res.attrs["meses_atipicos"] = atipicos_all
    res.attrs["metrica"] = metrica
    return res
