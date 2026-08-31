"""
core/forecast.py — Proyección de ventas (estacionalidad + tendencia)

Forecast mensual de unidades vendidas a nivel agregado (total, gran super
rubro, rubro o sucursal). A nivel SKU la demanda de esta base es errática
(clase Z) y un forecast puntual sería ruido — la proyección se hace donde
hay señal, y el valor $ se deriva valorizando las unidades a precio actual.

Método — competencia de modelos por grupo (nunca una caja negra: queda
registrado qué modelo ganó y con qué MAPE de backtest cada candidato, en
attrs["modelo_elegido"] / attrs["mape_por_modelo"]):

  1. Serie mensual de unidades por grupo. Winsorización por MAD: los meses
     atípicos (>3.5·MAD de la mediana) se recortan para el AJUSTE — no
     dominan ningún modelo — pero se reportan en attrs["meses_atipicos"]
     para investigarlos (suelen ser lotes FB mayoristas o anulaciones
     masivas).
  2. Con >= MIN_MESES (12) de historia, se hace backtest (holdout de los
     últimos `backtest` meses) de todos los modelos aplicables y gana el
     de menor MAPE:
       - "estacional_robusto": el modelo original — estacionalidad
         multiplicativa por mes calendario + tendencia robusta (Theil-Sen:
         mediana de pendientes), AMORTIGUADA en la proyección (φ=0.8) para
         que una caída/suba reciente no se extrapole al infinito. Siempre
         aplicable desde 12 meses — es el piso determinístico, nunca falla,
         y es el que se usa si no hay backtest o la historia no alcanza
         para comparar.
       - "holt_winters": Exponential Smoothing (statsmodels) con tendencia
         amortiguada; estacionalidad multiplicativa solo con >= 24 meses
         (2 ciclos) — si no, corre sin estacionalidad.
       - "sarima": SARIMAX estacional (statsmodels), orden
         (1,1,1)x(1,1,1,12). Solo aplica con >= MIN_MESES_SARIMA (30) de
         historia — hoy (2025 + 2026 parcial, ~19-20 meses) casi ningún
         grupo lo dispara todavía; se activa solo, sin flag, cuando hay
         historia real para sostenerlo.
  3. Con entre MIN_MESES_KNN (3) y MIN_MESES-1 meses de historia, el grupo
     no se descarta: se le "presta" el patrón estacional + tendencia
     relativa de los K_VECINOS grupos más parecidos (kNN por similitud
     coseno de índice estacional, scikit-learn) que sí tengan historia
     completa, escalado al nivel propio del grupo corto. Queda etiquetado
     como modelo "kNN (prestado de: ...)" — nunca se mezcla silenciosamente
     con los otros. Con menos de MIN_MESES_KNN meses, se excluye
     (attrs["grupos_excluidos"]).
  4. Banda de proyección: ±1.96·σ de los residuos del modelo elegido
     (aprox. 95%).

Requiere al menos 12 meses de historia por grupo para un modelo de serie
temporal propio (ideal 24+ para estacionalidad, 30+ para SARIMA); grupos
más nuevos usan kNN (mínimo 3 meses).

Uso:
    from core.forecasting.demanda import forecast_ventas

    df = forecast_ventas("cliente_x", nivel="gran_super_rubro",
                         horizonte=6, backtest=3)
"""

import warnings

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

from core.comun import (
    conectar, precios_snapshot, mapa_estructura,
    catalogo_articulos, DEPOSITOS_DISTRIBUCION,
)

NIVELES = ("total", "gran_super_rubro", "rubro", "marca", "sucursal")
METRICAS = ("ventas", "transferencias")
MIN_MESES = 12
MIN_MESES_SARIMA = 30
MIN_MESES_KNN = 3
K_VECINOS = 5


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

    # Siempre precio actual (último snapshot): el forecast valoriza a precio
    # de hoy, no histórico.
    df_val = (precios_snapshot(conn, None)[["codigo", precio_col]]
              .rename(columns={precio_col: "precio"}))

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
    elif nivel == "marca":
        arts = conn.execute("SELECT codigo, marca FROM articulos").df()
        marca_art = arts.set_index("codigo")["marca"].astype("string").str.strip()
        df["grupo"] = df["codigo"].map(marca_art).fillna("Sin clasificar")
    else:
        arts = conn.execute("SELECT codigo, rubro FROM articulos").df()
        est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()
        mapa = mapa_estructura(est).set_index("_clave")
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


def _perfil_estacional_tendencia(serie_fit: pd.Series) -> dict:
    """Índice estacional (12 valores) + tendencia Theil-Sen (a, b) sobre la
    serie ya winsorizada. Se usa tanto para ajustar "estacional_robusto"
    como para describir el "perfil" de un grupo con historia completa que
    puede actuar de donante en el préstamo kNN (ver _modelo_knn_prestado)."""
    idx_est = _indices_estacionales(serie_fit)
    est_hist = serie_fit.index.month.map(idx_est).values
    est_hist = np.where(est_hist > 0, est_hist, 1.0)
    deses = serie_fit.values / est_hist
    t = np.arange(len(serie_fit), dtype=float)
    a, b = _theil_sen(t, deses)
    return {"idx_est": idx_est, "a": a, "b": b}


def _modelo_estacional_robusto(serie_fit: pd.Series, horizonte: int, phi: float = 0.8) -> dict:
    """Modelo original: estacionalidad + tendencia robusta amortiguada."""
    perfil = _perfil_estacional_tendencia(serie_fit)
    idx_est, a, b = perfil["idx_est"], perfil["a"], perfil["b"]
    t = np.arange(len(serie_fit), dtype=float)
    est_hist = serie_fit.index.month.map(idx_est).values
    est_hist = np.where(est_hist > 0, est_hist, 1.0)

    fitted = np.clip((a + b * t) * est_hist, 0, None)
    resid = serie_fit.values - fitted
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 2 else 0.0

    # proyección con tendencia amortiguada: el aporte de la pendiente decae
    # φ^i por mes — una tendencia reciente no se extrapola linealmente.
    meses_fut = pd.date_range(
        serie_fit.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    est_fut = meses_fut.month.map(idx_est).values
    nivel_base = a + b * t[-1]
    acum_phi = np.cumsum(phi ** np.arange(1, horizonte + 1))
    forecast = np.clip((nivel_base + b * acum_phi) * est_fut, 0, None)

    return {"fitted": pd.Series(fitted, index=serie_fit.index),
            "forecast": pd.Series(forecast, index=meses_fut),
            "sigma": sigma}


def _modelo_holt_winters(serie_fit: pd.Series, horizonte: int) -> dict | None:
    """Exponential Smoothing con tendencia amortiguada; estacionalidad
    multiplicativa solo si hay >= 2 ciclos y no hay ceros (statsmodels no
    admite estacionalidad multiplicativa con valores <= 0)."""
    n = len(serie_fit)
    tipo_est = None
    if n >= 24:
        tipo_est = "mul" if (serie_fit > 0).all() else "add"
    modelo = ExponentialSmoothing(
        serie_fit, trend="add", damped_trend=True,
        seasonal=tipo_est, seasonal_periods=12 if tipo_est else None,
        initialization_method="estimated",
    )
    ajuste = modelo.fit(optimized=True)

    meses_fut = pd.date_range(
        serie_fit.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    forecast = np.clip(np.asarray(ajuste.forecast(horizonte)), 0, None)
    fitted = np.clip(np.nan_to_num(np.asarray(ajuste.fittedvalues), nan=0.0), 0, None)
    resid = serie_fit.values - fitted
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 2 else 0.0

    return {"fitted": pd.Series(fitted, index=serie_fit.index),
            "forecast": pd.Series(forecast, index=meses_fut),
            "sigma": sigma}


def _modelo_sarima(serie_fit: pd.Series, horizonte: int) -> dict | None:
    """SARIMAX estacional. Gateado afuera por MIN_MESES_SARIMA: con poca
    historia (< ~2.5 ciclos) el orden estacional no es identificable y
    statsmodels puede no converger — en ese caso el caller trata la
    excepción como "modelo no aplicable" y sigue con los demás candidatos."""
    modelo = SARIMAX(
        serie_fit, order=(1, 1, 1), seasonal_order=(1, 1, 1, 12),
        enforce_stationarity=False, enforce_invertibility=False,
    )
    ajuste = modelo.fit(disp=False)

    meses_fut = pd.date_range(
        serie_fit.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    pred = ajuste.get_forecast(steps=horizonte)
    forecast = np.clip(np.asarray(pred.predicted_mean), 0, None)
    fitted = np.clip(np.nan_to_num(np.asarray(ajuste.fittedvalues), nan=0.0), 0, None)
    resid = serie_fit.values - fitted
    resid = resid[np.isfinite(resid)]
    sigma = (float(np.std(resid, ddof=1)) if len(resid) > 2
             else float(np.asarray(pred.se_mean).mean()))

    return {"fitted": pd.Series(fitted, index=serie_fit.index),
            "forecast": pd.Series(forecast, index=meses_fut),
            "sigma": sigma}


# Registro de modelos de serie temporal propia: nombre -> (función, meses
# mínimos de historia para ser candidato). "estacional_robusto" es el piso
# determinístico (nunca falla); los demás compiten contra él por MAPE.
MODELOS = {
    "estacional_robusto": (_modelo_estacional_robusto, MIN_MESES),
    "holt_winters": (_modelo_holt_winters, MIN_MESES),
    "sarima": (_modelo_sarima, MIN_MESES_SARIMA),
}


def _candidatos_aplicables(n_meses: int) -> list[str]:
    return [nombre for nombre, (_, minimo) in MODELOS.items() if n_meses >= minimo]


def _ajustar_con_modelo(nombre: str, serie: pd.Series, horizonte: int) -> dict | None:
    """Winsoriza `serie` y corre el modelo `nombre`. None si el modelo no
    converge (se trata como "no aplicable para este grupo/fold", no como
    error fatal — el caller sigue con los demás candidatos)."""
    y_fit, atipicos = _winsorizar(serie.values.astype(float))
    serie_fit = pd.Series(y_fit, index=serie.index).asfreq("MS")
    fn, _ = MODELOS[nombre]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = fn(serie_fit, horizonte)
    except Exception:
        return None
    if res is None:
        return None
    res["atipicos"] = atipicos
    res["modelo"] = nombre
    return res


def _elegir_modelo(serie: pd.Series, horizonte: int, backtest: int) -> tuple[str, dict]:
    """Backtest de holdout: corre cada modelo aplicable sin los últimos
    `backtest` meses y mide MAPE contra los reales. Gana el de menor MAPE;
    "estacional_robusto" es el default si no hay backtest, la historia no
    alcanza para un holdout, o ningún candidato converge."""
    n = len(serie)
    if not backtest or n < MIN_MESES + backtest:
        return "estacional_robusto", {}

    candidatos = _candidatos_aplicables(n - backtest)
    mapes = {}
    for nombre in candidatos:
        res = _ajustar_con_modelo(nombre, serie.iloc[:-backtest], backtest)
        if res is None:
            continue
        m = _mape(serie.iloc[-backtest:].values, res["forecast"].values)
        if m is not None:
            mapes[nombre] = m

    if not mapes:
        return "estacional_robusto", {}
    return min(mapes, key=mapes.get), mapes


# ---------------------------------------------------------------------------
# kNN: préstamo de patrón para grupos con historia corta (< MIN_MESES)
# ---------------------------------------------------------------------------

def _vector_estacional_objetivo(serie: pd.Series) -> np.ndarray:
    """Vector de 12 posiciones (ratio al propio promedio) para los meses
    calendario que el grupo corto sí tiene datos; NaN en los que no."""
    v = np.full(12, np.nan)
    nivel = float(serie.mean())
    if nivel <= 0:
        return v
    ratios = serie.groupby(serie.index.month).mean() / nivel
    for mes, r in ratios.items():
        v[int(mes) - 1] = r
    return v


def _elegir_donantes(vector_objetivo: np.ndarray, perfiles: dict, k: int = K_VECINOS) -> list[str]:
    """k vecinos más cercanos (similitud coseno) entre los grupos con
    historia completa, comparando índices estacionales de 12 posiciones.
    Donde el grupo corto no tiene datos propios, se completa con el
    promedio de esa posición entre los donantes — para poder comparar en
    el mismo espacio de 12 dimensiones."""
    nombres = list(perfiles)
    if not nombres:
        return []
    X = np.array([perfiles[n]["idx_est"].reindex(range(1, 13)).values for n in nombres])
    prom_meses = np.nanmean(X, axis=0)
    objetivo_completo = np.where(np.isfinite(vector_objetivo), vector_objetivo, prom_meses)

    n_vecinos = min(k, len(nombres))
    vecinos = NearestNeighbors(n_neighbors=n_vecinos, metric="cosine").fit(X)
    _, idx = vecinos.kneighbors(objetivo_completo.reshape(1, -1))
    return [nombres[i] for i in idx[0]]


def _modelo_knn_prestado(serie: pd.Series, horizonte: int, perfiles: dict) -> dict | None:
    """Para un grupo con historia corta: toma el índice estacional y la
    tendencia relativa (b/a, tasa mensual) promedio de los k grupos más
    parecidos que sí tienen historia completa, y los escala al nivel
    propio (el único dato de escala que el grupo corto aporta). La banda
    de error queda deliberadamente ancha cuando hay muy pocos meses
    propios para medir residuos — es una estimación prestada, no ajustada."""
    donantes = _elegir_donantes(_vector_estacional_objetivo(serie), perfiles)
    if not donantes:
        return None

    idx_prom = pd.concat([perfiles[n]["idx_est"] for n in donantes], axis=1).mean(axis=1)
    media_idx = idx_prom.mean()
    idx_prom = idx_prom / media_idx if media_idx > 0 else idx_prom * 0 + 1.0

    b_rel = [perfiles[n]["b"] / perfiles[n]["a"] for n in donantes if perfiles[n]["a"] > 0]
    # tope +-5%/mes: no extrapolar sin límite una tendencia ajena al grupo
    b_rel_prom = float(np.clip(np.mean(b_rel) if b_rel else 0.0, -0.05, 0.05))

    nivel = float(serie.mean())
    phi = 0.8
    meses_fut = pd.date_range(
        serie.index[-1] + pd.offsets.MonthBegin(1), periods=horizonte, freq="MS")
    est_fut = np.where(np.isfinite(meses_fut.month.map(idx_prom).values),
                       meses_fut.month.map(idx_prom).values, 1.0)
    acum_phi = np.cumsum(phi ** np.arange(1, horizonte + 1))
    forecast = np.clip(nivel * (1.0 + b_rel_prom * acum_phi) * est_fut, 0, None)

    est_hist_raw = serie.index.month.map(idx_prom).values
    est_hist = np.where(np.isfinite(est_hist_raw), est_hist_raw, 1.0)
    fitted = np.clip(nivel * est_hist, 0, None)
    resid = serie.values - fitted
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 2 else nivel * 0.75

    return {"fitted": pd.Series(fitted, index=serie.index),
            "forecast": pd.Series(forecast, index=meses_fut),
            "sigma": sigma, "donantes": donantes}


def _mape(reales: np.ndarray, previstos: np.ndarray) -> float | None:
    mask = reales > 0
    if not mask.any():
        return None
    return float(np.mean(np.abs(reales[mask] - previstos[mask]) / reales[mask]) * 100)


def _agregar_filas(filas: list, grupo, serie: pd.Series, fc: pd.Series,
                   sigma: float, precio_medio: float) -> None:
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
    conn = conectar(proyecto)
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
        attrs: "mape" (dict por grupo, del modelo que ganó), "grupos_excluidos"
        (historia < MIN_MESES_KNN), "meses_atipicos", "metrica",
        "modelo_elegido" (dict por grupo — nombre del modelo o
        "kNN (prestado de: ...)"), "mape_por_modelo" (dict por grupo de
        {modelo: mape} para los candidatos que compitieron).
    """
    if nivel not in NIVELES:
        raise ValueError(f"nivel debe ser uno de {NIVELES}, no '{nivel}'")
    if metrica not in METRICAS:
        raise ValueError(f"metrica debe ser una de {METRICAS}, no '{metrica}'")
    if horizonte < 1:
        raise ValueError("horizonte debe ser >= 1")

    conn = conectar(proyecto)
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
    modelos_elegidos, mape_por_modelo, perfiles_donantes = {}, {}, {}
    grupos_cortos = []  # historia entre MIN_MESES_KNN y MIN_MESES-1: se resuelven después vía kNN

    for grupo, g in base.groupby("grupo", dropna=False):
        serie = (g.set_index("mes")["unidades"]
                 .resample("MS").sum().fillna(0.0))
        precio_medio = (g["valor"].sum() / g["unidades"].sum()
                        if g["unidades"].sum() > 0 else 0.0)
        n = len(serie)

        if n < MIN_MESES_KNN:
            excluidos.append(str(grupo))
            continue

        if n < MIN_MESES:
            grupos_cortos.append((grupo, serie, precio_medio))
            continue

        modelo_elegido, mapes_modelo = _elegir_modelo(serie, horizonte, backtest)
        if mapes_modelo:
            mape_por_modelo[str(grupo)] = mapes_modelo
            mapes[str(grupo)] = mapes_modelo.get(modelo_elegido)

        resultado = _ajustar_con_modelo(modelo_elegido, serie, horizonte)
        if resultado is None:
            # "estacional_robusto" es determinístico y nunca falla; si el
            # ganador del backtest no converge sobre la serie completa
            # (raro, pero posible en el borde de un gate), se cae a él.
            modelo_elegido = "estacional_robusto"
            resultado = _ajustar_con_modelo(modelo_elegido, serie, horizonte)
        modelos_elegidos[str(grupo)] = modelo_elegido

        meses_at = [m.strftime("%Y-%m") for m in serie.index[resultado["atipicos"]]]
        if meses_at:
            atipicos_all[str(grupo)] = meses_at

        # Perfil (índice estacional + tendencia) para donar a grupos con
        # historia corta, sea cual sea el modelo que haya ganado acá.
        y_fit_full, _ = _winsorizar(serie.values.astype(float))
        perfiles_donantes[str(grupo)] = _perfil_estacional_tendencia(
            pd.Series(y_fit_full, index=serie.index))

        _agregar_filas(filas, grupo, serie, resultado["forecast"],
                       resultado["sigma"], precio_medio)

    for grupo, serie, precio_medio in grupos_cortos:
        resultado = _modelo_knn_prestado(serie, horizonte, perfiles_donantes)
        if resultado is None:
            excluidos.append(str(grupo))
            continue
        modelos_elegidos[str(grupo)] = f"kNN (prestado de: {', '.join(resultado['donantes'])})"
        _agregar_filas(filas, grupo, serie, resultado["forecast"],
                       resultado["sigma"], precio_medio)

    res = pd.DataFrame(filas)
    if not res.empty:
        res["mes"] = pd.to_datetime(res["mes"]).dt.strftime("%Y-%m")
        res = res.sort_values(["grupo", "mes"]).reset_index(drop=True)
    res.attrs["mape"] = mapes
    res.attrs["grupos_excluidos"] = excluidos
    res.attrs["meses_atipicos"] = atipicos_all
    res.attrs["metrica"] = metrica
    res.attrs["modelo_elegido"] = modelos_elegidos
    res.attrs["mape_por_modelo"] = mape_por_modelo
    return res


# ---------------------------------------------------------------------------
# Detalle por SKU: estimación de demanda + stock exhibir/pedir
# ---------------------------------------------------------------------------

def estimar_demanda_stock_sku(
    proyecto: str,
    mes_desde: str,
    mes_hasta: str,
    codigos: list[str] | None = None,
    sucursales_exhibir: list[str] | None = None,
    dias_historia_tendencia: int = 90,
    anios_historia_max: int = 5,
    depositos_distribucion: tuple[str, ...] | list[str] = DEPOSITOS_DISTRIBUCION,
    fecha_stock: str | None = None,
) -> pd.DataFrame:
    """
    Estimación de demanda por SKU para un período puntual (`mes_desde` a
    `mes_hasta`, ej. el rango que se está por pronosticar arriba en la
    página), cruzada con el stock actual. A diferencia de `forecast_ventas`,
    acá NO se ajusta un modelo estacional completo — a nivel SKU puntual la
    demanda es errática (clase Z) y un ajuste fino sería ruido — pero sí se
    combinan dos señales simples y transparentes:

      1. Tendencia reciente: demanda diaria de los últimos
         `dias_historia_tendencia` días, extrapolada a la cantidad de días
         del período pedido.
      2. Mismo período en años anteriores: venta real del mismo rango de
         meses calendario, un año atrás, dos años atrás, etc. (tantos años
         como haya con el rango completo cargado), promediados.

    La estimación final es el promedio de ambas señales (si no hay ningún
    año anterior completo todavía, se usa solo la tendencia reciente). Esto
    es deliberadamente simple y auditable: cada componente queda en su
    propia columna para que se entienda de dónde sale el número, no una
    caja negra.

    Args:
        mes_desde, mes_hasta:   rango de meses a estimar, formato "YYYY-MM"
                                (ej. el "próximo mes" hasta el mes elegido
                                arriba en la página).
        codigos:                subconjunto de SKUs (None = todos los que
                                tuvieron venta o stock en la ventana).
        sucursales_exhibir:     sucursales cuyo stock cuenta como "para
                                exhibir" y sobre las que se mide la demanda
                                (vacío/None = todas las que no son depósito
                                de logística).
        dias_historia_tendencia: ventana de demanda reciente, en días.
        anios_historia_max:     tope de años hacia atrás a considerar para
                                la señal histórica (se corta antes si no hay
                                datos).
        depositos_distribucion: depósitos cuyo stock cuenta como "para
                                pedir" (default: 001/002).
        fecha_stock:            snapshot de stock a usar (None = el más
                                reciente).

    Returns:
        DataFrame por SKU: codigo, descripcion, rubro, marca, clasificacion,
        demanda_diaria (tendencia reciente, por día), demanda_tendencia
        (reciente extrapolada al período), demanda_historica_prom (promedio
        del mismo período en años anteriores; NA si no hay ninguno completo
        todavía), demanda_estimada (promedio de las dos anteriores),
        valor_estimado, stock_exhibir, stock_distribucion, cobertura_cd_dias
        (días que cubre el stock del CD al ritmo diario reciente; NA sin
        demanda), venta_<año> (unidades vendidas ese año calendario, mismo
        alcance de sucursales). Ordenado por valor_estimado desc.

        `clasificacion` (regla simple por disponibilidad, no reemplaza el
        juicio de quien compra):
          - "Exhibir":            hay stock en sucursal y se vende.
          - "Pedir":               no hay stock en sucursal pero sí en el CD.
          - "Recomendar compra":   no hay stock ni en sucursal ni en el CD.
          - "Sin rotación":        hay stock en sucursal pero no se vende
                                   (caso no pedido explícitamente, pero hay
                                   que declararlo para que la clasificación
                                   sea exhaustiva).
        attrs: fecha_stock, dias_periodo, mes_desde, mes_hasta, anios_historia
        (años efectivamente usados en la señal histórica).
    """
    depositos_distribucion = list(depositos_distribucion or [])
    sucursales_exhibir = list(sucursales_exhibir or [])
    codigos = list(codigos or [])

    mes_desde_p = pd.Period(mes_desde, freq="M")
    mes_hasta_p = pd.Period(mes_hasta, freq="M")
    if mes_hasta_p < mes_desde_p:
        raise ValueError("mes_hasta debe ser >= mes_desde")
    dias_periodo = (mes_hasta_p.end_time.normalize()
                    - mes_desde_p.start_time.normalize()).days + 1

    conn = conectar(proyecto)
    try:
        if fecha_stock:
            fs = conn.execute(
                "SELECT MAX(fecha_snapshot) FROM stock_sucursal WHERE fecha_snapshot <= ?",
                [str(pd.to_datetime(fecha_stock).date())],
            ).fetchone()[0]
        else:
            fs = conn.execute("SELECT MAX(fecha_snapshot) FROM stock_sucursal").fetchone()[0]
        fs = str(fs) if fs else None

        # Sin sucursales explícitas, "para exhibir" son todas las bocas reales:
        # ni depósito de logística (es_logistica) ni centro de distribución
        # (depositos_distribucion) — los CD no son "vidriera", son de donde se
        # pide, y es_logistica marca otra cosa (outlets, e-commerce), no los
        # CD reales (ver docstring de comparar_clase_abc).
        filtro_base = ("m.codigodepo IN (SELECT unnest($sucs))" if sucursales_exhibir
                       else "NOT COALESCE(d.es_logistica, FALSE) "
                            "AND m.codigodepo NOT IN (SELECT unnest($cds))")
        filtro_cod = "AND m.codigo IN (SELECT unnest($cods))" if codigos else ""
        params = {}
        if sucursales_exhibir:
            params["sucs"] = sucursales_exhibir
        else:
            params["cds"] = depositos_distribucion
        if codigos:
            params["cods"] = codigos

        df_dem = conn.execute(f"""
            SELECT m.codigo, -SUM(m.diferencia)::DOUBLE AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
            LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
            WHERE m.fecha > (SELECT MAX(fecha) FROM movimientos) - INTERVAL ($dias) DAY
              AND {filtro_base} {filtro_cod}
            GROUP BY 1
        """, {**params, "dias": dias_historia_tendencia}).df()

        # Venta por año calendario (todo el histórico disponible, mismo
        # alcance de sucursales) — para mostrar la evolución año a año junto
        # a la estimación reciente.
        df_venta_anio = conn.execute(f"""
            SELECT m.codigo, year(m.fecha)::INT AS anio,
                   -SUM(m.diferencia)::DOUBLE AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
            LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
            WHERE {filtro_base} {filtro_cod}
            GROUP BY 1, 2
        """, params).df()

        # Mismo período (mes_desde..mes_hasta) un año atrás, dos años atrás...
        # tantos como quepan enteros dentro del histórico cargado.
        fmin_fecha, fmax_fecha = conn.execute(
            "SELECT MIN(fecha), MAX(fecha) FROM movimientos").fetchone()
        fmin_p, fmax_p = pd.Period(fmin_fecha, freq="M"), pd.Period(fmax_fecha, freq="M")

        df_hist_por_k = {}
        anios_historia = []
        for k in range(1, anios_historia_max + 1):
            d_shift, h_shift = mes_desde_p - 12 * k, mes_hasta_p - 12 * k
            if d_shift < fmin_p or h_shift > fmax_p:
                break
            anios_historia.append(d_shift.year)
            df_hist_por_k[k] = conn.execute(f"""
                SELECT m.codigo, -SUM(m.diferencia)::DOUBLE AS unidades
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
                LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
                WHERE (year(m.fecha) * 12 + month(m.fecha))
                      BETWEEN $desde_ord AND $hasta_ord
                  AND {filtro_base} {filtro_cod}
                GROUP BY 1
            """, {**params, "desde_ord": d_shift.year * 12 + d_shift.month,
                  "hasta_ord": h_shift.year * 12 + h_shift.month}).df()

        def _stock(depos: list[str]) -> pd.DataFrame:
            if not fs or not depos:
                return pd.DataFrame(columns=["codigo", "stock"])
            return conn.execute("""
                SELECT codigo, SUM(stock)::DOUBLE AS stock
                FROM stock_sucursal
                WHERE codigodepo IN (SELECT unnest($depos)) AND fecha_snapshot = $fs::DATE
                GROUP BY 1
            """, {"depos": depos, "fs": fs}).df()

        if sucursales_exhibir:
            df_exh = _stock(sucursales_exhibir)
        else:
            deps_no_log = conn.execute(
                "SELECT codigodepo FROM depositos "
                "WHERE NOT COALESCE(es_logistica, FALSE) "
                "AND codigodepo NOT IN (SELECT unnest($cds))",
                {"cds": depositos_distribucion},
            ).df()["codigodepo"].tolist()
            df_exh = _stock(deps_no_log)
        df_dist = _stock(depositos_distribucion)

        precio = (precios_snapshot(conn, None)
                  .set_index("codigo")["lista_1"].astype(float))
    finally:
        conn.close()

    cat = catalogo_articulos(proyecto)
    if codigos:
        cat = cat[cat["codigo"].isin(codigos)]
    base = cat[["codigo", "descripcion", "rubro", "marca"]].drop_duplicates("codigo")

    df = (base.merge(df_dem, on="codigo", how="left")
          .merge(df_exh.rename(columns={"stock": "stock_exhibir"}), on="codigo", how="left")
          .merge(df_dist.rename(columns={"stock": "stock_distribucion"}), on="codigo", how="left"))

    hist_cols = [f"_hist_k{k}" for k in df_hist_por_k]
    for k, df_k in df_hist_por_k.items():
        df = df.merge(df_k.rename(columns={"unidades": f"_hist_k{k}"}), on="codigo", how="left")

    for c in ["unidades", "stock_exhibir", "stock_distribucion", *hist_cols]:
        df[c] = df[c].fillna(0.0)

    # Sin venta (reciente o histórica) ni stock en ningún lado no aporta nada.
    hay_algo = (df["unidades"] > 0) | (df["stock_exhibir"] > 0) | (df["stock_distribucion"] > 0)
    for c in hist_cols:
        hay_algo = hay_algo | (df[c] > 0)
    df = df[hay_algo].copy()

    df["demanda_diaria"] = df["unidades"] / dias_historia_tendencia
    df["demanda_tendencia"] = df["demanda_diaria"] * dias_periodo
    if hist_cols:
        df["demanda_historica_prom"] = df[hist_cols].mean(axis=1)
        df["demanda_estimada"] = df[["demanda_tendencia", "demanda_historica_prom"]].mean(axis=1)
        df = df.drop(columns=hist_cols)
    else:
        df["demanda_historica_prom"] = float("nan")
        df["demanda_estimada"] = df["demanda_tendencia"]
    df["valor_estimado"] = (df["demanda_estimada"] * df["codigo"].map(precio)).fillna(0.0)
    df["cobertura_cd_dias"] = (
        df["stock_distribucion"] / df["demanda_diaria"].where(df["demanda_diaria"] > 0))

    # --- clasificación: dónde está el stock (o no) frente a la demanda ------
    hay_exhibir = df["stock_exhibir"] > 0
    hay_distribucion = df["stock_distribucion"] > 0
    hay_demanda = df["demanda_estimada"] > 0
    df["clasificacion"] = "Sin rotación"
    df.loc[~hay_exhibir & hay_distribucion, "clasificacion"] = "Pedir"
    df.loc[~hay_exhibir & ~hay_distribucion, "clasificacion"] = "Recomendar compra"
    df.loc[hay_exhibir & hay_demanda, "clasificacion"] = "Exhibir"

    # --- venta por año calendario (columnas venta_<año>) --------------------
    cols_venta = []
    if not df_venta_anio.empty:
        pivot_venta = (df_venta_anio.pivot_table(
            index="codigo", columns="anio", values="unidades", fill_value=0.0))
        pivot_venta.columns = [f"venta_{int(a)}" for a in pivot_venta.columns]
        cols_venta = sorted(pivot_venta.columns.tolist())
        df = df.merge(pivot_venta.reset_index(), on="codigo", how="left")
        for c in cols_venta:
            df[c] = df[c].fillna(0.0)

    cols = (["codigo", "descripcion", "rubro", "marca", "clasificacion",
             "demanda_diaria", "demanda_tendencia", "demanda_historica_prom",
             "demanda_estimada", "valor_estimado",
             "stock_exhibir", "stock_distribucion", "cobertura_cd_dias"]
            + cols_venta)
    df = df[cols].sort_values("valor_estimado", ascending=False).reset_index(drop=True)
    df.attrs["fecha_stock"] = fs
    df.attrs["dias_periodo"] = dias_periodo
    df.attrs["mes_desde"] = mes_desde
    df.attrs["mes_hasta"] = mes_hasta
    df.attrs["anios_historia"] = anios_historia
    return df
