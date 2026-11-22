"""
core/forecasting/intermitente.py — Croston/TSB para SKUs de demanda intermitente

El segmento "intermitente" de `core/forecasting/segmentacion.py` (vende
salteado, pero cuando vende es una cantidad parecida) es justo el caso donde
los métodos clásicos de suavizado exponencial fallan: si un mes sin ventas
cuenta como una venta de 0 unidades, el nivel estimado queda permanentemente
sesgado a la baja. Croston separa la serie en "cuánto vende cuando vende" y
"cada cuánto vende", y suaviza cada cosa por separado — TSB es la variante
que corrige el sesgo de Croston cuando un SKU deja de venderse del todo
(Croston clásico nunca termina de bajar la estimación porque el intervalo
solo se actualiza en los meses CON venta).

Implementación a mano (no una librería como `statsforecast`): con ~2-5k SKUs
intermitentes y series de a lo sumo unas pocas decenas de meses, un loop
puro en Python/NumPy corre en menos de un segundo para todo el segmento —
no hace falta una librería vectorizada pensada para cientos de miles de
series, y evita el conflicto de versión de pandas que trae `statsforecast`
(fuerza `pandas<3.0.0`; este proyecto fija `pandas==3.0.3` a propósito, ver
requirements.txt). Mismo criterio que el resto de `core/forecasting/`: nada
de caja negra, se compite Croston vs. TSB por MAE de backtest y se expone
qué ganó.

Croston y TSB proyectan una TASA CONSTANTE por período (no tendencia ni
estacionalidad) — es lo esperable para demanda intermitente: no hay señal
suficiente para ajustar más que eso.

Uso:
    from core.forecasting.intermitente import forecast_intermitente

    df = forecast_intermitente("cliente_x", horizonte=6)
"""

import numpy as np
import pandas as pd

from core.comun import conectar
from core.forecasting.segmentacion import clasificar_demanda_sku, ventas_mensuales_por_sku

ALPHA_CROSTON = 0.1
ALPHA_D_TSB = 0.1
ALPHA_P_TSB = 0.1
MODELOS_INTERMITENTE = ("croston", "tsb")


def _nivel_croston(y: np.ndarray, alpha: float = ALPHA_CROSTON) -> float:
    """Croston clásico: suaviza por separado el tamaño de la venta y el
    intervalo entre ventas (en meses); nivel = tamaño_suavizado / intervalo_suavizado."""
    idx_venta = np.flatnonzero(y > 0)
    if len(idx_venta) == 0:
        return 0.0
    tamanos = y[idx_venta]
    intervalos = np.diff(np.concatenate(([-1], idx_venta)))
    z, q = float(tamanos[0]), float(intervalos[0])
    for i in range(1, len(tamanos)):
        z += alpha * (tamanos[i] - z)
        q += alpha * (intervalos[i] - q)
    return z / q if q > 0 else 0.0


def _nivel_tsb(y: np.ndarray, alpha_d: float = ALPHA_D_TSB,
               alpha_p: float = ALPHA_P_TSB) -> float:
    """TSB: suaviza la PROBABILIDAD de venta en cada período (se actualiza
    todos los meses, no solo cuando hay venta) y el tamaño de venta por
    separado; nivel = probabilidad_suavizada × tamaño_suavizado. A
    diferencia de Croston, sí converge a 0 si el SKU deja de venderse."""
    ocurre0 = y[0] > 0
    p, z = float(ocurre0), (float(y[0]) if ocurre0 else 0.0)
    for t in range(1, len(y)):
        ocurre = y[t] > 0
        p += alpha_p * (float(ocurre) - p)
        if ocurre:
            z += alpha_d * (y[t] - z)
    return p * z


FUNCIONES_INTERMITENTE = {"croston": _nivel_croston, "tsb": _nivel_tsb}


def elegir_modelo_intermitente(y: np.ndarray, backtest: int) -> tuple[str, dict]:
    """Backtest de holdout por MAE (no MAPE: con muchos meses en cero el
    error porcentual no está definido) sobre los últimos `backtest` meses.
    Gana el de menor MAE; con historia insuficiente para el holdout, o
    empate en cero, gana TSB por default (evita el sesgo conocido de
    Croston si el SKU se apagó)."""
    n = len(y)
    if not backtest or n <= backtest:
        return "tsb", {}
    train, real = y[:-backtest], y[-backtest:]
    maes = {}
    for nombre, fn in FUNCIONES_INTERMITENTE.items():
        nivel = fn(train)
        maes[nombre] = float(np.mean(np.abs(real - nivel)))
    ganador = min(maes, key=maes.get)
    return ganador, maes


def forecast_intermitente(
    proyecto: str, horizonte: int = 6, backtest: int = 3,
    meses_min_historia: int = 6,
) -> pd.DataFrame:
    """
    Proyección mensual (tasa constante) para los SKUs clasificados
    "intermitente" por `clasificar_demanda_sku`.

    Returns:
        DataFrame largo: codigo, mes, tipo ('real'/'forecast'), unidades,
        banda_inf, banda_sup. attrs: "modelo_elegido" (dict por SKU:
        "croston" o "tsb"), "mae_por_modelo" (dict por SKU).
    """
    seg = clasificar_demanda_sku(proyecto, meses_min_historia=meses_min_historia)
    codigos = seg.loc[seg["segmento"] == "intermitente", "codigo"].tolist()

    vacio = pd.DataFrame(columns=["codigo", "mes", "tipo", "unidades",
                                  "banda_inf", "banda_sup"])
    vacio.attrs["modelo_elegido"] = {}
    vacio.attrs["mae_por_modelo"] = {}
    if not codigos:
        return vacio

    conn = conectar(proyecto)
    try:
        ventas = ventas_mensuales_por_sku(conn)
    finally:
        conn.close()

    ventas = ventas[ventas["codigo"].isin(codigos)]
    if ventas.empty:
        return vacio
    mes_max = ventas["mes"].max()

    filas, modelos_elegidos, mae_por_modelo = [], {}, {}
    for codigo, g in ventas.groupby("codigo"):
        serie = (g.set_index("mes")["unidades"].resample("MS").sum())
        # extiende hasta el último mes disponible del proyecto (no del SKU):
        # los meses sin venta hasta hoy también son señal para TSB.
        serie = serie.reindex(pd.date_range(serie.index.min(), mes_max, freq="MS"),
                              fill_value=0.0)
        y = np.clip(serie.values.astype(float), 0, None)

        modelo, maes = elegir_modelo_intermitente(y, backtest)
        if maes:
            mae_por_modelo[str(codigo)] = maes
        modelos_elegidos[str(codigo)] = modelo
        nivel = FUNCIONES_INTERMITENTE[modelo](y)
        mae = maes.get(modelo, float(np.mean(np.abs(y - nivel))))

        meses_fut = pd.date_range(serie.index[-1] + pd.offsets.MonthBegin(1),
                                  periods=horizonte, freq="MS")
        for mes, valor in serie.items():
            filas.append({"codigo": codigo, "mes": mes, "tipo": "real",
                          "unidades": float(valor), "banda_inf": None, "banda_sup": None})
        for mes in meses_fut:
            filas.append({
                "codigo": codigo, "mes": mes, "tipo": "forecast",
                "unidades": nivel,
                "banda_inf": max(nivel - 1.96 * mae, 0.0),
                "banda_sup": nivel + 1.96 * mae,
            })

    res = pd.DataFrame(filas)
    res["mes"] = pd.to_datetime(res["mes"]).dt.strftime("%Y-%m")
    res = res.sort_values(["codigo", "mes"]).reset_index(drop=True)
    res.attrs["modelo_elegido"] = modelos_elegidos
    res.attrs["mae_por_modelo"] = mae_por_modelo
    return res
