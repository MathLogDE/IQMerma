"""
core/inventario/reposicion.py — Perfil de reposición por SKU

Cruza reposición (remitos), ventas y quiebre a nivel de SKU, para un recorte
de catálogo y una sucursal: cada cuánto llega un remito, con qué cantidad,
cuánto se vende, cuántos días se pasa en quiebre o por debajo de una barrera
elegida, y —lo nuevo— cuánto tarda en cruzar esa barrera contando desde el
último remito recibido. Sirve para priorizar qué reponer primero dentro de
una marca o rubro.

No reinventa la reconstrucción de stock: se apoya en
`core.inventario.series.serie_stock` / `resumen_quiebres` /
`marcadores_movimiento`. Lo único nuevo acá es la cadencia de remitos
vectorizada por SKU, el query de ventas por código, y el escaneo hacia
adelante que mide el tiempo hasta el quiebre contando desde el último
remito.

Uso:
    from core.inventario.reposicion import perfil_reposicion, resumen_reposicion

    perfil = perfil_reposicion("cliente_x", "008", "2026-01-01", "2026-07-31",
                               codigos=["1111033", "1111001"], umbral_barrera=10)
    resumen = resumen_reposicion(perfil)
"""

import pandas as pd

from core.comun import conectar, DEPOSITOS_DISTRIBUCION
from core.inventario.series import serie_stock, resumen_quiebres, marcadores_movimiento

# Promedio calendario (365.25/12) — denominador de ventas_promedio, en días
# y no en cuenta de meses porque el período pedido no calza con bordes de mes.
_DIAS_POR_MES = 30.4375


def _cadencia_remitos(rem: pd.DataFrame) -> pd.DataFrame:
    """
    Cadencia y cantidad de remitos de entrada por SKU.

    Args:
        rem: subconjunto de marcadores_movimiento(serie, tipos=["REM"]) con
             delta > 0 (solo entradas — misma convención que ya usa la
             sección "Evolución diaria de un SKU" de Serie de stock).

    Returns:
        DataFrame: codigo, remitos_periodo, cantidad_promedio_remito,
        fecha_ultimo_remito, cadencia_dias (NaN si hay 0 o 1 remito).
    """
    cols = ["codigo", "remitos_periodo", "cantidad_promedio_remito",
            "fecha_ultimo_remito", "cadencia_dias"]
    if rem.empty:
        return pd.DataFrame(columns=cols)

    g = rem.groupby("codigo")
    out = g.agg(
        remitos_periodo=("fecha", "size"),
        fecha_primer_remito=("fecha", "min"),
        fecha_ultimo_remito=("fecha", "max"),
        cantidad_promedio_remito=("delta", "mean"),
    ).reset_index()
    out["cadencia_dias"] = (
        (out["fecha_ultimo_remito"] - out["fecha_primer_remito"]).dt.days
        / (out["remitos_periodo"] - 1)
    ).where(out["remitos_periodo"] > 1)
    return out[cols]


def _ventas_por_codigo(
    proyecto: str, codigodepo: str, fecha_desde: str, fecha_hasta: str,
    codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Ventas totales y promedio mensual por SKU en el período, para una
    sucursal. Ventas = filtradas por `tipos_categoria.es_venta` (no por
    tipomov: hay ventas registradas con tipomov='REM'), sumando `diferencia`
    con el signo volteado a positivo — mismo criterio que
    `core.inventario.interanual.METRICAS["ventas_u"]`.

    Returns:
        DataFrame: codigo, ventas_totales_periodo, ventas_promedio (mensual).
    """
    cols = ["codigo", "ventas_totales_periodo", "ventas_promedio"]
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depo": codigodepo, "desde": fecha_desde, "hasta": fecha_hasta}
        if codigos:
            conn.register("_codigos_ventas",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND m.codigo IN (SELECT codigo FROM _codigos_ventas)"
        df = conn.execute(f"""
            SELECT m.codigo, (-1 * SUM(m.diferencia))::DOUBLE AS ventas_totales_periodo
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            WHERE m.codigodepo = $depo
              AND m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              AND t.es_venta
              {filtro_cod}
            GROUP BY m.codigo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)

    dias = (pd.to_datetime(fecha_hasta) - pd.to_datetime(fecha_desde)).days + 1
    df["ventas_promedio"] = df["ventas_totales_periodo"] / (dias / _DIAS_POR_MES)
    return df[cols]


def _unidades_remitidas_por_codigo(
    proyecto: str, codigodepo: str, fecha_desde: str, fecha_hasta: str,
    codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Unidades remitidas totales por SKU en el período, para una sucursal:
    tipo IN ('RI', 'RE', 'MD') — remitos comunes más diferencias de camión.
    Suma `ingreso` (nunca `diferencia`): para MD la columna `diferencia`
    no sirve, el ERP exporta el egreso en negativo solo en ese tipo y la
    ingesta lo deja con signo positivo (ver `core.distribucion.diferencias`),
    así que reconstruir desde `ingreso` es el único criterio válido para
    los tres tipos a la vez.

    Returns:
        DataFrame: codigo, unidades_remitidas_periodo.
    """
    cols = ["codigo", "unidades_remitidas_periodo"]
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depo": codigodepo, "desde": fecha_desde, "hasta": fecha_hasta}
        if codigos:
            conn.register("_codigos_remitidas",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND m.codigo IN (SELECT codigo FROM _codigos_remitidas)"
        df = conn.execute(f"""
            SELECT m.codigo, SUM(m.ingreso)::DOUBLE AS unidades_remitidas_periodo
            FROM movimientos m
            WHERE m.codigodepo = $depo
              AND m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              AND m.tipo IN ('RI', 'RE', 'MD')
              {filtro_cod}
            GROUP BY m.codigo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    return df[cols]


def _ultima_venta_por_codigo(
    proyecto: str, codigodepo: str, fecha_hasta: str, codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Fecha de la última venta de cada SKU en la sucursal — mismo criterio de
    "venta" que `_ventas_por_codigo` (`tipos_categoria.es_venta`, no
    tipomov), pero sin acotar por `fecha_desde`: mira todo el historial
    hasta `fecha_hasta`. Un SKU sin ventas dentro del período pedido puede
    haber vendido antes; acotar también por `fecha_desde` ocultaría
    justamente eso.

    Returns:
        DataFrame: codigo, fecha_ultima_venta.
    """
    cols = ["codigo", "fecha_ultima_venta"]
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depo": codigodepo, "hasta": fecha_hasta}
        if codigos:
            conn.register("_codigos_ult_venta",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND m.codigo IN (SELECT codigo FROM _codigos_ult_venta)"
        df = conn.execute(f"""
            SELECT m.codigo, MAX(m.fecha) AS fecha_ultima_venta
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            WHERE m.codigodepo = $depo
              AND m.fecha <= $hasta::DATE
              AND t.es_venta
              {filtro_cod}
            GROUP BY m.codigo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["fecha_ultima_venta"] = pd.to_datetime(df["fecha_ultima_venta"])
    return df[cols]


def _dias_con_stock(serie: pd.DataFrame) -> pd.DataFrame:
    """
    Días con stock disponible (stock > 0) por SKU, sumando vigencia_dias —
    igual convención que usa `resumen_quiebres` para `dias_quiebre`, pero
    para el complemento. Deliberadamente NO se calcula como
    "días del período − dias_quiebre": el tramo antes del primer movimiento
    de un SKU dentro de la ventana no tiene fila en absoluto (ver
    `serie_stock`), así que no cuenta ni como quiebre ni como con-stock —
    tratarlo como "con stock" por descarte inflaría `ventas_promedio_ajustado`
    para SKUs cuyo primer movimiento del período llega tarde.

    Returns:
        DataFrame: codigo, dias_con_stock.
    """
    tmp = serie[["codigo", "vigencia_dias", "stock"]].copy()
    tmp["dias_con_stock"] = tmp["vigencia_dias"].where(tmp["stock"] > 0, 0)
    return tmp.groupby("codigo")["dias_con_stock"].sum().reset_index()


def _stock_disponible(
    proyecto: str, codigodepo: str, fecha_stock: str | None,
    codigos: list[str] | None,
) -> pd.DataFrame:
    """
    Stock del último snapshot para la sucursal analizada y para los centros
    de distribución (`DEPOSITOS_DISTRIBUCION`) — para saber, además de cómo
    viene reponiendo, qué hay disponible para pedir de inmediato sin esperar
    al próximo remito. Mismo snapshot ("fecha_stock") que ancla toda la
    reconstrucción de `serie_stock`, no uno nuevo. De paso trae `uxb`
    (unidades por bulto), `costo` y `lista_1`: mismo origen (`stock_sucursal`),
    así que salen del mismo query sin otra ida a la base — mismas columnas
    que usa `core.comun.precios_snapshot`.

    Returns:
        DataFrame: codigo, stock_sucursal, stock_cd_<código> (uno por cada
        depósito de DEPOSITOS_DISTRIBUCION), uxb, costo, lista_1.
    """
    cols_cd = [f"stock_cd_{d}" for d in DEPOSITOS_DISTRIBUCION]
    cols = ["codigo", "stock_sucursal", *cols_cd, "uxb", "costo", "lista_1"]
    if not fecha_stock:
        return pd.DataFrame(columns=cols)

    depos = sorted({codigodepo, *DEPOSITOS_DISTRIBUCION})
    conn = conectar(proyecto)
    try:
        filtro_cod = ""
        params = {"depos": depos, "fs": fecha_stock}
        if codigos:
            conn.register("_codigos_stock",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND codigo IN (SELECT codigo FROM _codigos_stock)"
        df = conn.execute(f"""
            SELECT codigo, codigodepo, SUM(stock)::DOUBLE AS stock,
                   MAX(uxb)::DOUBLE AS uxb,
                   MAX(costo)::DOUBLE AS costo,
                   MAX(lista_1)::DOUBLE AS lista_1
            FROM stock_sucursal
            WHERE codigodepo IN (SELECT unnest($depos))
              AND fecha_snapshot = $fs::DATE
              {filtro_cod}
            GROUP BY codigo, codigodepo
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=cols)

    # Selección columna por columna (no un rename masivo): si la sucursal
    # analizada es justo uno de los depósitos de distribución, un rename por
    # dict pisaría una de las dos claves porque comparten el mismo código.
    piv = df.pivot_table(index="codigo", columns="codigodepo", values="stock",
                         aggfunc="sum")
    out = pd.DataFrame({"codigo": piv.index})
    out["stock_sucursal"] = (piv[codigodepo].values if codigodepo in piv.columns
                             else 0.0)
    for d in DEPOSITOS_DISTRIBUCION:
        out[f"stock_cd_{d}"] = piv[d].values if d in piv.columns else 0.0
    # uxb, costo y lista_1 son atributos del producto, no del depósito:
    # normalmente el mismo valor en todas las filas de un código, pero por
    # las dudas se toma el máximo entre depósitos/snapshot en vez de asumir
    # cuál "gana".
    out = out.merge(df.groupby("codigo")[["uxb", "costo", "lista_1"]].max().reset_index(),
                    on="codigo", how="left")
    return out.reset_index(drop=True)


def _tiempo_hasta_quiebre(
    serie: pd.DataFrame, rem_por_sku: pd.DataFrame,
    umbral_barrera: float, fecha_hasta: str,
) -> pd.DataFrame:
    """
    Para cada SKU con al menos un remito de entrada: mira SOLO hacia
    adelante desde su último remito hasta fecha_hasta y busca el primer día
    con stock<=umbral_barrera. Si el nivel que deja el propio remito ya está
    bajo el umbral, el resultado es 0 días. Si no se encuentra cruce en la
    ventana observada, queda censurado (NaN) —no cero— y se informa aparte
    cuántos días se observaron sin romper (`dias_desde_ultimo_ingreso`), para
    no confundir "no rompió" con "no hay datos".

    Returns:
        DataFrame: codigo, fecha_ultimo_remito, dias_desde_ultimo_ingreso,
        dias_hasta_quiebre_barrera, quebro_barrera.
    """
    cols = ["codigo", "fecha_ultimo_remito", "dias_desde_ultimo_ingreso",
            "dias_hasta_quiebre_barrera", "quebro_barrera"]
    base = rem_por_sku.dropna(subset=["fecha_ultimo_remito"])[["codigo", "fecha_ultimo_remito"]]
    if base.empty:
        return pd.DataFrame(columns=cols)

    hasta_ts = pd.to_datetime(fecha_hasta)
    s = serie.merge(base, on="codigo", how="inner")

    # Nivel que deja el propio remito: si ya está bajo el umbral ahí, la
    # rotura es inmediata (0 días) — el filtro "posterior al remito" de
    # abajo, por sí solo, se la perdería.
    en_remito = (s[s["fecha"] == s["fecha_ultimo_remito"]]
                 .drop_duplicates("codigo", keep="last")
                 .set_index("codigo")["stock"])

    forward = s[s["fecha"] > s["fecha_ultimo_remito"]].sort_values(["codigo", "fecha"])
    ultimo = base.set_index("codigo")["fecha_ultimo_remito"]

    cruza = forward[forward["stock"] <= umbral_barrera]
    primera_fecha = cruza.groupby("codigo")["fecha"].first()
    dias = (primera_fecha - ultimo.reindex(primera_fecha.index)).dt.days
    dias = dias.reindex(ultimo.index)
    ya_roto = en_remito[en_remito <= umbral_barrera].index
    dias.loc[dias.index.isin(ya_roto)] = 0

    out = pd.DataFrame({"codigo": ultimo.index, "fecha_ultimo_remito": ultimo.values})
    out["dias_desde_ultimo_ingreso"] = (hasta_ts - out["fecha_ultimo_remito"]).dt.days
    out["dias_hasta_quiebre_barrera"] = out["codigo"].map(dias)
    out["quebro_barrera"] = out["dias_hasta_quiebre_barrera"].notna()
    return out[cols]


def _dias_desde_quiebre_real(serie: pd.DataFrame) -> pd.DataFrame:
    """
    Para los SKUs que llegan al cierre del período en quiebre (el último
    nivel de stock reconstruido es <= 0): hace cuántos días ininterrumpidos
    vienen en ese estado, contando hacia atrás desde fecha_hasta hasta el
    movimiento que rompió el nivel a cero. A diferencia del viejo cálculo de
    "días hasta el quiebre" (que miraba hacia adelante desde el último
    remito), esto no depende de que haya habido un remito en el período —
    solo importa el estado actual del stock. Misma técnica de tramos
    consecutivos que usa `resumen_quiebres` para `racha_quiebre`
    (`core.inventario.series`), pero quedándose solo con el tramo que
    contiene la última fila de cada SKU, no con el más largo del período.

    Returns:
        DataFrame: codigo, dias_desde_quiebre_real. Los SKUs que cierran con
        stock > 0 no aparecen (quedan NaN tras el merge en
        `perfil_reposicion`, mismo criterio de censura que el resto del
        módulo).
    """
    cols = ["codigo", "dias_desde_quiebre_real"]
    if serie.empty:
        return pd.DataFrame(columns=cols)

    df = serie.sort_values(["codigo", "fecha"]).copy()
    df["en_quiebre"] = df["stock"] <= 0
    df["_grupo"] = (df["en_quiebre"] != df.groupby("codigo")["en_quiebre"].shift()).cumsum()

    ultima = df.groupby("codigo").tail(1)
    actuales = ultima.loc[ultima["en_quiebre"], ["codigo", "_grupo"]]
    if actuales.empty:
        return pd.DataFrame(columns=cols)

    racha = (df.merge(actuales, on=["codigo", "_grupo"])
              .groupby("codigo")["vigencia_dias"].sum())
    return racha.rename("dias_desde_quiebre_real").reset_index()[cols]


def perfil_reposicion(
    proyecto: str,
    codigodepo: str,
    fecha_desde: str,
    fecha_hasta: str,
    codigos: list[str] | None = None,
    umbral_barrera: float = 0,
) -> pd.DataFrame:
    """
    Perfil de reposición por SKU: cadencia y cantidad de remitos, ventas
    promedio, días de quiebre / bajo la barrera elegida, tiempo hasta cruzar
    la barrera contando desde el último remito, y — para los que están en
    quiebre ahora mismo — hace cuántos días vienen así.

    Args:
        proyecto, codigodepo: contexto (una sola sucursal).
        fecha_desde/hasta:    período a analizar.
        codigos:              subconjunto de SKUs (None = todos los que
                              tengan movimiento en el período/sucursal).
        umbral_barrera:       nivel de stock (unidades) que define la
                              "barrera" de alerta temprana, además del
                              quiebre real (stock<=0). Mismo criterio que
                              `umbral_bajo` en `resumen_quiebres`.

    Returns:
        DataFrame por SKU: codigo, descripcion, rubro, marca,
        gran_super_rubro, cadencia_dias, remitos_periodo,
        cantidad_promedio_remito, fecha_ultimo_remito,
        unidades_remitidas_periodo (tipo IN ('RI','RE','MD'), sumando
        `ingreso` — no `diferencia`, que para los MD no es confiable, ver
        `_unidades_remitidas_por_codigo`), ventas_totales_periodo,
        ventas_promedio, dias_con_stock, ventas_promedio_ajustado (ventas
        promedio mensual calculada solo sobre los días con stock disponible,
        no sobre el período entero — no se diluye por los días en quiebre,
        que no es que no hubo demanda sino que no había nada para vender),
        fecha_ultima_venta (última venta registrada del SKU hasta el fin del
        período, SIN acotar por el inicio — a diferencia de ventas_promedio,
        para no ocultar la última venta de un SKU que no vendió dentro de la
        ventana elegida pero sí antes),
        dias_quiebre, dias_sin_quiebre (complemento de dias_quiebre sobre el
        total de días del período — a diferencia de dias_con_stock, cuenta
        el tramo antes del primer movimiento del SKU como "sin quiebre", no
        lo deja fuera), pct_quiebre, dias_bajo_barrera,
        dias_desde_quiebre_real (para los SKUs que cierran el período con
        stock <= 0: hace cuántos días ininterrumpidos vienen en quiebre,
        contando hacia atrás desde fecha_hasta; NaN para los que cierran con
        stock > 0 — ver `_dias_desde_quiebre_real`), en_quiebre_real (bool,
        equivale a dias_desde_quiebre_real.notna()), dias_hasta_quiebre_barrera,
        quebro_barrera, dias_desde_ultimo_ingreso, sin_remitos_en_periodo,
        racha_quiebre, stock_min, stock_prom, stock_max, stock_final,
        stock_sucursal (último snapshot de la sucursal analizada),
        dias_remanentes (stock_sucursal sobre la tasa DIARIA de
        ventas_promedio_ajustado — cuántos días alcanza el stock actual al
        ritmo de venta reciente; vacío si no hay tasa de venta con la que
        dividir), pedido_sugerido ((ventas_promedio_ajustado * 2) menos
        stock_sucursal — cobertura objetivo de 2 meses de venta ajustada;
        puede dar negativo si ya sobra stock), pct_pedido (pedido_sugerido
        sobre ventas_promedio_ajustado, ratio crudo sin multiplicar por 100;
        vacío si no hay tasa de venta con la que dividir), stock_cd_<código>
        (uno por depósito de DEPOSITOS_DISTRIBUCION — lo disponible para
        pedir de inmediato), uxb (unidades por bulto, del mismo snapshot),
        costo, lista_1 (precio de costo y de lista 1, del mismo snapshot que
        el stock — mismas columnas que `core.comun.precios_snapshot`),
        movimientos.
        Ordenado por dias_hasta_quiebre_barrera ascendente (los que no
        cruzaron, al final). attrs: desde, hasta, sucursal, barrera,
        fecha_stock.
    """
    serie = serie_stock(proyecto, codigodepo, fecha_desde, fecha_hasta, codigos=codigos)

    cols_stock_cd = [f"stock_cd_{d}" for d in DEPOSITOS_DISTRIBUCION]
    cols = ["codigo", "descripcion", "rubro", "marca", "gran_super_rubro",
            "cadencia_dias", "remitos_periodo", "cantidad_promedio_remito",
            "fecha_ultimo_remito", "unidades_remitidas_periodo",
            "ventas_totales_periodo", "ventas_promedio",
            "dias_con_stock", "ventas_promedio_ajustado", "fecha_ultima_venta",
            "dias_quiebre", "dias_sin_quiebre", "pct_quiebre", "dias_bajo_barrera",
            "dias_desde_quiebre_real", "en_quiebre_real",
            "dias_hasta_quiebre_barrera", "quebro_barrera",
            "dias_desde_ultimo_ingreso", "sin_remitos_en_periodo",
            "racha_quiebre", "stock_min", "stock_prom", "stock_max",
            "stock_final", "stock_sucursal", "dias_remanentes",
            "pedido_sugerido", "pct_pedido",
            *cols_stock_cd, "uxb", "costo", "lista_1", "movimientos"]
    attrs = dict(desde=serie.attrs.get("desde"), hasta=serie.attrs.get("hasta"),
                 sucursal=codigodepo, barrera=umbral_barrera,
                 fecha_stock=serie.attrs.get("fecha_stock"))
    if serie.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    resumen = resumen_quiebres(serie, umbral_bajo=umbral_barrera)
    resumen = resumen.rename(columns={"dias_bajo": "dias_bajo_barrera"})
    dias_rango = (pd.to_datetime(serie.attrs["hasta"])
                  - pd.to_datetime(serie.attrs["desde"])).days + 1
    resumen["dias_sin_quiebre"] = dias_rango - resumen["dias_quiebre"]

    marcas = marcadores_movimiento(serie, tipos=["REM"])
    rem = marcas[marcas["delta"] > 0]
    rem_por_sku = _cadencia_remitos(rem)

    ventas = _ventas_por_codigo(proyecto, codigodepo, serie.attrs["desde"],
                                serie.attrs["hasta"], codigos)
    ultima_venta = _ultima_venta_por_codigo(proyecto, codigodepo,
                                            serie.attrs["hasta"], codigos)
    remitido = _unidades_remitidas_por_codigo(proyecto, codigodepo, serie.attrs["desde"],
                                              serie.attrs["hasta"], codigos)

    quiebre_post_remito = _tiempo_hasta_quiebre(
        serie, rem_por_sku, umbral_barrera, serie.attrs["hasta"])
    racha_actual = _dias_desde_quiebre_real(serie)

    stock_disp = _stock_disponible(proyecto, codigodepo, serie.attrs.get("fecha_stock"),
                                   codigos)
    dias_stock = _dias_con_stock(serie)

    df = (resumen
          .merge(rem_por_sku, on="codigo", how="left")
          .merge(ventas, on="codigo", how="left")
          .merge(ultima_venta, on="codigo", how="left")
          .merge(remitido, on="codigo", how="left")
          .merge(quiebre_post_remito.drop(columns=["fecha_ultimo_remito"]),
                 on="codigo", how="left")
          .merge(racha_actual, on="codigo", how="left")
          .merge(stock_disp, on="codigo", how="left")
          .merge(dias_stock, on="codigo", how="left"))

    df["remitos_periodo"] = df["remitos_periodo"].fillna(0).astype(int)
    df["unidades_remitidas_periodo"] = df["unidades_remitidas_periodo"].fillna(0.0)
    df["ventas_totales_periodo"] = df["ventas_totales_periodo"].fillna(0.0)
    df["ventas_promedio"] = df["ventas_promedio"].fillna(0.0)
    df["sin_remitos_en_periodo"] = df["remitos_periodo"] == 0
    df["dias_con_stock"] = df["dias_con_stock"].fillna(0.0)

    # Ventas promedio "ajustada": la venta promedio diluye los días en
    # quiebre (nada para vender = nada vendido, no es que no había demanda).
    # Dividiendo solo por los días con stock disponible da una tasa de
    # demanda más realista para decidir cuánto pedir.
    # El divisor se enmascara a NaN ANTES de dividir (no después con
    # .where()): con resultados de pocas filas, pandas puede evaluar la
    # división elemento a elemento como un 0.0/0.0 de Python puro, que
    # lanza ZeroDivisionError en vez de devolver NaN.
    dias_con_stock_div = df["dias_con_stock"].where(df["dias_con_stock"] > 0)
    df["ventas_promedio_ajustado"] = (
        df["ventas_totales_periodo"] / (dias_con_stock_div / _DIAS_POR_MES)
    )
    df["en_quiebre_real"] = df["dias_desde_quiebre_real"].notna()
    df["quebro_barrera"] = df["quebro_barrera"].fillna(False).astype(bool)
    df[["stock_sucursal", *cols_stock_cd]] = df[["stock_sucursal", *cols_stock_cd]].fillna(0.0)

    # Días remanentes: stock de la sucursal (snapshot) sobre la tasa DIARIA
    # de ventas_promedio_ajustado (que es mensual, de ahí el * _DIAS_POR_MES).
    # Vacío si no hay tasa de venta con la que dividir (mismo criterio de
    # censura que ventas_promedio_ajustado): stock_sucursal=0 con venta > 0
    # da 0 (sin cobertura), no vacío.
    ventas_ajustado_div = df["ventas_promedio_ajustado"].where(df["ventas_promedio_ajustado"] > 0)
    df["dias_remanentes"] = (
        df["stock_sucursal"] * _DIAS_POR_MES / ventas_ajustado_div
    )

    # Pedido sugerido: cobertura objetivo de 2 meses de venta ajustada menos
    # lo que ya hay en la sucursal. Puede dar negativo (ya sobra stock) — se
    # deja tal cual, no se recorta a 0, para poder detectar sobre-stock.
    df["pedido_sugerido"] = (df["ventas_promedio_ajustado"] * 2) - df["stock_sucursal"]
    df["pct_pedido"] = df["pedido_sugerido"] / ventas_ajustado_div

    df = (df[cols]
          .sort_values("dias_hasta_quiebre_barrera", na_position="last")
          .reset_index(drop=True))
    df.attrs.update(attrs)
    return df


def pedido_por_dias(perfil: pd.DataFrame, dias: float) -> pd.Series:
    """
    Pedido recomendado para cubrir `dias` de venta: venta diaria ajustada
    (ventas_promedio_ajustado, mensual, dividida por _DIAS_POR_MES) por esa
    cantidad de días, menos el stock actual de la sucursal. Mismo criterio
    de signo que `pedido_sugerido` (cobertura fija de 2 meses): puede dar
    negativo si ya sobra stock — no se recorta a 0 acá.

    Args:
        perfil: salida de perfil_reposicion (usa ventas_promedio_ajustado y
                stock_sucursal).
        dias:   días de cobertura objetivo.
    """
    tasa_diaria = perfil["ventas_promedio_ajustado"] / _DIAS_POR_MES
    return tasa_diaria * dias - perfil["stock_sucursal"]


def resumen_reposicion(perfil: pd.DataFrame) -> dict:
    """
    Agregados del perfil para un panel de KPIs: SKUs analizados, cadencia
    promedio, cobertura de remitos, días de quiebre promedio, y cuántos
    cruzaron la barrera vs. cuántos quedaron censurados (con remito, sin
    romper en el período observado).
    """
    if perfil.empty:
        return {
            "skus": 0, "cadencia_prom": None, "con_remitos": 0, "sin_remitos": 0,
            "dias_quiebre_prom": None, "dias_bajo_barrera_prom": None,
            "quebraron_barrera": 0, "censurados_barrera": 0,
            "dias_hasta_quiebre_mediana": None,
        }
    con_rem = perfil[~perfil["sin_remitos_en_periodo"]]
    return {
        "skus": len(perfil),
        "cadencia_prom": (float(perfil["cadencia_dias"].mean())
                          if perfil["cadencia_dias"].notna().any() else None),
        "con_remitos": int((~perfil["sin_remitos_en_periodo"]).sum()),
        "sin_remitos": int(perfil["sin_remitos_en_periodo"].sum()),
        "dias_quiebre_prom": float(perfil["dias_quiebre"].mean()),
        "dias_bajo_barrera_prom": float(perfil["dias_bajo_barrera"].mean()),
        "quebraron_barrera": int(con_rem["quebro_barrera"].sum()),
        "censurados_barrera": int((~con_rem["quebro_barrera"]).sum()) if len(con_rem) else 0,
        "dias_hasta_quiebre_mediana": (
            float(con_rem.loc[con_rem["quebro_barrera"], "dias_hasta_quiebre_barrera"].median())
            if con_rem["quebro_barrera"].any() else None),
    }
