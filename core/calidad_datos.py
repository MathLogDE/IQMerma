"""
core/calidad_datos.py — Calidad de la ingesta: días anómalos por sucursal

Compara, para cada sucursal, cuántos movimientos tuvo cada día contra su
propia mediana histórica del mismo día de la semana (ventana móvil
centrada). Un día muy por debajo de lo esperado es la huella típica de un
gap de ingesta del ERP para esa sucursal — el mismo patrón que llevó a
encontrar el caso de Rodríguez Busto / SKU 2969010 (ver
`core/inventario/series.py`): un offset constante entre el stock
reconstruido y el snapshot real que después se resuelve sin ningún
movimiento que lo explique.

Este chequeo es deliberadamente más liviano que ese: no reconstruye stock
por SKU, solo cuenta filas de `movimientos` por sucursal y día. Sirve para
encontrar CANDIDATOS a revisar, no para confirmar el gap — para confirmarlo
hay que mirar el SKU puntual, cruzando snapshots de `stock_sucursal` contra
la reconstrucción (a mano, como se hizo en ese caso).

La mediana local siempre se calcula sobre TODO el histórico de movimientos
disponible (más estable en los bordes del rango pedido, sin necesitar
padding manual) — el resultado recién se filtra a [fecha_desde, fecha_hasta]
al final.

Uso:
    from core.calidad_datos import detectar_dias_anomalos, resumen_dias_anomalos

    df = detectar_dias_anomalos("cliente_x", "2026-01-01", "2026-09-14")
    resumen = resumen_dias_anomalos(df)
"""

import pandas as pd

from core.comun import conectar

# Ocurrencias del mismo día de semana (a ambos lados) que entran en la
# mediana móvil centrada — 9 ~ 4 semanas antes y después.
VENTANA_OCURRENCIAS = 9
RATIO_UMBRAL = 0.15
# Ignora sucursales/días cuya mediana local sea menor a esto: evita ruido en
# sucursales chicas donde bajar de 5 a 2 movimientos ya es "normal".
MEDIANA_MINIMA = 30.0
# Hasta cuántas sucursales afectadas el mismo día se considera un gap
# "aislado" de esa sucursal — más que eso suele ser feriado o corte general.
MAX_SUCURSALES_AISLADO = 2

_COLS = ["fecha", "codigodepo", "nombre", "movimientos",
         "mediana_local", "ratio", "sucursales_afectadas_ese_dia"]


def detectar_dias_anomalos(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    ratio_umbral: float = RATIO_UMBRAL,
    mediana_minima: float = MEDIANA_MINIMA,
    ventana_ocurrencias: int = VENTANA_OCURRENCIAS,
) -> pd.DataFrame:
    """
    Detecta combinaciones (sucursal, día) con muchos menos movimientos de lo
    esperado dentro de [fecha_desde, fecha_hasta].

    Args:
        ratio_umbral:        se marca "anómalo" si movimientos/mediana_local
                              <= este umbral (default 0.15 = 15% de lo
                              esperado).
        mediana_minima:       ignora sucursales/días cuya mediana local sea
                              menor a esto.
        ventana_ocurrencias:  ocurrencias del mismo día de semana (a ambos
                              lados) que entran en la mediana móvil.

    Returns:
        DataFrame: fecha, codigodepo, nombre, movimientos, mediana_local,
        ratio, sucursales_afectadas_ese_dia (cuántas sucursales distintas
        dieron anómalas ese mismo día — un número alto suele ser feriado o
        corte general de toda la cadena, no un gap puntual de esa sucursal).
        attrs: desde, hasta, ratio_umbral, mediana_minima.
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    attrs = dict(desde=desde, hasta=hasta, ratio_umbral=ratio_umbral,
                mediana_minima=mediana_minima)

    conn = conectar(proyecto)
    try:
        base = conn.execute("""
            SELECT codigodepo, fecha, COUNT(*)::DOUBLE AS movimientos
            FROM movimientos GROUP BY 1, 2
        """).df()
        deps = conn.execute("SELECT codigodepo, nombre FROM depositos").df()
    finally:
        conn.close()

    if base.empty:
        vacio = pd.DataFrame(columns=_COLS)
        vacio.attrs.update(attrs)
        return vacio

    base["fecha"] = pd.to_datetime(base["fecha"])
    todas_fechas = pd.date_range(base["fecha"].min(), base["fecha"].max(), freq="D")
    todas_deps = base["codigodepo"].unique()
    grilla = pd.MultiIndex.from_product(
        [todas_deps, todas_fechas], names=["codigodepo", "fecha"]).to_frame(index=False)
    base = grilla.merge(base, on=["codigodepo", "fecha"], how="left").fillna({"movimientos": 0.0})
    base["dow"] = base["fecha"].dt.dayofweek

    partes = []
    for dep, g in base.groupby("codigodepo"):
        g = g.sort_values("fecha").set_index("fecha")
        for _, gg in g.groupby("dow"):
            mediana = gg["movimientos"].rolling(
                window=ventana_ocurrencias, center=True,
                min_periods=max(3, ventana_ocurrencias // 2)).median()
            partes.append(gg.assign(mediana_local=mediana).reset_index()
                         [["fecha", "movimientos", "mediana_local"]].assign(codigodepo=dep))

    out = pd.concat(partes, ignore_index=True)
    out["ratio"] = out["movimientos"] / out["mediana_local"]

    anom = out[(out["mediana_local"] >= mediana_minima) & (out["ratio"] <= ratio_umbral)].copy()
    anom = anom[(anom["fecha"] >= desde) & (anom["fecha"] <= hasta)]
    if anom.empty:
        vacio = pd.DataFrame(columns=_COLS)
        vacio.attrs.update(attrs)
        return vacio

    afectadas = anom.groupby("fecha")["codigodepo"].nunique()
    anom["sucursales_afectadas_ese_dia"] = anom["fecha"].map(afectadas)
    nombres = deps.drop_duplicates("codigodepo").set_index("codigodepo")["nombre"]
    anom["nombre"] = anom["codigodepo"].map(nombres).fillna(anom["codigodepo"])

    anom = anom[_COLS].sort_values(["fecha", "codigodepo"]).reset_index(drop=True)
    anom.attrs.update(attrs)
    return anom


def resumen_dias_anomalos(df: pd.DataFrame) -> dict:
    """
    KPIs agregados de `detectar_dias_anomalos`: aisladas (afectan hasta
    `MAX_SUCURSALES_AISLADO` sucursales el mismo día — el patrón más parecido
    a un gap de ingesta puntual) vs. amplias (probablemente feriado o corte
    general), y cuántas sucursales/días distintos aparecen en cada una.
    """
    if df.empty:
        return dict(total=0, aisladas=0, dias_amplios=0,
                    sucursales_afectadas=0, dias_afectados=0)
    aisladas = df[df["sucursales_afectadas_ese_dia"] <= MAX_SUCURSALES_AISLADO]
    amplias = df[df["sucursales_afectadas_ese_dia"] > MAX_SUCURSALES_AISLADO]
    return dict(
        total=len(df),
        aisladas=len(aisladas),
        dias_amplios=amplias["fecha"].nunique(),
        sucursales_afectadas=df["codigodepo"].nunique(),
        dias_afectados=df["fecha"].nunique(),
    )
