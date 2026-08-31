"""
core/merma/avance.py — Avance de conteo de inventario (movimientos tipo='INV')

Complementa core/merma/analisis.py: mientras ese módulo mide "cuánta plata se
perdió", este mide "qué tan avanzado está el conteo físico" — por sucursal y
rubro (avance_matriz) y como totales de unidades/diferencia del conteo en sí
(avance_conteo). Siempre un rango fijo fecha_desde/fecha_hasta (no hay corte
dinámico acá).

Modelo:
  - "Contado" = tuvo al menos una línea tipo='INV' en el rango (presencia, no
    neto — mismo criterio que calcular_merma's tuvo_inv/pct_skus_contados).
  - % avance = SKUs contados / SKUs esperados, por (nivel, sucursal). Dos
    métodos de denominador (`metodo_denominador`):
      "movimientos": SKUs con CUALQUIER movimiento en el rango, en esa
                     sucursal (mismo criterio que pct_skus_contados).
      "catalogo":    SKUs activos del catálogo en ese nivel (`activo == True`
                     estricto) — mismo valor para todas las sucursales,
                     porque no existe una tabla de surtido por sucursal.
  - avance_conteo() está acotado SOLO a líneas tipo='INV' (no a toda la
    merma del período). "Unidades contadas" = SUM(ingreso) en esas líneas
    (la cantidad física registrada por el conteo); "diferencia" =
    -SUM(diferencia) (mismo signo que el resto de la app: faltante
    positivo), valorizada con el snapshot de precios. Esta lectura de
    ingreso/egreso para INV no está documentada en el ERP; se infiere del
    modelo general diferencia = ingreso - egreso (ver ingesta/movimientos.py):
    egreso = stock teórico reemplazado, ingreso = cantidad contada.
"""

import pandas as pd

from core.comun import (  # noqa: E402
    MODOS_VALORIZACION_MERMA,
    conectar,
    validar_modo_valorizacion,
    catalogo_articulos,
    precios_snapshot,
    precio_unitario_fila,
    snapshot_usado,
    adjuntar_rubros,
)

NIVELES_AVANCE = ("rubro", "super_rubro", "gran_super_rubro")
METODOS_DENOMINADOR = ("movimientos", "catalogo")


def _validar_nivel(nivel: str) -> None:
    if nivel not in NIVELES_AVANCE:
        raise ValueError(f"nivel debe ser uno de {NIVELES_AVANCE}, no '{nivel}'")


def _validar_metodo(metodo: str) -> None:
    if metodo not in METODOS_DENOMINADOR:
        raise ValueError(
            f"metodo_denominador debe ser uno de {METODOS_DENOMINADOR}, no '{metodo}'"
        )


# ---------------------------------------------------------------------------
# Matriz nivel × sucursal
# ---------------------------------------------------------------------------

def avance_matriz(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    nivel: str = "rubro",
    metodo_denominador: str = "movimientos",
    codigos: list[str] | None = None,
    codigodepo: str | list[str] | None = None,
) -> pd.DataFrame:
    """
    Matriz de % de avance de conteo: una fila por valor de `nivel`, una
    columna por sucursal (`codigodepo`). Grid siempre completo — toda
    combinación (nivel_valor, sucursal) aparece, incluso sin datos (pct_avance
    = pd.NA en ese caso, nunca una celda faltante).

    Args:
        nivel:               "rubro" | "super_rubro" | "gran_super_rubro".
        metodo_denominador:  "movimientos" (SKUs con cualquier movimiento en
                              el rango, por sucursal) o "catalogo" (SKUs
                              activos del catálogo en ese nivel, igual para
                              todas las sucursales).
        codigos:              subconjunto opcional de SKUs (filtro de
                              catálogo ya resuelto por la UI). None = todos.
        codigodepo:           sucursal, lista, o None = todas las de
                              `depositos` (no solo las que tuvieron
                              movimientos, para no ocultar sucursales sin
                              actividad en el rango).

    Returns:
        DataFrame ancho: nivel_valor, <codigodepo_1>, <codigodepo_2>, ...
        df.attrs: nivel, metodo_denominador, fecha_desde, fecha_hasta,
        sucursales (lista de {codigodepo, etiqueta} en el orden de las
        columnas — etiqueta = depositos.abreviacion o codigodepo si no hay),
        detalle (long: nivel_valor, codigodepo, skus_contados, skus_esperados,
        pct_avance).
    """
    _validar_nivel(nivel)
    _validar_metodo(metodo_denominador)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())

    cat = catalogo_articulos(proyecto)
    if codigos is not None:
        cat = cat[cat["codigo"].isin(set(codigos))]
    cat = cat.copy()
    cat[nivel] = cat[nivel].fillna("Sin clasificar")
    codigos_scope = cat["codigo"].tolist()

    depos_filtro = None
    if codigodepo:
        depos_filtro = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos_filtro else ""
    params: dict = {"desde": desde, "hasta": hasta, "codigos": codigos_scope}
    if depos_filtro:
        params["depos"] = depos_filtro

    conn = conectar(proyecto)
    try:
        pares = conn.execute(f"""
            SELECT codigo, codigodepo,
                   MAX(CASE WHEN tipo = 'INV' THEN 1 ELSE 0 END) AS contado
            FROM movimientos
            WHERE fecha >= $desde::DATE AND fecha <= $hasta::DATE
              AND codigo IN (SELECT unnest($codigos))
              {filtro_depo}
            GROUP BY codigo, codigodepo
        """, params).df()

        if depos_filtro:
            df_dep = conn.execute("""
                SELECT codigodepo, abreviacion FROM depositos
                WHERE codigodepo IN (SELECT unnest($depos)) ORDER BY codigodepo
            """, {"depos": depos_filtro}).df()
        else:
            df_dep = conn.execute(
                "SELECT codigodepo, abreviacion FROM depositos ORDER BY codigodepo"
            ).df()
    finally:
        conn.close()

    depos = df_dep["codigodepo"].tolist()
    nivel_valores = sorted(cat[nivel].unique())

    if not nivel_valores or not depos:
        vacio = pd.DataFrame(columns=["nivel_valor"] + depos)
        vacio.attrs.update(nivel=nivel, metodo_denominador=metodo_denominador,
                          fecha_desde=desde, fecha_hasta=hasta, sucursales=[])
        return vacio

    g = pares.merge(cat[["codigo", nivel]], on="codigo", how="inner")
    contados_g = (g.groupby([nivel, "codigodepo"])["contado"].sum()
                 .rename("skus_contados").reset_index())
    esperado_mov = (g.groupby([nivel, "codigodepo"])["codigo"].nunique()
                   .rename("skus_esperados").reset_index())

    grid = pd.MultiIndex.from_product(
        [nivel_valores, depos], names=[nivel, "codigodepo"]
    ).to_frame(index=False)
    grid = grid.merge(contados_g, on=[nivel, "codigodepo"], how="left")
    grid["skus_contados"] = grid["skus_contados"].fillna(0).astype(int)

    if metodo_denominador == "movimientos":
        grid = grid.merge(esperado_mov, on=[nivel, "codigodepo"], how="left")
    else:
        # "catalogo": denominador = SKUs activos del nivel, mismo valor en
        # todas las sucursales (broadcast vía merge sin codigodepo).
        denom_cat = (cat[cat["activo"] == True].groupby(nivel)["codigo"].nunique()
                    .rename("skus_esperados").reset_index())
        grid = grid.merge(denom_cat, on=nivel, how="left")
    grid["skus_esperados"] = grid["skus_esperados"].fillna(0).astype(int)

    grid["pct_avance"] = pd.NA
    m = grid["skus_esperados"] > 0
    grid.loc[m, "pct_avance"] = (
        grid.loc[m, "skus_contados"] / grid.loc[m, "skus_esperados"] * 100
    ).round(2)

    etiquetas_crudas = {
        c: (a if isinstance(a, str) and a.strip() else c)
        for c, a in zip(df_dep["codigodepo"], df_dep["abreviacion"])
    }
    # abreviacion no es única (varios depósitos pueden compartir la misma,
    # p.ej. un valor por defecto sin cargar) — desambiguar agregando el
    # código a cada repetida, para no generar columnas duplicadas.
    repetidas = pd.Series(list(etiquetas_crudas.values())).value_counts()
    etiquetas = {
        c: (f"{a} ({c})" if repetidas.get(a, 0) > 1 else a)
        for c, a in etiquetas_crudas.items()
    }

    wide = grid.set_index([nivel, "codigodepo"])["pct_avance"].unstack("codigodepo")
    wide = wide.reindex(index=nivel_valores, columns=depos)
    wide = wide.reset_index().rename(columns={nivel: "nivel_valor"})
    wide.attrs.update(
        nivel=nivel, metodo_denominador=metodo_denominador,
        fecha_desde=desde, fecha_hasta=hasta,
        sucursales=[{"codigodepo": c, "etiqueta": etiquetas.get(c, c)} for c in depos],
        detalle=grid,
    )
    return wide


# ---------------------------------------------------------------------------
# Conteo y diferencias (solo tipo='INV', totalizado)
# ---------------------------------------------------------------------------

def avance_conteo(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    codigos: list[str] | None = None,
    codigodepo: str | list[str] | None = None,
) -> pd.DataFrame:
    """
    Unidades contadas y diferencia (unidades + valorizada) de los movimientos
    tipo='INV' en el rango, por SKU. `codigodepo` es siempre un filtro (WHERE
    codigodepo IN (...)) — nunca una dimensión de agrupación: una sucursal o
    varias, el resultado no las distingue (quien consuma esto debe totalizar,
    no desagregar por sucursal).

    No restringe a códigos presentes en `articulos` (a diferencia de
    avance_matriz, que sí lo necesita para clasificar por rubro) — un
    movimiento de un código desconocido en el catálogo igual cuenta acá, con
    descripción/rubro en blanco.

    Returns:
        DataFrame por SKU: codigo, descripcion, rubro, marca, super_rubro,
        gran_super_rubro, unidades_contadas, diferencia_unidades,
        valor_unitario, diferencia_valorizada.
        attrs: fecha_valorizacion (snapshot usado), fecha_desde, fecha_hasta,
        modo_valorizacion.
    """
    validar_modo_valorizacion(modo_valorizacion, MODOS_VALORIZACION_MERMA)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())

    depos_filtro = None
    if codigodepo:
        depos_filtro = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos_filtro else ""
    filtro_cod = "AND codigo IN (SELECT unnest($codigos))" if codigos else ""
    params: dict = {"desde": desde, "hasta": hasta}
    if depos_filtro:
        params["depos"] = depos_filtro
    if codigos:
        params["codigos"] = list(codigos)

    cols_vacio = ["codigo", "descripcion", "rubro", "marca", "super_rubro",
                 "gran_super_rubro", "unidades_contadas", "diferencia_unidades",
                 "valor_unitario", "diferencia_valorizada"]

    conn = conectar(proyecto)
    try:
        df = conn.execute(f"""
            SELECT codigo,
                   SUM(ingreso)    AS unidades_contadas,
                   SUM(diferencia) AS neto_diferencia
            FROM movimientos
            WHERE tipo = 'INV'
              AND fecha >= $desde::DATE AND fecha <= $hasta::DATE
              {filtro_depo}
              {filtro_cod}
            GROUP BY codigo
        """, params).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

        if df.empty:
            vacio = pd.DataFrame(columns=cols_vacio)
            vacio.attrs.update(fecha_valorizacion=fv_usada, fecha_desde=desde,
                              fecha_hasta=hasta, modo_valorizacion=modo_valorizacion)
            return vacio

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    df["diferencia_unidades"] = -df["neto_diferencia"]
    df = df.drop(columns=["neto_diferencia"])
    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)
    df["valor_unitario"] = precio_unitario_fila(df["codigo"], modo_valorizacion, df_val)
    df["diferencia_valorizada"] = df["diferencia_unidades"] * df["valor_unitario"]

    df.attrs.update(fecha_valorizacion=fv_usada, fecha_desde=desde, fecha_hasta=hasta,
                    modo_valorizacion=modo_valorizacion)
    return df
