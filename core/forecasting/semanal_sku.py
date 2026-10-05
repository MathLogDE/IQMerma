"""
core/forecasting/semanal_sku.py — Forecast semanal por SKU con Prophet
(filtrado por rubro/marca/sucursal desde afuera, comparación año a año)

Extiende el pipeline semanal de `semanal.py` (Prophet + feriados/eventos AR)
a nivel SKU individual — algo que ese módulo excluye explícitamente a nivel
"rubro"/"marca" por costo (ver su docstring: ~16 min con 141 rubros
completos). Acá el costo se acota desde afuera: quien llama ya filtró a una
lista chica de SKUs (por ejemplo, un rubro puntual) en vez de correr sobre
el catálogo completo — por eso `forecast_semanal_sku` exige la lista de
`codigos` como parámetro obligatorio, nunca "todos".

Multi-sucursal: Prophet ajusta por separado cada combinación (SKU,
sucursal) — no se suma la demanda antes de ajustar — y las curvas
resultantes (previsión y bandas) se suman semana a semana entre las
sucursales elegidas. Es más caro que sumar antes de ajustar, pero respeta
que cada sucursal puede tener una estacionalidad propia.

Comparación año a año: para cada semana futura se busca, por cada año hacia
atrás pedido, la semana con el mismo número de semana ISO en el año
correspondiente (no un offset fijo de 52 semanas) — estacionalmente más
correcto. Si esa semana no tiene datos (antes del inicio del histórico)
queda en None, nunca en 0, para no confundir "sin historia" con "no vendió".

Uso:
    from core.forecasting.semanal_sku import forecast_semanal_sku

    df = forecast_semanal_sku("cliente_x", codigos=["001", "002"],
                              codigodepos=["001", "003"], horizonte=14,
                              anios_comparacion=3)
"""

from datetime import date

import numpy as np
import pandas as pd

from core.comun import conectar
from core.forecasting.semanal import (
    _ajustar_prophet, _wmape, calendario_eventos_ar,
    MIN_SEMANAS, UMBRAL_WMAPE_EXCLUSION,
)

HORIZONTE_MAX = 52
ANIOS_COMPARACION_MAX = 5
# Tope de combinaciones (SKU × sucursal): cada una es una corrida de Prophet
# completa (backtest + final), con el mismo overhead de subproceso cmdstan
# que ya documenta semanal.py — sin tope, un filtro demasiado amplio puede
# tildar la página varios minutos.
LIMITE_COMBINACIONES = 150

_COLUMNAS_VACIO = ["codigo", "descripcion", "rubro", "marca", "semana",
                   "tipo", "anio_offset", "unidades", "banda_inf", "banda_sup"]


def _serie_semanal_sku(conn, codigos: list[str],
                       codigodepos: list[str] | None) -> pd.DataFrame:
    """Serie semanal real por (codigo, codigodepo) — mismo patrón SQL que
    `_serie_semanal` de semanal.py, indexada por SKU en vez de por grupo
    agregado."""
    filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if codigodepos else ""
    params = {"codigos": codigos}
    if codigodepos:
        params["depos"] = codigodepos

    sql = f"""
        SELECT date_trunc('week', m.fecha) AS semana,
               m.codigo, m.codigodepo, -SUM(m.diferencia) AS unidades
        FROM movimientos m
        JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
        WHERE m.codigo IN (SELECT unnest($codigos)) {filtro_depo}
        GROUP BY 1, 2, 3
    """
    df = conn.execute(sql, params).df()
    if df.empty:
        return pd.DataFrame(columns=["semana", "codigo", "codigodepo", "unidades"])
    df["semana"] = pd.to_datetime(df["semana"])
    return df


def _stock_actual_sku(conn, codigos: list[str],
                      codigodepos: list[str] | None) -> dict:
    """Último stock disponible por SKU, al snapshot más reciente cargado en
    `stock_sucursal`, sumado entre las sucursales elegidas — o entre todas
    las sucursales si no se filtró ninguna (mismo criterio de `codigodepos`
    que el resto del módulo). Mismo patrón que `_stock` en demanda.py."""
    fs = conn.execute("SELECT MAX(fecha_snapshot) FROM stock_sucursal").fetchone()[0]
    if fs is None:
        return {}
    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if codigodepos else ""
    params = {"codigos": codigos, "fs": str(fs)}
    if codigodepos:
        params["depos"] = codigodepos
    df = conn.execute(f"""
        SELECT codigo, SUM(stock)::DOUBLE AS stock
        FROM stock_sucursal
        WHERE codigo IN (SELECT unnest($codigos)) {filtro_depo}
          AND fecha_snapshot = $fs::DATE
        GROUP BY 1
    """, params).df()
    return df.set_index("codigo")["stock"].to_dict()


def _semana_equivalente_anio_anterior(fecha_semana: pd.Timestamp, anios_atras: int) -> date:
    """Fecha con el mismo número de semana ISO y mismo día de semana que
    `fecha_semana`, `anios_atras` años antes (respeta el ancla de semana que
    use el resample aguas arriba, sea lunes o domingo). Si el año objetivo
    no llega a tener esa semana (semana 53 en un año de 52), cae a la semana
    anterior — preferible a perder el dato entero por un borde de calendario
    poco frecuente."""
    iso_year, iso_week, iso_weekday = fecha_semana.isocalendar()
    objetivo_year = iso_year - anios_atras
    try:
        return date.fromisocalendar(objetivo_year, iso_week, iso_weekday)
    except ValueError:
        return date.fromisocalendar(objetivo_year, iso_week - 1, iso_weekday)


def forecast_semanal_sku(
    proyecto: str,
    codigos: list[str],
    codigodepos: list[str] | None = None,
    horizonte: int = 14,
    anios_comparacion: int = 3,
    backtest: int = 12,
) -> pd.DataFrame:
    """
    Proyección SEMANAL por SKU con Prophet (mismo calendario de feriados y
    eventos AR que `semanal.forecast_ventas_semanal`), junto con la demanda
    real de la semana ISO equivalente en cada uno de los `anios_comparacion`
    años anteriores — para decidir compras/distribución con la estacionalidad
    a la vista, no solo el promedio.

    `codigodepos=None` o `[]`: cada SKU se ajusta una sola vez sobre la
    demanda de todas las sucursales sumada (igual al `codigodepo=None` de
    semanal.py). Con una o más sucursales, se ajusta Prophet por separado
    para cada (SKU, sucursal) y se suman las curvas resultantes semana a
    semana entre las sucursales elegidas (ver docstring del módulo).

    Levanta ValueError si `len(codigos) * max(len(codigodepos), 1)` supera
    `LIMITE_COMBINACIONES` — quien llama debe acotar el filtro antes.

    Returns:
        DataFrame tidy: codigo, descripcion, rubro, marca, semana (fecha,
        "YYYY-MM-DD"), tipo ('prevista'|'historico'), anio_offset
        (0=prevista, 1..n=histórico), unidades (None si esa semana no tiene
        historia), banda_inf, banda_sup (solo con tipo='prevista').
        attrs: "wmape" (dict por codigo, backtest), "excluidos" (SKUs o
        combinaciones SKU×sucursal sin proyección y motivo),
        "combinaciones_calculadas" (SKU×sucursal efectivamente ajustadas),
        "stock_actual" (dict por codigo: último stock disponible, sumado
        entre las sucursales elegidas o todas si no se filtró ninguna).
    """
    if not codigos:
        raise ValueError("codigos no puede estar vacío")
    if horizonte < 1 or horizonte > HORIZONTE_MAX:
        raise ValueError(f"horizonte debe estar entre 1 y {HORIZONTE_MAX}")
    if anios_comparacion < 0 or anios_comparacion > ANIOS_COMPARACION_MAX:
        raise ValueError(f"anios_comparacion debe estar entre 0 y {ANIOS_COMPARACION_MAX}")

    n_combinaciones = len(codigos) * max(len(codigodepos or []), 1)
    if n_combinaciones > LIMITE_COMBINACIONES:
        raise ValueError(
            f"{n_combinaciones} combinaciones SKU×sucursal superan el límite de "
            f"{LIMITE_COMBINACIONES} — acotá el filtro de rubro/marca o de sucursales."
        )

    conn = conectar(proyecto)
    try:
        base = _serie_semanal_sku(conn, codigos, codigodepos)
        cat = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos "
            "WHERE codigo IN (SELECT unnest($codigos))", {"codigos": codigos},
        ).df()
        stock_actual = _stock_actual_sku(conn, codigos, codigodepos)
    finally:
        conn.close()

    vacio = pd.DataFrame(columns=_COLUMNAS_VACIO)
    vacio.attrs.update(wmape={}, excluidos=[], combinaciones_calculadas=0,
                       stock_actual=stock_actual)
    if base.empty:
        return vacio

    anio_desde = int(base["semana"].min().year) - anios_comparacion
    anio_hasta = int(base["semana"].max().year) + 1 + (horizonte // 52 + 1)
    eventos = calendario_eventos_ar(anio_desde, anio_hasta)
    info_cat = cat.set_index("codigo")

    filas, wmapes, excluidos = [], {}, []
    calculadas = 0

    for codigo in codigos:
        base_codigo = base[base["codigo"] == codigo]
        if base_codigo.empty:
            excluidos.append(f"{codigo} (sin ventas)")
            continue

        pred_total, historico_total = None, None
        wmape_partes = []

        for depo in (codigodepos if codigodepos else [None]):
            g = base_codigo if depo is None else base_codigo[base_codigo["codigodepo"] == depo]
            serie = g.set_index("semana")["unidades"].resample("W").sum().fillna(0.0)
            etiqueta = codigo if depo is None else f"{codigo}|{depo}"
            n = len(serie)
            if n < MIN_SEMANAS:
                excluidos.append(f"{etiqueta} (< {MIN_SEMANAS} semanas de historia)")
                continue

            if backtest and n > MIN_SEMANAS + backtest:
                train_bt = serie.iloc[:-backtest]
                pred_bt = _ajustar_prophet(train_bt, backtest, eventos)
                pred_bt_fut = pred_bt[pred_bt.index > train_bt.index.max()]
                w = _wmape(serie.iloc[-backtest:].values, pred_bt_fut["yhat"].values)
                if w is not None:
                    wmape_partes.append(w)
                    if w > UMBRAL_WMAPE_EXCLUSION:
                        excluidos.append(f"{etiqueta} (WMAPE backtest {w:.0f}%)")
                        continue

            pred = _ajustar_prophet(serie, horizonte, eventos)
            pred_fut = pred[pred.index > serie.index.max()][["yhat", "yhat_lower", "yhat_upper"]]
            calculadas += 1

            pred_total = pred_fut if pred_total is None else pred_total.add(pred_fut, fill_value=0.0)
            hist_serie = serie.to_frame(name="unidades")
            historico_total = (hist_serie if historico_total is None
                               else historico_total.add(hist_serie, fill_value=0.0))

        if pred_total is None:
            continue
        if wmape_partes:
            wmapes[codigo] = float(np.mean(wmape_partes))

        info = info_cat.loc[codigo] if codigo in info_cat.index else None
        descripcion = info["descripcion"] if info is not None else ""
        rubro = info["rubro"] if info is not None else ""
        marca = info["marca"] if info is not None else ""

        for semana, row in pred_total.iterrows():
            filas.append({
                "codigo": codigo, "descripcion": descripcion, "rubro": rubro, "marca": marca,
                "semana": semana, "tipo": "prevista", "anio_offset": 0,
                "unidades": float(row["yhat"]),
                "banda_inf": float(row["yhat_lower"]), "banda_sup": float(row["yhat_upper"]),
            })
            for offset in range(1, anios_comparacion + 1):
                fecha_equiv = pd.Timestamp(_semana_equivalente_anio_anterior(semana, offset))
                valor_hist = (
                    float(historico_total.loc[fecha_equiv, "unidades"])
                    if fecha_equiv in historico_total.index else None
                )
                filas.append({
                    "codigo": codigo, "descripcion": descripcion, "rubro": rubro, "marca": marca,
                    "semana": semana, "tipo": "historico", "anio_offset": offset,
                    "unidades": valor_hist, "banda_inf": None, "banda_sup": None,
                })

    res = pd.DataFrame(filas, columns=_COLUMNAS_VACIO)
    if not res.empty:
        res["semana"] = pd.to_datetime(res["semana"]).dt.strftime("%Y-%m-%d")
        res = res.sort_values(["codigo", "semana", "anio_offset"]).reset_index(drop=True)
    res.attrs["wmape"] = wmapes
    res.attrs["excluidos"] = excluidos
    res.attrs["combinaciones_calculadas"] = calculadas
    res.attrs["stock_actual"] = stock_actual
    return res
