"""
core/forecasting/segmentacion.py — Clasificación de demanda por SKU

A nivel agregado (`core/forecasting/demanda.py`) hay suficiente señal para
ajustar un modelo de serie temporal. A nivel SKU individual no todos los
artículos tienen el mismo tipo de demanda, y forzar un solo enfoque (o
descartarlos a todos por "erráticos") deja plata en la mesa: hay un segmento
razonable de SKUs con demanda continua donde un modelo de serie temporal SÍ
tiene sentido, y otro de demanda intermitente donde métodos como Croston/TSB
rinden mejor que un promedio móvil.

Método: clasificación ADI / CV² (Syntetos & Boylan, 2005), el estándar de la
industria para decidir qué familia de modelo de demanda usar por SKU:

  - ADI (Average Demand Interval): meses transcurridos entre la primera y la
    última venta, dividido la cantidad de meses con venta > 0. ADI≈1 significa
    que vende casi todos los meses; ADI alto significa que pasa muchos meses
    sin ventas entre una venta y la siguiente.
  - CV² (coeficiente de variación al cuadrado): variabilidad del TAMAÑO de
    venta en los meses que sí vendió. CV² bajo significa cantidades parecidas
    mes a mes; alto significa que a veces vende 2 y a veces 200.

Con los umbrales estándar (ADI=1.32, CV²=0.49) selecciona uno de 4 segmentos:

  - "continuo" (ADI bajo, CV² bajo):    vende seguido y en cantidades
                                         parecidas — un modelo de serie
                                         temporal por SKU o agrupado tiene
                                         señal real para ajustar.
  - "intermitente" (ADI alto, CV² bajo): vende salteado pero cuando vende es
                                         siempre una cantidad similar —
                                         candidato a Croston/TSB.
  - "erratico" (ADI bajo, CV² alto):     vende seguido pero en cantidades muy
                                         variables.
  - "lumpy" (ADI alto, CV² alto):        vende salteado Y en cantidades muy
                                         variables — el caso más difícil,
                                         ningún método clásico de una sola
                                         serie rinde bien acá (ver roadmap:
                                         se ataca con un modelo pooled, no
                                         por SKU).

SKUs con menos de `meses_min_historia` meses con venta no se clasifican en
ninguno de los 4 (ADI/CV² con pocos puntos no es confiable) — quedan aparte
como "sin_historia_suficiente". Los que nunca vendieron quedan "sin_ventas".

Uso:
    from core.forecasting.segmentacion import clasificar_demanda_sku

    df = clasificar_demanda_sku("cliente_x")
    df["segmento"].value_counts()
"""

import numpy as np
import pandas as pd

from core.comun import conectar, catalogo_articulos

SEGMENTOS = ("continuo", "intermitente", "erratico", "lumpy",
             "sin_historia_suficiente", "sin_ventas")

# Umbrales estándar Syntetos & Boylan (2005) para clasificar el tipo de
# demanda de un SKU y elegir la familia de modelo que le corresponde.
ADI_CORTE = 1.32
CV2_CORTE = 0.49


def ventas_mensuales_por_sku(conn) -> pd.DataFrame:
    """Unidades vendidas por mes y SKU, agregando todas las sucursales.
    Pública porque también la usa `core/forecasting/intermitente.py`."""
    return conn.execute("""
        SELECT m.codigo, date_trunc('month', m.fecha) AS mes,
               -SUM(m.diferencia) AS unidades
        FROM movimientos m
        JOIN tipos_categoria t ON m.tipo = t.tipo AND t.es_venta
        GROUP BY 1, 2
    """).df()


def clasificar_demanda_sku(proyecto: str, meses_min_historia: int = 6) -> pd.DataFrame:
    """
    Clasifica cada SKU del catálogo por patrón de demanda (ADI/CV²).

    Args:
        proyecto:          Nombre del proyecto.
        meses_min_historia: mínimo de meses con venta > 0 para confiar en la
                            clasificación (con menos, ADI/CV² es ruido).

    Returns:
        DataFrame por SKU: codigo, descripcion, rubro, marca, segmento,
        adi, cv2, meses_con_venta, meses_ventana (meses entre la primera y la
        última venta). `adi`/`cv2` son NA para "sin_historia_suficiente" y
        "sin_ventas".
    """
    conn = conectar(proyecto)
    try:
        df = ventas_mensuales_por_sku(conn)
    finally:
        conn.close()

    cat = catalogo_articulos(proyecto)[["codigo", "descripcion", "rubro", "marca"]]

    if df.empty:
        out = cat.copy()
        out["segmento"] = "sin_ventas"
        for c in ("adi", "cv2", "meses_con_venta", "meses_ventana"):
            out[c] = pd.NA
        return out

    con_venta = df[df["unidades"] > 0]
    resumen = (con_venta.groupby("codigo")
               .agg(meses_con_venta=("unidades", "size"),
                    media=("unidades", "mean"),
                    desvio=("unidades", "std"),
                    desde=("mes", "min"),
                    hasta=("mes", "max"))
               .reset_index())
    resumen["meses_ventana"] = (
        (resumen["hasta"].dt.to_period("M") - resumen["desde"].dt.to_period("M"))
        .apply(lambda p: p.n) + 1
    )
    resumen["adi"] = resumen["meses_ventana"] / resumen["meses_con_venta"]
    # Un solo mes con venta no tiene desvío definido (std de 1 dato = NaN):
    # CV2 = 0 (sin variabilidad observada), no "desconocido".
    resumen["cv2"] = np.where(
        resumen["meses_con_venta"] > 1,
        (resumen["desvio"] / resumen["media"]) ** 2,
        0.0,
    )

    out = cat.merge(
        resumen[["codigo", "adi", "cv2", "meses_con_venta", "meses_ventana"]],
        on="codigo", how="left",
    )

    tiene_venta = out["meses_con_venta"].notna()
    suficiente = tiene_venta & (out["meses_con_venta"] >= meses_min_historia)

    out["segmento"] = "sin_ventas"
    out.loc[tiene_venta & ~suficiente, "segmento"] = "sin_historia_suficiente"

    adi_bajo = out["adi"] <= ADI_CORTE
    cv2_bajo = out["cv2"] <= CV2_CORTE
    out.loc[suficiente & adi_bajo & cv2_bajo, "segmento"] = "continuo"
    out.loc[suficiente & ~adi_bajo & cv2_bajo, "segmento"] = "intermitente"
    out.loc[suficiente & adi_bajo & ~cv2_bajo, "segmento"] = "erratico"
    out.loc[suficiente & ~adi_bajo & ~cv2_bajo, "segmento"] = "lumpy"

    return out
