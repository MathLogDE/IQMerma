"""
core/forecasting/evaluacion.py — Fase 5: evaluación comparativa

Las fases 2 y 3 del roadmap (Croston/TSB para intermitentes,
`core/forecasting/intermitente.py`; LightGBM pooled para continuo/errático/
lumpy, `core/forecasting/pooled.py`) se sumaron como "segunda opinión" AL
LADO del baseline actual en producción (`estimar_demanda_stock_sku` en
`demanda.py`: tendencia reciente + promedio histórico del mismo período en
años anteriores) — ninguna lo reemplazó. Este módulo es la evaluación que
faltaba para decidir con datos, no a ojo, si conviene reemplazarlo en algún
segmento.

Metodología: mismo backtest, mismo WMAPE, para ambos lados de la
comparación — comparar métricas calculadas distinto sería comparar peras con
manzanas. El baseline productivo usa una ventana diaria (90 días) y no
acepta una fecha de corte arbitraria para "qué hubiera predicho hace unos
meses" — acá se reproduce su misma lógica (tendencia reciente + promedio
histórico del mismo mes calendario en años anteriores) pero a granularidad
MENSUAL sobre el panel de `ventas_mensuales_por_sku`, evaluada en los
últimos `backtest` meses de cada serie — el mismo holdout que ya usan
Croston/TSB. Es una aproximación mensual del baseline diario real, no una
llamada directa a `estimar_demanda_stock_sku` (esa función no está pensada
para "rebobinar" el reloj); el objetivo es comparar el MISMO criterio
(tendencia + histórico) contra los métodos nuevos, no reproducir el output
exacto de la UI.

Uso:
    from core.forecasting.evaluacion import evaluar_modelos

    resumen = evaluar_modelos("cliente_x", backtest=3)
"""

import numpy as np
import pandas as pd

from core.comun import conectar
from core.forecasting.segmentacion import clasificar_demanda_sku, ventas_mensuales_por_sku
from core.forecasting.intermitente import elegir_modelo_intermitente, FUNCIONES_INTERMITENTE
from core.forecasting.pooled import SEGMENTOS_POOLED, forecast_pooled

ETIQUETA_METODO_NUEVO = {"intermitente": "Croston/TSB",
                         "pooled": "LightGBM pooled"}


def _wmape(real, pred) -> float | None:
    real = np.asarray(real, dtype=float)
    pred = np.asarray(pred, dtype=float)
    total = real.sum()
    if total <= 0:
        return None
    return float(np.sum(np.abs(real - pred)) / total * 100)


def _baseline_tendencia_historico(serie: pd.Series, backtest: int) -> np.ndarray:
    """Reproduce a mano, a nivel mensual, el criterio del baseline
    productivo: promedio de los 3 meses previos al corte ("tendencia
    reciente"), promediado con el mismo mes calendario 1-3 años antes
    ("histórico") cuando hay dato — igual que `estimar_demanda_stock_sku`,
    sin ver ningún mes del holdout."""
    train = serie.iloc[:-backtest]
    tendencia = float(train.tail(3).mean()) if len(train) else 0.0
    preds = []
    for mes in serie.index[-backtest:]:
        hist_vals = [train.loc[mes - pd.DateOffset(years=k)]
                     for k in (1, 2, 3) if (mes - pd.DateOffset(years=k)) in train.index]
        preds.append(float(np.mean([tendencia, np.mean(hist_vals)])) if hist_vals else tendencia)
    return np.array(preds)


def _evaluar_intermitente(ventas: pd.DataFrame, codigos: list[str], backtest: int) -> dict:
    v = ventas[ventas["codigo"].isin(codigos)]
    if v.empty:
        return {}
    mes_max = v["mes"].max()
    reales, pred_base, pred_nuevo, meses = [], [], [], []
    for _, g in v.groupby("codigo"):
        serie = g.set_index("mes")["unidades"].resample("MS").sum()
        serie = serie.reindex(pd.date_range(serie.index.min(), mes_max, freq="MS"), fill_value=0.0)
        if len(serie) <= backtest + 3:
            continue
        y = np.clip(serie.values.astype(float), 0, None)
        train, test = y[:-backtest], y[-backtest:]

        reales.extend(test)
        meses.extend(serie.index[-backtest:])
        pred_base.extend(_baseline_tendencia_historico(pd.Series(y, index=serie.index), backtest))

        modelo, _ = elegir_modelo_intermitente(train, backtest)
        nivel = FUNCIONES_INTERMITENTE[modelo](train)
        pred_nuevo.extend([nivel] * backtest)

    return {"reales": reales, "pred_base": pred_base, "pred_nuevo": pred_nuevo, "meses": meses}


def _curva_mensual(meses: list, reales: list, pred_base: list, pred_nuevo: list,
                   etiqueta_base: str, etiqueta_nuevo: str) -> pd.DataFrame:
    """Agrega (suma) real/predicho de todos los SKUs de un segmento, mes a
    mes — para graficar la curva del backtest en la UI, no solo el WMAPE
    final agregado."""
    if not meses:
        return pd.DataFrame(columns=["mes", "real", etiqueta_base, etiqueta_nuevo])
    df = pd.DataFrame({"mes": pd.to_datetime(meses).strftime("%Y-%m"),
                       "real": reales, etiqueta_base: pred_base, etiqueta_nuevo: pred_nuevo})
    return df.groupby("mes", as_index=False).sum().sort_values("mes").reset_index(drop=True)


SEGMENTO_INTERMITENTE_LABEL = "intermitente"
SEGMENTO_POOLED_LABEL = "continuo/errático/lumpy"
BASELINE_LABEL = "Baseline (tendencia+histórico)"


def evaluar_modelos(proyecto: str, backtest: int = 3, meses_min_historia: int = 6) -> pd.DataFrame:
    """
    Compara, segmento por segmento, el baseline actual contra el método
    nuevo de la fase correspondiente del roadmap, sobre el mismo holdout.

    Returns:
        DataFrame: segmento, metodo, wmape, n_skus, es_nuevo (bool — para
        remarcar en la UI cuál es el método candidato a reemplazar al
        baseline). attrs["curva_mensual"]: dict {segmento: DataFrame(mes,
        real, <col por método)} — suma real vs. predicho de todos los SKUs
        del segmento, mes a mes, en el holdout del backtest (para graficar
        en la UI sin reentrenar nada).
    """
    seg = clasificar_demanda_sku(proyecto, meses_min_historia=meses_min_historia)
    conn = conectar(proyecto)
    try:
        ventas = ventas_mensuales_por_sku(conn)
    finally:
        conn.close()

    filas = []
    curva_mensual = {}

    # --- intermitente: baseline vs Croston/TSB ---------------------------
    codigos_interm = seg.loc[seg["segmento"] == "intermitente", "codigo"].tolist()
    ev = _evaluar_intermitente(ventas, codigos_interm, backtest)
    if ev:
        metodo_nuevo = ETIQUETA_METODO_NUEVO["intermitente"]
        filas.append({"segmento": SEGMENTO_INTERMITENTE_LABEL, "metodo": BASELINE_LABEL,
                      "wmape": _wmape(ev["reales"], ev["pred_base"]),
                      "n_skus": len(codigos_interm), "es_nuevo": False})
        filas.append({"segmento": SEGMENTO_INTERMITENTE_LABEL, "metodo": metodo_nuevo,
                      "wmape": _wmape(ev["reales"], ev["pred_nuevo"]),
                      "n_skus": len(codigos_interm), "es_nuevo": True})
        curva_mensual[SEGMENTO_INTERMITENTE_LABEL] = _curva_mensual(
            ev["meses"], ev["reales"], ev["pred_base"], ev["pred_nuevo"],
            BASELINE_LABEL, metodo_nuevo)

    # --- continuo/erratico/lumpy: baseline vs LightGBM pooled ------------
    codigos_pooled = seg.loc[seg["segmento"].isin(SEGMENTOS_POOLED), "codigo"].tolist()
    v_pool = ventas[ventas["codigo"].isin(codigos_pooled)]
    if not v_pool.empty:
        mes_max = v_pool["mes"].max()
        reales_b, preds_b, meses_b = [], [], []
        for _, g in v_pool.groupby("codigo"):
            serie = g.set_index("mes")["unidades"].resample("MS").sum()
            serie = serie.reindex(pd.date_range(serie.index.min(), mes_max, freq="MS"), fill_value=0.0)
            if len(serie) <= backtest + 3:
                continue
            y = np.clip(serie.values.astype(float), 0, None)
            reales_b.extend(y[-backtest:])
            meses_b.extend(serie.index[-backtest:])
            preds_b.extend(_baseline_tendencia_historico(pd.Series(y, index=serie.index), backtest))

        # LightGBM pooled ya calcula su propio WMAPE (y su curva mensual de
        # backtest, en attrs) sobre el mismo criterio (últimos `backtest`
        # meses) — se reusa en vez de reentrenar el modelo acá de nuevo.
        df_pooled = forecast_pooled(proyecto, horizonte=1, backtest=backtest,
                                    meses_min_historia=meses_min_historia)
        wmape_pooled = df_pooled.attrs.get("wmape")
        metodo_nuevo = ETIQUETA_METODO_NUEVO["pooled"]

        pred_lgbm_mensual = df_pooled.attrs.get("backtest_pred", pd.Series(dtype=float))
        pred_lgbm_mensual = pred_lgbm_mensual.copy()
        pred_lgbm_mensual.index = pd.to_datetime(pred_lgbm_mensual.index).strftime("%Y-%m")

        curva_pool = _curva_mensual(meses_b, reales_b, preds_b,
                                    [0.0] * len(meses_b), BASELINE_LABEL, metodo_nuevo)
        curva_pool[metodo_nuevo] = curva_pool["mes"].map(pred_lgbm_mensual).fillna(0.0)
        curva_mensual[SEGMENTO_POOLED_LABEL] = curva_pool

        filas.append({"segmento": SEGMENTO_POOLED_LABEL, "metodo": BASELINE_LABEL,
                      "wmape": _wmape(reales_b, preds_b),
                      "n_skus": len(codigos_pooled), "es_nuevo": False})
        filas.append({"segmento": SEGMENTO_POOLED_LABEL, "metodo": metodo_nuevo,
                      "wmape": wmape_pooled, "n_skus": len(codigos_pooled), "es_nuevo": True})

    res = pd.DataFrame(filas)
    res.attrs["curva_mensual"] = curva_mensual
    return res
