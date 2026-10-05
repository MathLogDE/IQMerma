"""
core/forecasting/semanal.py — Forecast semanal con feriados/eventos AR (Prophet)

`core/forecasting/demanda.py` proyecta a nivel MENSUAL — ahí un feriado o
evento puntual (Día de la Madre, Black Friday, Navidad) queda diluido dentro
de todo el mes, así que un modelo no puede aprender su efecto específico. A
nivel SEMANAL sí se puede: Prophet admite una lista de fechas con un peso
propio (`holidays`) y aprende cuánto mueve la venta cada una — exactamente
lo que SARIMA no hace bien sin armar a mano variables exógenas.

Calendario de eventos: feriados oficiales de Argentina (paquete `holidays`,
gratuito) + dos eventos comerciales fijos que NO son feriados pero mueven
mucho la venta en bazar/decoración y no están en ningún calendario oficial:
Día de la Madre (3er domingo de octubre) y Día del Niño (3er domingo de
agosto). Cada evento tiene una ventana de ±3 días alrededor de la fecha
exacta — a nivel semanal, sin esa ventana el efecto podría caer en la semana
"equivocada" según dónde arranque la semana calendario.

Pipeline nuevo y SEPARADO del mensual (no entra a la competencia de modelos
de `demanda.py`, que sigue siendo mensual) — se muestra en su propia sección
en la página Forecast. Niveles habilitados: "total", "gran_super_rubro",
"sucursal" — se EXCLUYEN "rubro" (141 grupos) y "marca" (587) por costo:
cada ajuste de Prophet arranca un subproceso de `cmdstan` (overhead real de
proceso, no solo cómputo) — un ajuste aislado tarda ~0.6s, pero medido con
"rubro" completo (141 grupos × 2 ajustes, backtest + final) salen ~16
minutos, inviable para una página interactiva aunque quede cacheado. Mismo
motivo que ya excluye SARIMA de "marca" en `demanda.py`, un escalón más
abajo por lo cara que es cada corrida de Prophet.

Categorías con un patrón "todo o nada" (ej. un gran_super_rubro "NAVIDAD":
casi cero 50 semanas al año y un pico enorme en 2-3 semanas de diciembre) son
un caso límite para cualquier descomposición estacional suave (Fourier, que
usa Prophet): medido en backtest real, ni modo multiplicativo ni aditivo, ni
bajar el orden de Fourier, evitan que el componente estacional "contamine"
semanas de temporada baja con valores muy altos que no corresponden — la
serie es demasiado parecida a una función escalón para una suma de senos.
En vez de mostrar esos forecasts rotos, si el WMAPE de backtest de un grupo
supera `UMBRAL_WMAPE_EXCLUSION` se lo excluye (queda en
attrs["grupos_excluidos"] con motivo) en vez de proyectarlo — mismo criterio
de "no mostrar lo que no sirve" que ya usa `demanda.py` con historia corta.

Uso:
    from core.forecasting.semanal import forecast_ventas_semanal

    df = forecast_ventas_semanal("cliente_x", nivel="gran_super_rubro", horizonte=12)
"""

import logging
import warnings

import numpy as np
import pandas as pd
import holidays as holidays_pkg
from prophet import Prophet

from core.comun import conectar, precios_snapshot, mapa_estructura

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

NIVELES_SEMANAL = ("total", "gran_super_rubro", "sucursal")
METRICAS = ("ventas", "transferencias")
MIN_SEMANAS = 20
# Estacionalidad anual solo con >= 2 años de historia semanal — con menos,
# Prophet la deja "under-identified" (mismo criterio que Holt-Winters en
# demanda.py, que también gatea la estacionalidad a 24 meses de historia).
MIN_SEMANAS_ESTACIONAL = 104
DIA_MADRE_MES, DIA_NINO_MES = 10, 8
# Grupos "todo o nada" (ver docstring del módulo): con un WMAPE de backtest
# por encima de esto, el forecast no es confiable — se excluye en vez de
# mostrarlo. 150% ya es un error mayor al 100% del valor real acumulado.
UMBRAL_WMAPE_EXCLUSION = 150.0


# ---------------------------------------------------------------------------
# Calendario de feriados + eventos comerciales AR
# ---------------------------------------------------------------------------

def _tercer_domingo(anio: int, mes: int) -> pd.Timestamp:
    d = pd.Timestamp(anio, mes, 1)
    primer_domingo = d + pd.Timedelta(days=(6 - d.weekday()) % 7)
    return primer_domingo + pd.Timedelta(weeks=2)


def calendario_eventos_ar(anio_desde: int, anio_hasta: int) -> pd.DataFrame:
    """Feriados oficiales AR + Día de la Madre/Niño, formato `holidays` de
    Prophet (columnas ds/holiday/lower_window/upper_window)."""
    anios = range(anio_desde, anio_hasta + 1)
    ar = holidays_pkg.Argentina(years=list(anios))
    filas = [{"ds": pd.Timestamp(fecha), "holiday": nombre} for fecha, nombre in ar.items()]
    for anio in anios:
        filas.append({"ds": _tercer_domingo(anio, DIA_MADRE_MES), "holiday": "Día de la Madre"})
        filas.append({"ds": _tercer_domingo(anio, DIA_NINO_MES), "holiday": "Día del Niño"})
    df = pd.DataFrame(filas)
    df["lower_window"] = -3
    df["upper_window"] = 3
    return df


# ---------------------------------------------------------------------------
# Serie semanal (mismo patrón que _serie_mensual en demanda.py, a semana)
# ---------------------------------------------------------------------------

def _serie_semanal(conn, codigodepo: str | None, nivel: str,
                   metrica: str = "ventas") -> pd.DataFrame:
    filtro_depo = "AND m.codigodepo = $depo" if codigodepo else ""
    params = {"depo": codigodepo} if codigodepo else []

    if metrica == "ventas":
        sql = f"""
            SELECT date_trunc('week', m.fecha) AS semana,
                   m.codigo, m.codigodepo, -SUM(m.diferencia) AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
            WHERE 1=1 {filtro_depo}
            GROUP BY 1, 2, 3
        """
        precio_col = "lista_1"
    else:
        sql = f"""
            SELECT date_trunc('week', m.fecha) AS semana,
                   m.codigo, m.codigodepo, SUM(m.diferencia) AS unidades
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo AND t.categoria = 'Remitido'
            WHERE m.diferencia > 0 {filtro_depo}
            GROUP BY 1, 2, 3
        """
        precio_col = "costo"

    df = conn.execute(sql, params).df()
    df_val = (precios_snapshot(conn, None)[["codigo", precio_col]]
              .rename(columns={precio_col: "precio"}))
    if df.empty:
        return pd.DataFrame(columns=["semana", "grupo", "unidades", "valor"])

    val = df_val.set_index("codigo")["precio"].astype(float)
    df["valor"] = (df["unidades"] * df["codigo"].map(val)).fillna(0.0)

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
        mapa = mapa_estructura(est).set_index("_clave")
        rubro_art = arts.set_index("codigo")["rubro"].astype("string").str.strip()
        clave = df["codigo"].map(rubro_art)
        col = "rubro_desc" if nivel == "rubro" else "gran_super_rubro"
        df["grupo"] = clave.map(mapa[col]).fillna("Sin clasificar")

    out = (df.groupby(["semana", "grupo"], dropna=False)
           .agg(unidades=("unidades", "sum"), valor=("valor", "sum"))
           .reset_index())
    out["semana"] = pd.to_datetime(out["semana"])
    return out


def _wmape(reales: np.ndarray, previstos: np.ndarray) -> float | None:
    """WMAPE (sum|error|/sum(real)), no MAPE promediado: a nivel semanal
    algunos grupos (sucursales chicas, rubros nicho) tienen semanas con muy
    pocas unidades — ahí un MAPE por semana puede explotar a miles de %
    (mismo problema que llevó a usar WMAPE/MAE en pooled.py e
    intermitente.py en vez de MAPE)."""
    total_real = reales.sum()
    if total_real <= 0:
        return None
    return float(np.sum(np.abs(reales - previstos)) / total_real * 100)


def _ajustar_prophet(serie: pd.Series, horizonte: int, eventos: pd.DataFrame) -> pd.DataFrame:
    df_prophet = pd.DataFrame({"ds": serie.index, "y": np.clip(serie.values, 0, None)})
    modelo = Prophet(
        yearly_seasonality=len(serie) >= MIN_SEMANAS_ESTACIONAL,
        weekly_seasonality=False, daily_seasonality=False,
        seasonality_mode="multiplicative", holidays=eventos, interval_width=0.95,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        modelo.fit(df_prophet)
    futuro = modelo.make_future_dataframe(periods=horizonte, freq="W")
    pred = modelo.predict(futuro)
    pred = pred.set_index("ds").clip(lower=0)
    return pred[["yhat", "yhat_lower", "yhat_upper"]]


def forecast_ventas_semanal(
    proyecto: str,
    codigodepo: str | None = None,
    nivel: str = "total",
    horizonte: int = 12,
    backtest: int = 12,
    metrica: str = "ventas",
) -> pd.DataFrame:
    """
    Proyección SEMANAL por grupo, con feriados/eventos argentinos como
    regresor explícito (Prophet). Niveles: `NIVELES_SEMANAL` — "rubro" y
    "marca" quedan afuera por costo (ver docstring del módulo).

    Returns:
        DataFrame largo: grupo, semana ("YYYY-MM-DD", lunes de esa semana),
        tipo ('real'/'forecast'), unidades, banda_inf, banda_sup, valor.
        attrs: "wmape" (dict por grupo, backtest — sum|error|/sum(real), no
        MAPE promediado: con semanas de pocas unidades el MAPE puede
        explotar), "grupos_excluidos" (< MIN_SEMANAS), "metrica".
    """
    if nivel not in NIVELES_SEMANAL:
        raise ValueError(f"nivel debe ser uno de {NIVELES_SEMANAL}, no '{nivel}'")
    if metrica not in METRICAS:
        raise ValueError(f"metrica debe ser una de {METRICAS}, no '{metrica}'")
    if horizonte < 1:
        raise ValueError("horizonte debe ser >= 1")

    conn = conectar(proyecto)
    try:
        base = _serie_semanal(conn, codigodepo, nivel, metrica)
    finally:
        conn.close()

    vacio = pd.DataFrame(columns=["grupo", "semana", "tipo", "unidades", "valor"])
    vacio.attrs.update(wmape={}, grupos_excluidos=[], metrica=metrica)
    if base.empty:
        return vacio

    # última semana suele estar incompleta (carga parcial) → se descarta del
    # ajuste si su venta es < 50% de la mediana de las 8 semanas previas.
    ultima = base["semana"].max()
    tot_ult = base.loc[base["semana"] == ultima, "unidades"].sum()
    previas = (base[base["semana"] < ultima].groupby("semana")["unidades"].sum().tail(8))
    if len(previas) >= 4 and tot_ult < 0.5 * previas.median():
        base = base[base["semana"] < ultima]

    anio_desde = int(base["semana"].min().year)
    anio_hasta = int(base["semana"].max().year) + 1 + (horizonte // 52 + 1)
    eventos = calendario_eventos_ar(anio_desde, anio_hasta)

    filas, wmapes, excluidos = [], {}, []
    for grupo, g in base.groupby("grupo", dropna=False):
        serie = (g.set_index("semana")["unidades"].resample("W").sum().fillna(0.0))
        precio_medio = (g["valor"].sum() / g["unidades"].sum()
                        if g["unidades"].sum() > 0 else 0.0)
        n = len(serie)
        if n < MIN_SEMANAS:
            excluidos.append(str(grupo))
            continue

        if backtest and n > MIN_SEMANAS + backtest:
            train_bt = serie.iloc[:-backtest]
            pred_bt = _ajustar_prophet(train_bt, backtest, eventos)
            pred_bt_fut = pred_bt[pred_bt.index > train_bt.index.max()]
            w = _wmape(serie.iloc[-backtest:].values, pred_bt_fut["yhat"].values)
            if w is not None:
                wmapes[str(grupo)] = w
                if w > UMBRAL_WMAPE_EXCLUSION:
                    # patrón "todo o nada" (ver docstring): Prophet no lo
                    # representa bien, no tiene sentido mostrar el forecast.
                    excluidos.append(f"{grupo} (WMAPE backtest {w:.0f}%)")
                    continue

        pred = _ajustar_prophet(serie, horizonte, eventos)
        pred_fut = pred[pred.index > serie.index.max()]

        for semana, y in serie.items():
            filas.append({"grupo": grupo, "semana": semana, "tipo": "real",
                          "unidades": float(y), "banda_inf": None, "banda_sup": None,
                          "valor": float(y) * precio_medio})
        for semana, row in pred_fut.iterrows():
            filas.append({"grupo": grupo, "semana": semana, "tipo": "forecast",
                          "unidades": float(row["yhat"]),
                          "banda_inf": float(row["yhat_lower"]),
                          "banda_sup": float(row["yhat_upper"]),
                          "valor": float(row["yhat"]) * precio_medio})

    res = pd.DataFrame(filas)
    if not res.empty:
        res["semana"] = pd.to_datetime(res["semana"]).dt.strftime("%Y-%m-%d")
        res = res.sort_values(["grupo", "semana"]).reset_index(drop=True)
    res.attrs["wmape"] = wmapes
    res.attrs["grupos_excluidos"] = excluidos
    res.attrs["metrica"] = metrica
    return res
