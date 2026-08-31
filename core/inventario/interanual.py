"""
core/inventario/interanual.py — Comparación de años (evolución interanual)

Compara el mismo recorte de catálogo entre varios años: 2025 vs 2026, o
2024;2025;2026, o los años sueltos que se quiera. Los años disponibles salen
siempre de los datos (`anios_disponibles`), nunca hardcodeados, así que a
medida que se cargue histórico aparecen solos.

Qué se compara
--------------
**Flujo, no stock.** Los snapshots de stock son de una fecha puntual (hoy hay
dos, ambos de julio 2026), así que no existe un nivel de stock histórico contra
el cual comparar. Lo que sí es histórico son los movimientos, y de ahí salen
las métricas: lo que se vendió y lo que se perdió, en unidades o valorizado.

Precios constantes
------------------
La valorización usa **un solo snapshot de precios para todos los años** (el
precio es global por SKU en el ERP). Eso es deliberado: compara volumen real,
no inflación. Dos años valorizados al mismo precio se diferencian sólo por
las unidades y por el mix de productos, que es justo lo que se quiere ver.

Meses parciales
---------------
Si los datos de un año arrancan o cortan a mitad de mes, ese mes no es
comparable contra el mismo mes de otro año. `cobertura_meses()` lo detecta y
`dia_corte` permite recortar todos los años al mismo día (ej. comparar del 1
al 16 de julio en ambos años). Con `meses_dia_corte` el recorte se limita a
esos meses (los incompletos); el resto de `meses` queda entero — así un
período largo con un solo mes en curso no pierde días de los meses ya
cerrados.

Uso:
    from core.inventario.interanual import comparar_anios, serie_mensual_anios

    comp = comparar_anios("cliente_x", [2025, 2026], dimension="rubro")
    serie = serie_mensual_anios("cliente_x", [2025, 2026])
"""

import pandas as pd

from core.comun import (
    conectar, catalogo_articulos, precios_snapshot, snapshot_usado,
    validar_modo_valorizacion, DEPOSITOS_DISTRIBUCION, MESES,
)

# Métricas comparables.
#   filtro: condición SQL sobre el catálogo de tipos (tabla `t`).
#   campo:  columna de movimientos que se suma.
#   signo:  invierte el resultado — ventas y merma son egresos (diferencia
#           negativa) y se muestran en positivo.
#
# Ventas: se filtra por `es_venta`, no por tipomov. Hay ventas registradas con
# tipomov REM (facturadas con remito a consumidor final) que son ventas de
# verdad; el catálogo las clasifica por `tipo` y las toma bien.
#
# Remitos: es la reposición real (categoría "Remitido" = RI/RE/RDC) y se mide
# por `ingreso`, o sea **lo que efectivamente entró**. Sumar `diferencia` no
# serviría: el egreso del centro de distribución cancelaría el ingreso de la
# sucursal y el total quedaría en cero.
METRICAS = {
    "ventas_u":    {"etiqueta": "Ventas (unidades)",     "filtro": "t.es_venta",
                    "campo": "diferencia", "valorizada": False, "signo": -1},
    "ventas_val":  {"etiqueta": "Ventas ($)",            "filtro": "t.es_venta",
                    "campo": "diferencia", "valorizada": True,  "signo": -1},
    "remitos_u":   {"etiqueta": "Remitos recibidos (unidades)",
                    "filtro": "t.categoria = 'Remitido'",
                    "campo": "ingreso",    "valorizada": False, "signo": 1},
    "remitos_val": {"etiqueta": "Remitos recibidos ($)",
                    "filtro": "t.categoria = 'Remitido'",
                    "campo": "ingreso",    "valorizada": True,  "signo": 1},
    "merma_u":     {"etiqueta": "Merma (unidades)",      "filtro": "t.es_merma",
                    "campo": "diferencia", "valorizada": False, "signo": -1},
    "merma_val":   {"etiqueta": "Merma ($)",             "filtro": "t.es_merma",
                    "campo": "diferencia", "valorizada": True,  "signo": -1},
}

# Dimensiones de análisis: "general" (agregados) y por SKU.
DIMENSIONES = {
    "gran_super_rubro": "Gran Super Rubro",
    "super_rubro":      "Super Rubro",
    "rubro":            "Rubro",
    "marca":            "Marca",
    "codigodepo":       "Sucursal",
    "codigo":           "SKU",
}

# MESES: importado de core.comun (se re-exporta para compatibilidad con
# quien todavía importa desde core.inventario.interanual).


def anios_disponibles(proyecto: str) -> list[int]:
    """Años con movimientos cargados, del más reciente al más viejo."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute(
            "SELECT DISTINCT year(fecha) FROM movimientos ORDER BY 1 DESC"
        ).fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def cobertura_meses(
    proyecto: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Qué tan completo está cada mes de cada año: primer y último día con
    movimientos contra los días que tiene el mes.

    Args:
        sucursales: subconjunto de sucursales (None = todas). Importante
                    pasarlo cuando el recorte que se va a comparar está
                    filtrado: una sucursal que arrancó a cargar datos más
                    tarde puede tener meses parciales que la cobertura del
                    proyecto entero no refleja.
        codigos:    subconjunto de SKUs (None = todos), mismo motivo.

    Returns:
        DataFrame: anio, mes, dia_desde, dia_hasta, dias_mes, completo (bool).
        Sirve para avisar que un mes parcial no es comparable de igual a igual.
    """
    conn = conectar(proyecto)
    try:
        filtros = []
        params: dict = {}
        if sucursales:
            filtros.append("codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if codigos:
            filtros.append("codigo IN (SELECT unnest($cods))")
            params["cods"] = list(codigos)
        where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
        df = conn.execute(f"""
            SELECT year(fecha)::INT  AS anio,
                   month(fecha)::INT AS mes,
                   MIN(day(fecha))::INT AS dia_desde,
                   MAX(day(fecha))::INT AS dia_hasta,
                   day(last_day(MAX(fecha)))::INT AS dias_mes
            FROM movimientos
            {where}
            GROUP BY 1, 2 ORDER BY 1, 2
        """, params).df()
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=["anio", "mes", "dia_desde", "dia_hasta",
                                     "dias_mes", "completo"])
    # Tolerancia de un día: casi ningún mes tiene movimientos el 1 (feriado,
    # domingo), y eso no lo vuelve incomparable.
    df["completo"] = (df["dia_desde"] <= 2) & (df["dia_hasta"] >= df["dias_mes"] - 1)
    return df


def _expr(dimension: str) -> str:
    """Expresión SQL de la dimensión: las de catálogo salen del join, no de movimientos."""
    origen = "m" if dimension in ("codigo", "codigodepo") else "c"
    return f"{origen}.{dimension} AS {dimension}"


def _consultar(
    proyecto: str,
    anios: list[int],
    agrupar: list[str],
    meses: list[int] | None,
    metrica: str,
    codigos: list[str] | None,
    sucursales: list[str] | None,
    modo_valorizacion: str,
    fecha_valorizacion: str | None,
    dia_corte: int | None,
    excluir_sucursales: list[str] | None = None,
    meses_dia_corte: list[int] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """
    Agregación en DuckDB por las expresiones de `agrupar` + año.
    `agrupar` son expresiones SQL ya formadas (ver `_expr`).
    Devuelve (df, snapshot de precios usado).

    `meses_dia_corte` acota el recorte de `dia_corte` a esos meses (los
    incompletos); el resto de los meses de `meses` quedan enteros. None =
    `dia_corte` se aplica a todos los meses (comportamiento histórico).
    """
    cfg = METRICAS[metrica]
    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"

    conn = conectar(proyecto)
    try:
        cat = catalogo_articulos(proyecto)
        conn.register("_cat", cat)
        conn.register("_precios", precios_snapshot(conn, fecha_valorizacion)
                      [["codigo", unit_col]].rename(columns={unit_col: "precio"}))
        fs = snapshot_usado(conn, fecha_valorizacion)

        filtros = ["year(m.fecha) IN (SELECT unnest($anios))", cfg["filtro"]]
        params: dict = {"anios": [int(a) for a in anios]}
        if meses:
            filtros.append("month(m.fecha) IN (SELECT unnest($meses))")
            params["meses"] = [int(x) for x in meses]
        if dia_corte:
            if meses_dia_corte:
                filtros.append(
                    "(month(m.fecha) NOT IN (SELECT unnest($meses_corte)) "
                    "OR day(m.fecha) <= $dia_corte)")
                params["meses_corte"] = [int(x) for x in meses_dia_corte]
            else:
                filtros.append("day(m.fecha) <= $dia_corte")
            params["dia_corte"] = int(dia_corte)
        if sucursales:
            filtros.append("m.codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if excluir_sucursales:
            filtros.append("m.codigodepo NOT IN (SELECT unnest($excl))")
            params["excl"] = list(excluir_sucursales)
        if codigos:
            conn.register("_codigos", pd.DataFrame(
                {"codigo": pd.unique(pd.Series(list(codigos)))}))
            filtros.append("m.codigo IN (SELECT codigo FROM _codigos)")

        # El valor de la métrica: unidades, o unidades × precio del snapshot
        # (mismo precio para todos los años → precios constantes).
        campo = f"m.{cfg['campo']}"
        valor = (f"SUM({campo} * COALESCE(p.precio, 0))"
                 if cfg["valorizada"] else f"SUM({campo})")
        cols = ", ".join(agrupar)

        df = conn.execute(f"""
            SELECT {cols}, year(m.fecha)::INT AS anio,
                   ({cfg['signo']} * {valor})::DOUBLE AS valor,
                   COUNT(DISTINCT m.codigo)::INT      AS skus
            FROM movimientos m
            JOIN tipos_categoria t ON m.tipo = t.tipo
            LEFT JOIN _cat c     ON m.codigo = c.codigo
            LEFT JOIN _precios p ON m.codigo = p.codigo
            WHERE {' AND '.join(filtros)}
            GROUP BY ALL
        """, params).df()
    finally:
        conn.close()
    return df, fs


def comparar_anios(
    proyecto: str,
    anios: list[int],
    dimension: str = "gran_super_rubro",
    meses: list[int] | None = None,
    metrica: str = "ventas_u",
    codigos: list[str] | None = None,
    sucursales: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
    meses_dia_corte: list[int] | None = None,
) -> pd.DataFrame:
    """
    Compara una métrica entre varios años, abierta por la dimensión elegida.

    Args:
        proyecto:           contexto.
        anios:              años a comparar (2 o más; salen de anios_disponibles).
        dimension:          clave de DIMENSIONES ("rubro", "marca", "codigo"...).
        meses:              meses a incluir (1-12). None = el año completo.
        metrica:            clave de METRICAS.
        codigos:            subconjunto de SKUs (None = todos).
        sucursales:         subconjunto de sucursales (None = todas).
        modo_valorizacion:  "costo" o "lista_1" (sólo para las métricas $).
        fecha_valorizacion: snapshot de precios; el MISMO para todos los años.
        dia_corte:          recorta al día N (para comparar períodos parciales
                            de igual a igual).
        meses_dia_corte:    si se pasa, `dia_corte` sólo recorta esos meses
                            (los incompletos); el resto de `meses` queda
                            entero. None = recorta todos los meses de `meses`.

    Returns:
        DataFrame ancho: una fila por valor de la dimensión, una columna por
        año, más `variacion_pct` y `variacion_abs` (último año contra el
        primero de la lista, ordenados) y `etiqueta` para graficar.
        attrs: anios, meses, metrica, dimension, modo_valorizacion,
        fecha_valorizacion, dia_corte, meses_dia_corte, total_por_anio.
    """
    if metrica not in METRICAS:
        raise ValueError(f"metrica debe ser una de {list(METRICAS)}, no '{metrica}'")
    if dimension not in DIMENSIONES:
        raise ValueError(
            f"dimension debe ser una de {list(DIMENSIONES)}, no '{dimension}'")
    validar_modo_valorizacion(modo_valorizacion)
    anios = sorted({int(a) for a in anios})
    if len(anios) < 2:
        raise ValueError("Hay que elegir al menos dos años para comparar.")

    df, fs = _consultar(proyecto, anios, [_expr(dimension)], meses, metrica, codigos,
                        sucursales, modo_valorizacion, fecha_valorizacion,
                        dia_corte, meses_dia_corte=meses_dia_corte)

    attrs = dict(anios=anios, meses=sorted(meses) if meses else None,
                 metrica=metrica, dimension=dimension,
                 modo_valorizacion=modo_valorizacion, fecha_valorizacion=fs,
                 dia_corte=dia_corte, meses_dia_corte=meses_dia_corte,
                 total_por_anio={})

    cols_anio = [str(a) for a in anios]
    if df.empty:
        vacio = pd.DataFrame(columns=[dimension, "etiqueta", *cols_anio,
                                      "variacion_abs", "variacion_pct", "skus"])
        vacio.attrs.update(attrs)
        return vacio

    attrs["total_por_anio"] = {
        int(a): float(v) for a, v in df.groupby("anio")["valor"].sum().items()}

    skus = df.groupby(dimension, dropna=False)["skus"].max()
    ancho = (df.pivot_table(index=dimension, columns="anio", values="valor",
                            aggfunc="sum", dropna=False)
             .reindex(columns=anios).fillna(0.0))
    ancho.columns = [str(c) for c in ancho.columns]

    base, ultimo = cols_anio[0], cols_anio[-1]
    ancho["variacion_abs"] = ancho[ultimo] - ancho[base]
    # Sin base no hay porcentaje: un alta del último año no es "+∞%". Se
    # divide por abs(base) (no por base) para que el signo del % siempre
    # coincida con el de variacion_abs, aunque el año base haya dado negativo.
    ancho["variacion_pct"] = (ancho["variacion_abs"]
                              / ancho[base].abs().where(ancho[base] != 0) * 100)
    ancho["skus"] = skus

    res = ancho.reset_index()
    res[dimension] = res[dimension].fillna("(sin dato)").astype(str)
    res["etiqueta"] = res[dimension]
    if dimension == "codigo":
        desc = catalogo_articulos(proyecto).drop_duplicates("codigo") \
            .set_index("codigo")["descripcion"]
        res["etiqueta"] = (res["codigo"] + " — "
                           + res["codigo"].map(desc).fillna("").str.slice(0, 45))

    res = (res[[dimension, "etiqueta", *cols_anio, "variacion_abs",
                "variacion_pct", "skus"]]
           .sort_values(ultimo, ascending=False).reset_index(drop=True))
    res.attrs.update(attrs)
    return res


def serie_mensual_anios(
    proyecto: str,
    anios: list[int],
    meses: list[int] | None = None,
    metrica: str = "ventas_u",
    codigos: list[str] | None = None,
    sucursales: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
    excluir_sucursales: list[str] | None = None,
    meses_dia_corte: list[int] | None = None,
) -> pd.DataFrame:
    """
    Serie mes a mes con una línea por año — el gráfico interanual clásico
    (meses en el eje X, un color por año).

    Returns:
        DataFrame largo: anio, mes, mes_nombre, valor. Los meses sin datos
        quedan en 0 para que las líneas no se corten.
    """
    anios = sorted({int(a) for a in anios})
    df, _ = _consultar(proyecto, anios, ["month(m.fecha)::INT AS mes"], meses,
                       metrica, codigos, sucursales, modo_valorizacion,
                       fecha_valorizacion, dia_corte, excluir_sucursales,
                       meses_dia_corte=meses_dia_corte)

    cols = ["anio", "mes", "mes_nombre", "valor"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    pedidos = sorted(meses) if meses else list(range(1, 13))
    grilla = pd.MultiIndex.from_product([anios, pedidos], names=["anio", "mes"])
    serie = (df.groupby(["anio", "mes"])["valor"].sum()
             .reindex(grilla, fill_value=0.0).reset_index())
    serie["mes_nombre"] = serie["mes"].map(MESES)
    return serie[cols]


def serie_mensual_sucursales(
    proyecto: str,
    anios: list[int],
    sucursales: list[str],
    meses: list[int] | None = None,
    metrica: str = "ventas_u",
    codigos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
    meses_dia_corte: list[int] | None = None,
) -> pd.DataFrame:
    """
    Serie mes a mes abierta por sucursal Y por año, para cruzar sucursales
    entre sí: permite ver si una caída es generalizada o de unas pocas bocas.

    A diferencia de `serie_mensual_anios`, que agrega todas las sucursales,
    acá cada (sucursal, año) es una serie propia.

    Returns:
        DataFrame largo: codigodepo, anio, mes, mes_nombre, valor. Los meses
        sin datos quedan en 0 para que las líneas no se corten.
    """
    anios = sorted({int(a) for a in anios})
    sucs = list(sucursales)
    cols = ["codigodepo", "anio", "mes", "mes_nombre", "valor"]
    if not sucs:
        return pd.DataFrame(columns=cols)

    df, _ = _consultar(
        proyecto, anios,
        ["month(m.fecha)::INT AS mes", _expr("codigodepo")],
        meses, metrica, codigos, sucs, modo_valorizacion,
        fecha_valorizacion, dia_corte, meses_dia_corte=meses_dia_corte)
    if df.empty:
        return pd.DataFrame(columns=cols)

    pedidos = sorted(meses) if meses else list(range(1, 13))
    grilla = pd.MultiIndex.from_product(
        [sucs, anios, pedidos], names=["codigodepo", "anio", "mes"])
    serie = (df.groupby(["codigodepo", "anio", "mes"])["valor"].sum()
             .reindex(grilla, fill_value=0.0).reset_index())
    serie["mes_nombre"] = serie["mes"].map(MESES)
    return serie[cols]


def ventas_vs_remitos(
    proyecto: str,
    anios: list[int],
    meses: list[int] | None = None,
    valorizado: bool = False,
    codigos: list[str] | None = None,
    sucursales: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    dia_corte: int | None = None,
    meses_dia_corte: list[int] | None = None,
    excluir_depositos: list[str] | tuple[str, ...] | None = DEPOSITOS_DISTRIBUCION,
) -> pd.DataFrame:
    """
    Cruza ventas contra reposición recibida, mes a mes y por año: sirve para
    ver cuánto de una caída (o suba) de ventas viene acompañada de un cambio
    en la distribución.

    Ojo con la lectura: esto muestra que las dos series se mueven juntas o no,
    **no prueba causalidad**. Menos reposición puede explicar menos venta, y
    también al revés (se repone menos porque se vende menos).

    Args:
        valorizado:        False compara unidades, True compara pesos a
                           precios constantes (ver el encabezado del módulo).
        excluir_depositos: por defecto saca los centros de distribución. Es
                           importante: el ingreso al CD desde el proveedor no
                           es reposición que le llegue a una boca, y mezclarlo
                           distorsiona la lectura (en 2026 el ingreso al CD
                           subió 22% mientras el de las sucursales quedó
                           plano). Pasá None para incluir todo.
        (el resto igual que comparar_anios)

    Returns:
        DataFrame largo: anio, mes, mes_nombre, ventas, remitos, cobertura
        (remitos / ventas: cuántas unidades entraron por cada una vendida).
        attrs: totales (por año, de ambas series), variaciones (% del último
        año contra el primero), brecha (variación de remitos − la de ventas,
        en puntos porcentuales) y excluidos.
    """
    anios = sorted({int(a) for a in anios})
    excluidos = list(excluir_depositos or [])
    comun = dict(meses=meses, codigos=codigos, sucursales=sucursales,
                 modo_valorizacion=modo_valorizacion,
                 fecha_valorizacion=fecha_valorizacion, dia_corte=dia_corte,
                 meses_dia_corte=meses_dia_corte,
                 excluir_sucursales=excluidos or None)
    suf = "val" if valorizado else "u"

    ventas = serie_mensual_anios(proyecto, anios, metrica=f"ventas_{suf}", **comun)
    remitos = serie_mensual_anios(proyecto, anios, metrica=f"remitos_{suf}", **comun)

    cols = ["anio", "mes", "mes_nombre", "ventas", "remitos", "cobertura"]
    if ventas.empty and remitos.empty:
        return pd.DataFrame(columns=cols)

    df = (ventas.rename(columns={"valor": "ventas"})
          .merge(remitos.rename(columns={"valor": "remitos"}),
                 on=["anio", "mes", "mes_nombre"], how="outer")
          .fillna({"ventas": 0.0, "remitos": 0.0})
          .sort_values(["anio", "mes"]))
    # Sin ventas no hay cobertura que calcular (dividir por cero no dice nada).
    df["cobertura"] = df["remitos"] / df["ventas"].where(df["ventas"] != 0)

    def _tot(col):
        return {int(a): float(v) for a, v in df.groupby("anio")[col].sum().items()}

    def _var(tot):
        base, ult = tot.get(anios[0], 0.0), tot.get(anios[-1], 0.0)
        return (ult - base) / abs(base) * 100 if base else None

    t_v, t_r = _tot("ventas"), _tot("remitos")
    v_v, v_r = _var(t_v), _var(t_r)
    df.attrs.update(
        anios=anios, valorizado=valorizado, excluidos=excluidos,
        totales={"ventas": t_v, "remitos": t_r},
        variaciones={"ventas": v_v, "remitos": v_r},
        brecha=(v_r - v_v) if (v_v is not None and v_r is not None) else None,
    )
    return df[cols].reset_index(drop=True)
