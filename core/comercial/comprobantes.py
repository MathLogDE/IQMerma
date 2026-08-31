"""
core/comercial/comprobantes.py — Comprobantes de venta/remito y canasta

Reconstruye el comprobante (factura, nota de crédito, remito) a partir de las
líneas de `movimientos`: cada línea es un (numero, codigodepo, tipo, codigo),
y todas las líneas que comparten (numero, codigodepo, tipo) son el mismo
documento — confirmado contra datos reales: `numero` se repite entre tipos
distintos de una misma sucursal (facturas y notas de crédito pueden compartir
numeración), así que `tipo` tiene que entrar en la clave. La fecha del
comprobante es el mínimo de sus líneas (en la enorme mayoría de los casos
coincide; el puñado con fechas mezcladas se resuelve igual que los pares MD
de `core.distribucion.diferencias`).

Solo se reconstruyen comprobantes de las categorías "Ventas" (FA/FB/NCA/NCB/
FCA/NCCA) y "Remitido" (RE/RI/RDC) — MD (diferencia de camión) ya se trata
aparte en `core.distribucion.diferencias` y queda afuera acá.

Valorización: siempre a precio constante del snapshot elegido (igual que
`core.inventario.interanual` y `core.comercial.margenes`), no al monto
transaccional de `movimientos.costo` — así el ticket se puede comparar entre
períodos sin que la inflación de precios lo distorsione.

Uso:
    from core.comercial.comprobantes import (
        resumen_comprobantes, evolucion_comprobantes, evolucion_comprobantes_sucursales,
        evolucion_comprobantes_multi, perfil_sku_en_comprobante, afinidad_skus,
        tipos_disponibles,
    )
"""

import pandas as pd

from core.comun import (
    conectar, precios_snapshot, snapshot_usado, validar_modo_valorizacion,
    adjuntar_rubros, MESES,
)

# Categorías de tipos_categoria que forman un "comprobante" analizable acá.
# MD (Dif. de camión) queda afuera: se trata en core.distribucion.diferencias.
CATEGORIAS_COMPROBANTE = ("Ventas", "Remitido")

METRICAS_COMPROBANTE = {
    "cantidad_comprobantes": {"etiqueta": "Cantidad de comprobantes", "valorizada": False},
    "referencias_prom":      {"etiqueta": "Referencias promedio",     "valorizada": False},
    "unidades_prom":         {"etiqueta": "Unidades promedio",        "valorizada": False},
    "valor_prom":            {"etiqueta": "Ticket promedio",          "valorizada": True},
}


def tipos_disponibles(proyecto: str) -> pd.DataFrame:
    """
    Tipos de comprobante disponibles (FA/FB/NCA/NCB/... de venta, RE/RI/RDC
    de remito), en el orden del catálogo `tipos_categoria` — para armar el
    filtro de la UI.

    Returns:
        DataFrame: tipo, categoria. Ordenado por orden de catálogo.
    """
    conn = conectar(proyecto)
    try:
        df = conn.execute("""
            SELECT tipo, categoria FROM tipos_categoria
            WHERE categoria IN (SELECT unnest($cats))
            ORDER BY orden, tipo
        """, {"cats": list(CATEGORIAS_COMPROBANTE)}).df()
    finally:
        conn.close()
    return df


# ---------------------------------------------------------------------------
# Reconstrucción de comprobantes (privada, la usan todas las funciones públicas)
# ---------------------------------------------------------------------------

def _comprobantes(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """
    Reconstruye un comprobante por fila, agrupando líneas de `movimientos`
    por (numero, codigodepo, tipo).

    Ojo: si se pasa `codigos`, el filtro se aplica a nivel de LÍNEA antes de
    reconstruir el comprobante (igual que el resto de los módulos de core) —
    un comprobante mixto (con líneas dentro y fuera del recorte) queda con
    solo sus líneas del recorte, así que `referencias`/`unidades`/`valor`
    reflejan el subconjunto filtrado, no el comprobante completo. `tipos`
    (FA/FB/NCA/NCB/... o RE/RI/RDC) sí filtra el comprobante entero, porque
    el tipo es un atributo del header, no de la línea.

    Returns:
        (DataFrame [numero, codigodepo, tipo, categoria, fecha, referencias,
         unidades, valor], fecha del snapshot usado para valorizar).
    """
    validar_modo_valorizacion(modo_valorizacion)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"

    conn = conectar(proyecto)
    try:
        conn.register("_precios_comp", precios_snapshot(conn, fecha_valorizacion)
                      [["codigo", unit_col]].rename(columns={unit_col: "precio"}))
        fs = snapshot_usado(conn, fecha_valorizacion)

        filtros = ["t.categoria IN (SELECT unnest($cats))",
                   "m.fecha >= $desde::DATE", "m.fecha <= $hasta::DATE"]
        params: dict = {"cats": list(CATEGORIAS_COMPROBANTE), "desde": desde, "hasta": hasta}
        if sucursales:
            filtros.append("m.codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if tipos:
            filtros.append("m.tipo IN (SELECT unnest($tipos))")
            params["tipos"] = list(tipos)
        if codigos:
            conn.register("_codigos_comp", pd.DataFrame(
                {"codigo": pd.unique(pd.Series(list(codigos)))}))
            filtros.append("m.codigo IN (SELECT codigo FROM _codigos_comp)")

        df = conn.execute(f"""
            WITH lineas AS (
                SELECT m.numero, m.codigodepo, m.tipo, m.fecha, m.codigo,
                       t.categoria,
                       (CASE WHEN t.categoria = 'Ventas' THEN -m.diferencia
                             ELSE m.ingreso END)::DOUBLE AS unidades_linea,
                       COALESCE(p.precio, 0)::DOUBLE AS precio
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo
                LEFT JOIN _precios_comp p ON m.codigo = p.codigo
                WHERE {' AND '.join(filtros)}
            )
            SELECT numero, codigodepo, tipo, MAX(categoria) AS categoria,
                   MIN(fecha) AS fecha,
                   COUNT(DISTINCT codigo)::INT           AS referencias,
                   SUM(unidades_linea)::DOUBLE           AS unidades,
                   SUM(unidades_linea * precio)::DOUBLE  AS valor
            FROM lineas
            GROUP BY numero, codigodepo, tipo
        """, params).df()
    finally:
        conn.close()

    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"])
    return df, fs


# ---------------------------------------------------------------------------
# Comparativa por sucursal
# ---------------------------------------------------------------------------

def resumen_comprobantes(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Comparativa entre sucursales: cantidad de comprobantes, referencias
    promedio, unidades promedio y ticket promedio, abierto por sucursal y
    tipo de comprobante (FA/FB/NCA/NCB/... de venta, RE/RI/RDC de remito).

    Args:
        tipos: subconjunto de tipos de comprobante (ver `tipos_disponibles`).
               None = todos.

    Returns:
        DataFrame: codigodepo, nombre, tipo, categoria, cantidad_comprobantes,
        referencias_prom, unidades_prom, valor_prom (ticket promedio),
        valor_total. Ordenado por cantidad_comprobantes descendente.
        attrs: desde, hasta, modo_valorizacion, fecha_valorizacion.
    """
    comp, fs = _comprobantes(proyecto, fecha_desde, fecha_hasta, sucursales,
                             codigos, tipos, modo_valorizacion, fecha_valorizacion)
    attrs = dict(desde=str(pd.to_datetime(fecha_desde).date()),
                 hasta=str(pd.to_datetime(fecha_hasta).date()),
                 modo_valorizacion=modo_valorizacion, fecha_valorizacion=fs)
    cols = ["codigodepo", "nombre", "tipo", "categoria", "cantidad_comprobantes",
            "referencias_prom", "unidades_prom", "valor_prom", "valor_total"]
    if comp.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    res = comp.groupby(["codigodepo", "tipo", "categoria"]).agg(
        cantidad_comprobantes=("numero", "size"),
        referencias_prom=("referencias", "mean"),
        unidades_prom=("unidades", "mean"),
        valor_prom=("valor", "mean"),
        valor_total=("valor", "sum"),
    ).reset_index()

    conn = conectar(proyecto)
    try:
        deps = conn.execute("SELECT codigodepo, nombre FROM depositos").df()
    finally:
        conn.close()
    nombre = deps.drop_duplicates("codigodepo").set_index("codigodepo")["nombre"]
    res["nombre"] = res["codigodepo"].map(nombre)

    res = (res[cols].sort_values("cantidad_comprobantes", ascending=False)
           .reset_index(drop=True))
    res.attrs.update(attrs)
    return res


# ---------------------------------------------------------------------------
# Evolución mensual / interanual
# ---------------------------------------------------------------------------

def _agregar_metrica(df: pd.DataFrame, metrica: str, group_cols: list[str]) -> pd.Series:
    if metrica == "cantidad_comprobantes":
        return df.groupby(group_cols).size().rename("valor").astype(float)
    col = {"referencias_prom": "referencias", "unidades_prom": "unidades",
           "valor_prom": "valor"}[metrica]
    return df.groupby(group_cols)[col].mean().rename("valor")


def _recortar_periodo(comp: pd.DataFrame, anios: list[int],
                      meses: list[int] | None, dia_corte: int | None) -> pd.DataFrame:
    comp = comp[comp["fecha"].dt.year.isin(anios)]
    if meses:
        comp = comp[comp["fecha"].dt.month.isin(meses)]
    if dia_corte:
        comp = comp[comp["fecha"].dt.day <= dia_corte]
    comp = comp.copy()
    comp["anio"] = comp["fecha"].dt.year
    comp["mes"] = comp["fecha"].dt.month
    return comp


def evolucion_comprobantes(
    proyecto: str,
    anios: list[int],
    meses: list[int] | None = None,
    metrica: str = "cantidad_comprobantes",
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
) -> pd.DataFrame:
    """
    Serie mes a mes con una línea por año — mismo patrón que
    `core.inventario.interanual.serie_mensual_anios`, para la métrica de
    comprobantes elegida.

    Args:
        tipos: subconjunto de tipos de comprobante (ver `tipos_disponibles`).
               None = todos.

    Returns:
        DataFrame largo: anio, mes, mes_nombre, valor. Los meses sin datos
        quedan en 0 para que las líneas no se corten.
        attrs: metrica, fecha_valorizacion.
    """
    if metrica not in METRICAS_COMPROBANTE:
        raise ValueError(f"metrica debe ser una de {list(METRICAS_COMPROBANTE)}, no '{metrica}'")
    anios = sorted({int(a) for a in anios})
    desde, hasta = f"{anios[0]}-01-01", f"{anios[-1]}-12-31"

    comp, fs = _comprobantes(proyecto, desde, hasta, sucursales, codigos, tipos,
                             modo_valorizacion, fecha_valorizacion)
    cols = ["anio", "mes", "mes_nombre", "valor"]
    pedidos = sorted(meses) if meses else list(range(1, 13))
    grilla = pd.MultiIndex.from_product([anios, pedidos], names=["anio", "mes"])
    attrs = dict(metrica=metrica, fecha_valorizacion=fs)

    if comp.empty:
        vacio = pd.DataFrame(index=grilla).reset_index()
        vacio["valor"] = 0.0
        vacio["mes_nombre"] = vacio["mes"].map(MESES)
        vacio = vacio[cols]
        vacio.attrs.update(attrs)
        return vacio

    comp = _recortar_periodo(comp, anios, meses, dia_corte)
    serie = (_agregar_metrica(comp, metrica, ["anio", "mes"])
             .reindex(grilla, fill_value=0.0).reset_index())
    serie["mes_nombre"] = serie["mes"].map(MESES)
    serie = serie[cols]
    serie.attrs.update(attrs)
    return serie


def evolucion_comprobantes_multi(
    proyecto: str,
    anios: list[int],
    sucursales: list[str] | None = None,
    meses: list[int] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
    abrir_sucursales: bool = False,
) -> dict:
    """
    Evolución mensual de las 4 métricas de `METRICAS_COMPROBANTE` juntas (para
    el reporte imprimible, que ya no obliga a elegir una sola métrica como el
    gráfico interactivo). Reconstruye los comprobantes una única vez y deriva
    todas las métricas de esa misma reconstrucción, en vez de llamar a
    `evolucion_comprobantes` una vez por métrica.

    Args:
        abrir_sucursales: si es True, además del total agrega la misma serie
            abierta por sucursal (para mostrar "todas juntas" + un gráfico
            por sucursal en el imprimible cuando el usuario filtró varias).

    Returns:
        {"combinado": DataFrame [anio, mes, mes_nombre, metrica, valor],
         "por_sucursal": DataFrame [codigodepo, anio, mes, mes_nombre,
         metrica, valor] si abrir_sucursales, si no None}.
        Ambos DataFrames llevan attrs["fecha_valorizacion"].
    """
    anios = sorted({int(a) for a in anios})
    desde, hasta = f"{anios[0]}-01-01", f"{anios[-1]}-12-31"
    metricas = list(METRICAS_COMPROBANTE)
    pedidos = sorted(meses) if meses else list(range(1, 13))

    comp, fs = _comprobantes(proyecto, desde, hasta, sucursales, codigos, tipos,
                             modo_valorizacion, fecha_valorizacion)
    comp_r = _recortar_periodo(comp, anios, meses, dia_corte) if not comp.empty else comp

    grilla = pd.MultiIndex.from_product([anios, pedidos], names=["anio", "mes"])
    partes = []
    for m in metricas:
        if comp_r.empty:
            s = pd.DataFrame(index=grilla).reset_index()
            s["valor"] = 0.0
        else:
            s = _agregar_metrica(comp_r, m, ["anio", "mes"]).reindex(grilla, fill_value=0.0).reset_index()
        s["metrica"] = m
        partes.append(s)
    combinado = pd.concat(partes, ignore_index=True)
    combinado["mes_nombre"] = combinado["mes"].map(MESES)
    combinado = combinado[["anio", "mes", "mes_nombre", "metrica", "valor"]]
    combinado.attrs["fecha_valorizacion"] = fs

    por_sucursal = None
    if abrir_sucursales:
        sucs = list(sucursales or [])
        grilla_s = pd.MultiIndex.from_product([sucs, anios, pedidos],
                                              names=["codigodepo", "anio", "mes"])
        partes_s = []
        for m in metricas:
            if comp_r.empty or not sucs:
                s = pd.DataFrame(index=grilla_s).reset_index()
                s["valor"] = 0.0
            else:
                s = (_agregar_metrica(comp_r, m, ["codigodepo", "anio", "mes"])
                     .reindex(grilla_s, fill_value=0.0).reset_index())
            s["metrica"] = m
            partes_s.append(s)
        por_sucursal = pd.concat(partes_s, ignore_index=True)
        por_sucursal["mes_nombre"] = por_sucursal["mes"].map(MESES)
        por_sucursal = por_sucursal[["codigodepo", "anio", "mes", "mes_nombre", "metrica", "valor"]]
        por_sucursal.attrs["fecha_valorizacion"] = fs

    return {"combinado": combinado, "por_sucursal": por_sucursal}


def evolucion_comprobantes_sucursales(
    proyecto: str,
    anios: list[int],
    sucursales: list[str],
    meses: list[int] | None = None,
    metrica: str = "cantidad_comprobantes",
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
) -> pd.DataFrame:
    """
    Igual que `evolucion_comprobantes`, pero abierta también por sucursal —
    mismo patrón que `serie_mensual_sucursales` de
    `core.inventario.interanual`, para cruzar sucursales entre sí.

    Args:
        tipos: subconjunto de tipos de comprobante (ver `tipos_disponibles`).
               None = todos.

    Returns:
        DataFrame largo: codigodepo, anio, mes, mes_nombre, valor.
    """
    if metrica not in METRICAS_COMPROBANTE:
        raise ValueError(f"metrica debe ser una de {list(METRICAS_COMPROBANTE)}, no '{metrica}'")
    anios = sorted({int(a) for a in anios})
    sucs = list(sucursales)
    cols = ["codigodepo", "anio", "mes", "mes_nombre", "valor"]
    if not sucs:
        return pd.DataFrame(columns=cols)

    desde, hasta = f"{anios[0]}-01-01", f"{anios[-1]}-12-31"
    comp, fs = _comprobantes(proyecto, desde, hasta, sucs, codigos, tipos,
                             modo_valorizacion, fecha_valorizacion)
    pedidos = sorted(meses) if meses else list(range(1, 13))
    grilla = pd.MultiIndex.from_product([sucs, anios, pedidos],
                                        names=["codigodepo", "anio", "mes"])
    attrs = dict(metrica=metrica, fecha_valorizacion=fs)

    if comp.empty:
        vacio = pd.DataFrame(index=grilla).reset_index()
        vacio["valor"] = 0.0
        vacio["mes_nombre"] = vacio["mes"].map(MESES)
        vacio = vacio[cols]
        vacio.attrs.update(attrs)
        return vacio

    comp = _recortar_periodo(comp, anios, meses, dia_corte)
    serie = (_agregar_metrica(comp, metrica, ["codigodepo", "anio", "mes"])
             .reindex(grilla, fill_value=0.0).reset_index())
    serie["mes_nombre"] = serie["mes"].map(MESES)
    serie = serie[cols]
    serie.attrs.update(attrs)
    return serie


# ---------------------------------------------------------------------------
# Perfil de SKU dentro del comprobante ("se vende de a N")
# ---------------------------------------------------------------------------

def perfil_sku_en_comprobante(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Por SKU: en cuántos comprobantes de VENTA aparece y cuántas unidades trae
    en promedio cada vez que aparece — resuelve patrones como "se factura de
    a 6 unidades". Solo mira comprobantes de venta (categoria='Ventas'): un
    remito de reposición no tiene el mismo patrón de "pack" comercial.

    Args:
        tipos: subconjunto de tipos de comprobante de venta (FA/FB/NCA/...).
               None = todos. Si se pasan tipos de remito, el resultado da
               vacío (perfil_sku_en_comprobante solo mira ventas).

    Returns:
        DataFrame: codigo, descripcion, rubro, marca, gran_super_rubro,
        apariciones (comprobantes distintos donde aparece), unidades_totales,
        unidades_prom_por_aparicion. Ordenado por apariciones descendente.
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    conn = conectar(proyecto)
    try:
        filtros = ["t.categoria = 'Ventas'",
                   "m.fecha >= $desde::DATE", "m.fecha <= $hasta::DATE"]
        params: dict = {"desde": desde, "hasta": hasta}
        if sucursales:
            filtros.append("m.codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if tipos:
            filtros.append("m.tipo IN (SELECT unnest($tipos))")
            params["tipos"] = list(tipos)
        if codigos:
            conn.register("_codigos_perfil", pd.DataFrame(
                {"codigo": pd.unique(pd.Series(list(codigos)))}))
            filtros.append("m.codigo IN (SELECT codigo FROM _codigos_perfil)")

        df = conn.execute(f"""
            WITH lineas AS (
                SELECT m.numero, m.codigodepo, m.tipo, m.codigo,
                       (-SUM(m.diferencia))::DOUBLE AS unidades_linea
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo
                WHERE {' AND '.join(filtros)}
                GROUP BY m.numero, m.codigodepo, m.tipo, m.codigo
            )
            SELECT codigo, COUNT(*)::INT AS apariciones,
                   SUM(unidades_linea)::DOUBLE AS unidades_totales,
                   AVG(unidades_linea)::DOUBLE AS unidades_prom_por_aparicion
            FROM lineas
            GROUP BY codigo
        """, params).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    cols = ["codigo", "descripcion", "rubro", "marca", "gran_super_rubro",
            "apariciones", "unidades_totales", "unidades_prom_por_aparicion"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)
    df = (df[cols].sort_values("apariciones", ascending=False)
          .reset_index(drop=True))
    return df


# ---------------------------------------------------------------------------
# Afinidad entre SKUs (canasta)
# ---------------------------------------------------------------------------

def afinidad_skus(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    tipos: list[str] | None = None,
    min_apariciones: int = 20,
    top_n: int = 50,
) -> pd.DataFrame:
    """
    Pares de SKUs co-comprados en el mismo comprobante de venta (afinidad /
    canasta). Para no explotar combinatoriamente (comprobantes de hasta ~120
    líneas, catálogos con cientos de miles de SKUs), primero se descartan los
    SKUs con menos de `min_apariciones` en comprobantes distintos, y el
    self-join solo corre entre los que quedan. Un recorte de fechas o
    catálogo (`codigos`) angosto acelera bastante el cálculo.

    Args:
        tipos: subconjunto de tipos de comprobante de venta (FA/FB/NCA/...).
               None = todos.

    Returns:
        DataFrame: codigo_a, descripcion_a, codigo_b, descripcion_b,
        comprobantes_conjuntos, soporte (% de comprobantes de venta del
        recorte que tienen el par), confianza_a_b (P(aparece B | aparece A)),
        confianza_b_a, lift (>1 = el par aparece junto más de lo que la
        casualidad explicaría). Ordenado por comprobantes_conjuntos
        descendente, top_n filas.
        attrs: skus_evaluados, skus_descartados (por debajo de
        min_apariciones — no entran al self-join), total_comprobantes.
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    cols = ["codigo_a", "descripcion_a", "codigo_b", "descripcion_b",
            "comprobantes_conjuntos", "soporte", "confianza_a_b",
            "confianza_b_a", "lift"]

    conn = conectar(proyecto)
    try:
        filtros = ["t.categoria = 'Ventas'",
                   "m.fecha >= $desde::DATE", "m.fecha <= $hasta::DATE"]
        params: dict = {"desde": desde, "hasta": hasta}
        if sucursales:
            filtros.append("m.codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if tipos:
            filtros.append("m.tipo IN (SELECT unnest($tipos))")
            params["tipos"] = list(tipos)
        if codigos:
            conn.register("_codigos_afin", pd.DataFrame(
                {"codigo": pd.unique(pd.Series(list(codigos)))}))
            filtros.append("m.codigo IN (SELECT codigo FROM _codigos_afin)")

        lineas = conn.execute(f"""
            SELECT DISTINCT m.numero, m.codigodepo, m.tipo, m.codigo
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            WHERE {' AND '.join(filtros)}
        """, params).df()

        total_comprobantes = int(
            lineas.drop_duplicates(["numero", "codigodepo", "tipo"]).shape[0])
        apariciones = (lineas.groupby("codigo").size()
                       .rename("apariciones").reset_index())
        frecuentes = apariciones[apariciones["apariciones"] >= min_apariciones]
        attrs = dict(skus_evaluados=int(len(frecuentes)),
                     skus_descartados=int(len(apariciones) - len(frecuentes)),
                     total_comprobantes=total_comprobantes)

        if frecuentes.empty or total_comprobantes == 0:
            vacio = pd.DataFrame(columns=cols)
            vacio.attrs.update(attrs)
            return vacio

        conn.register("_lineas_afin",
                      lineas[lineas["codigo"].isin(frecuentes["codigo"])])
        pares = conn.execute("""
            SELECT a.codigo AS codigo_a, b.codigo AS codigo_b,
                   COUNT(*)::INT AS comprobantes_conjuntos
            FROM _lineas_afin a
            JOIN _lineas_afin b
              ON a.numero = b.numero AND a.codigodepo = b.codigodepo
             AND a.tipo = b.tipo AND a.codigo < b.codigo
            GROUP BY a.codigo, b.codigo
        """).df()

        df_art = conn.execute("SELECT codigo, descripcion FROM articulos").df()
    finally:
        conn.close()

    if pares.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    apar = frecuentes.set_index("codigo")["apariciones"]
    pares["soporte"] = pares["comprobantes_conjuntos"] / total_comprobantes * 100
    pares["confianza_a_b"] = (pares["comprobantes_conjuntos"]
                              / pares["codigo_a"].map(apar) * 100)
    pares["confianza_b_a"] = (pares["comprobantes_conjuntos"]
                              / pares["codigo_b"].map(apar) * 100)
    pares["lift"] = (pares["comprobantes_conjuntos"] * total_comprobantes
                     / (pares["codigo_a"].map(apar) * pares["codigo_b"].map(apar)))

    desc = df_art.drop_duplicates("codigo").set_index("codigo")["descripcion"]
    pares["descripcion_a"] = pares["codigo_a"].map(desc)
    pares["descripcion_b"] = pares["codigo_b"].map(desc)

    pares = (pares[cols].sort_values("comprobantes_conjuntos", ascending=False)
             .head(top_n).reset_index(drop=True))
    pares.attrs.update(attrs)
    return pares
