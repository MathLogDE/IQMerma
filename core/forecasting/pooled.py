"""
core/forecasting/pooled.py — LightGBM pooled para continuo/errático/lumpy

Los segmentos "continuo", "erratico" y "lumpy" de
`core/forecasting/segmentacion.py` (~17k SKUs en el proyecto real) no tienen
suficiente señal PROPIA como para ajustarles un modelo de serie temporal uno
por uno (menos aún los erráticos/lumpy — ver docstring de `segmentacion.py`).
La apuesta acá es distinta: en vez de miles de modelos individuales, se
entrena UN SOLO modelo global (LightGBM, objetivo "tweedie" — pensado para
targets no negativos con masa en cero, el mismo enfoque que usaron las
soluciones ganadoras de competencias de forecasting retail como M5/
Corporación Favorita) sobre el panel SKU×mes completo. El SKU en sí NO entra
como feature categórica (con ~17k valores distintos degradaría los splits y
el tiempo de entrenamiento) — su identidad la aportan los lags y medias
móviles de su propia historia; lo que sí generaliza entre SKUs son rubro,
marca, precio, mes calendario y antigüedad.

A propósito NO se usa `mlforecast` (el wrapper de Nixtla para este patrón):
fuerza `pandas<3.0.0`, igual que `statsforecast` — ver
`core/forecasting/intermitente.py` para el mismo problema ya encontrado con
Croston/TSB. El panel (lags, medias móviles) se arma a mano con pandas
vectorizado (groupby + shift/rolling), sin loop por SKU.

Proyección multi-paso RECURSIVA: para cada mes futuro se predice con las
features calculadas de la historia real + lo ya predicho en los meses
anteriores del horizonte, y se lo agrega a la historia para el siguiente
paso — es la forma estándar de extender un modelo "un paso adelante" a un
horizonte de varios meses.

Uso:
    from core.forecasting.pooled import forecast_pooled

    df = forecast_pooled("cliente_x", horizonte=6)
"""

import numpy as np
import pandas as pd
import lightgbm as lgb

from core.comun import conectar, catalogo_articulos, precios_snapshot
from core.forecasting.segmentacion import clasificar_demanda_sku, ventas_mensuales_por_sku

SEGMENTOS_POOLED = ("continuo", "erratico", "lumpy")
LAGS = (1, 2, 3, 6, 12)
VENTANAS_MEDIA = (3, 6)
FEATURES_CAT = ["rubro", "marca", "mes_num"]
FEATURES_NUM = [f"lag_{k}" for k in LAGS] + [f"roll_mean_{w}" for w in VENTANAS_MEDIA] \
    + ["antiguedad", "precio"]
FEATURES = FEATURES_NUM + FEATURES_CAT

PARAMS_LGBM = dict(
    objective="tweedie", tweedie_variance_power=1.2,
    n_estimators=300, learning_rate=0.05, num_leaves=63,
    min_child_samples=20, verbosity=-1,
)


def _panel_mensual(codigos: list[str], ventas: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Panel largo codigo×mes (unidades, 0 en meses sin venta) desde el
    primer mes con venta de cada SKU hasta el último mes disponible del
    proyecto — vectorizado (sin loop por SKU)."""
    ventas = ventas[ventas["codigo"].isin(codigos)]
    mes_max = ventas["mes"].max()
    meses = pd.date_range(ventas["mes"].min(), mes_max, freq="MS")

    grid = pd.MultiIndex.from_product(
        [ventas["codigo"].unique(), meses], names=["codigo", "mes"]).to_frame(index=False)
    panel = grid.merge(ventas[["codigo", "mes", "unidades"]], on=["codigo", "mes"], how="left")
    # clip a piso 0: un mes con más devoluciones/anulaciones que ventas puede
    # dar diferencia neta negativa — el objetivo tweedie exige target >= 0,
    # y no tiene sentido "proyectar" una venta negativa.
    panel["unidades"] = panel["unidades"].fillna(0.0).clip(lower=0.0)

    primera_venta = ventas.groupby("codigo")["mes"].min().rename("primera_venta")
    panel = panel.merge(primera_venta, on="codigo", how="left")
    panel = panel[panel["mes"] >= panel["primera_venta"]].drop(columns="primera_venta")
    return panel.sort_values(["codigo", "mes"]).reset_index(drop=True), mes_max


def _agregar_features(panel: pd.DataFrame, catalogo: pd.DataFrame,
                      precios: pd.DataFrame) -> pd.DataFrame:
    panel = panel.merge(catalogo[["codigo", "rubro", "marca"]], on="codigo", how="left")
    precio_map = precios.set_index("codigo")["lista_1"].astype(float)
    panel["precio"] = panel["codigo"].map(precio_map).fillna(0.0)
    panel["mes_num"] = panel["mes"].dt.month.astype(str)
    panel["antiguedad"] = panel.groupby("codigo").cumcount()

    g = panel.groupby("codigo")["unidades"]
    for k in LAGS:
        panel[f"lag_{k}"] = g.shift(k)
    for w in VENTANAS_MEDIA:
        panel[f"roll_mean_{w}"] = g.transform(
            lambda s, w=w: s.shift(1).rolling(w, min_periods=1).mean())

    for c in FEATURES_CAT:
        panel[c] = panel[c].astype("category")
    return panel


def forecast_pooled(
    proyecto: str, horizonte: int = 6, backtest: int = 3,
    meses_min_historia: int = 6,
) -> pd.DataFrame:
    """
    Proyección mensual (LightGBM pooled) para los SKUs clasificados
    "continuo" / "erratico" / "lumpy" por `clasificar_demanda_sku`.

    Returns:
        DataFrame largo: codigo, mes, tipo ('real'/'forecast'), unidades,
        banda_inf, banda_sup. attrs: "wmape" (WMAPE de backtest, global,
        sum(|error|)/sum(real) — más estable que MAPE con series en cero),
        "n_skus", "importancia_features" (dict, para auditar qué pesa),
        "backtest_real"/"backtest_pred" (Series mensuales, suma real vs.
        predicha en el holdout del backtest — para graficar la curva de
        evaluación comparativa sin reentrenar, ver `evaluacion.py`).
    """
    seg = clasificar_demanda_sku(proyecto, meses_min_historia=meses_min_historia)
    codigos = seg.loc[seg["segmento"].isin(SEGMENTOS_POOLED), "codigo"].tolist()

    vacio = pd.DataFrame(columns=["codigo", "mes", "tipo", "unidades",
                                  "banda_inf", "banda_sup"])
    vacio.attrs.update(wmape=None, n_skus=0, importancia_features={})
    if not codigos:
        return vacio

    conn = conectar(proyecto)
    try:
        ventas = ventas_mensuales_por_sku(conn)
        precios = precios_snapshot(conn, None)
    finally:
        conn.close()

    panel, mes_max = _panel_mensual(codigos, ventas)
    if panel.empty:
        return vacio

    catalogo = catalogo_articulos(proyecto)[["codigo", "rubro", "marca"]]
    panel = _agregar_features(panel, catalogo, precios)

    meses_todos = sorted(panel["mes"].unique())
    meses_holdout = meses_todos[-backtest:] if backtest and len(meses_todos) > backtest + 6 else []
    train = panel[~panel["mes"].isin(meses_holdout)]

    wmape = None
    # curva mensual agregada del backtest (real vs. predicho) — para poder
    # graficarla en la UI (Fase 5, evaluación comparativa) sin reentrenar.
    backtest_real_mensual, backtest_pred_mensual = pd.Series(dtype=float), pd.Series(dtype=float)
    # sigma POR SKU (no uno global): con ~17k SKUs de escalas muy distintas
    # (de <1 a cientos de unidades/mes), un solo desvío global infla la
    # banda de los SKUs chicos con el ruido de los grandes.
    sigma_por_sku = pd.Series(dtype=float)
    if meses_holdout:
        modelo_bt = lgb.LGBMRegressor(**PARAMS_LGBM)
        modelo_bt.fit(train[FEATURES], train["unidades"], categorical_feature=FEATURES_CAT)
        test = panel[panel["mes"].isin(meses_holdout)].copy()
        test["pred"] = np.clip(modelo_bt.predict(test[FEATURES]), 0, None)
        test["error"] = test["unidades"] - test["pred"]
        if test["unidades"].sum() > 0:
            wmape = float(test["error"].abs().sum() / test["unidades"].sum() * 100)
        sigma_por_sku = test.groupby("codigo")["error"].agg(
            lambda s: float(s.std(ddof=1)) if len(s) > 1 else float(s.abs().iloc[0]))
        backtest_real_mensual = test.groupby("mes")["unidades"].sum()
        backtest_pred_mensual = test.groupby("mes")["pred"].sum()

    modelo = lgb.LGBMRegressor(**PARAMS_LGBM)
    modelo.fit(panel[FEATURES], panel["unidades"], categorical_feature=FEATURES_CAT)
    importancia = dict(zip(FEATURES, modelo.feature_importances_.tolist()))

    # --- proyección recursiva multi-paso ------------------------------
    cat_dtypes = {c: panel[c].dtype for c in FEATURES_CAT}
    meta = (panel[["codigo", "rubro", "marca", "precio"]].drop_duplicates("codigo")
            .set_index("codigo"))
    antiguedad_max = panel.groupby("codigo")["antiguedad"].max()

    # pivot deja NaN donde un SKU todavía no existía (panel ya viene
    # recortado desde su primer mes con venta) — no confunde "no existía
    # todavía" con "existía y no vendió" al calcular lags/medias.
    historia = panel.pivot_table(index="codigo", columns="mes", values="unidades", fill_value=np.nan)

    real_df = panel[["codigo", "mes", "unidades"]].copy()
    real_df["tipo"] = "real"
    real_df["banda_inf"] = np.nan
    real_df["banda_sup"] = np.nan

    forecast_frames = []
    for paso in range(1, horizonte + 1):
        nuevo_mes = mes_max + pd.offsets.MonthBegin(paso)
        X = pd.DataFrame(index=historia.index)
        for k in LAGS:
            X[f"lag_{k}"] = historia.iloc[:, -k] if historia.shape[1] >= k else np.nan
        for w in VENTANAS_MEDIA:
            X[f"roll_mean_{w}"] = historia.iloc[:, -w:].mean(axis=1, skipna=True)
        X["antiguedad"] = antiguedad_max + paso
        X["precio"] = meta["precio"]
        X["rubro"] = meta["rubro"].astype(cat_dtypes["rubro"])
        X["marca"] = meta["marca"].astype(cat_dtypes["marca"])
        X["mes_num"] = pd.Series(str(nuevo_mes.month), index=X.index).astype(cat_dtypes["mes_num"])

        pred = np.clip(modelo.predict(X[FEATURES]), 0, None)
        historia[nuevo_mes] = pred

        # sigma por SKU si hubo backtest para ese código; si no (SKU muy
        # nuevo, sin meses en el holdout), banda ancha relativa al propio
        # nivel proyectado — mismo criterio que el préstamo kNN de
        # demanda.py (`nivel * 0.75`) para señal débil.
        sigma_sku = sigma_por_sku.reindex(historia.index)
        sigma_sku = sigma_sku.fillna(pd.Series(pred, index=historia.index) * 0.75).values

        forecast_frames.append(pd.DataFrame({
            "codigo": historia.index, "mes": nuevo_mes, "tipo": "forecast",
            "unidades": pred,
            "banda_inf": np.clip(pred - 1.96 * sigma_sku, 0, None),
            "banda_sup": pred + 1.96 * sigma_sku,
        }))

    res = pd.concat([real_df] + forecast_frames, ignore_index=True)
    res["mes"] = pd.to_datetime(res["mes"]).dt.strftime("%Y-%m")
    res = res.sort_values(["codigo", "mes"]).reset_index(drop=True)
    res.attrs["wmape"] = wmape
    res.attrs["n_skus"] = len(codigos)
    res.attrs["importancia_features"] = importancia
    res.attrs["backtest_real"] = backtest_real_mensual
    res.attrs["backtest_pred"] = backtest_pred_mensual
    return res
