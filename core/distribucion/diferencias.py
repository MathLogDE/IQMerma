"""
core/distribucion/diferencias.py — Diferencias de camión (movimientos MD)

Cuando llega un camión y lo que se recibe no coincide con lo que se despachó,
el ERP registra la diferencia como un movimiento **REM / MD**: mueve las
unidades en disputa entre los dos depósitos involucrados. Cada remito MD tiene
exactamente dos patas —una con `egreso` (quien pierde las unidades) y otra con
`ingreso` (quien las recibe)— por el mismo SKU y la misma cantidad, así que el
remito neto siempre da cero.

Sentido, mirando siempre desde el centro de distribución (001 / 002):

    sucursal → depósito   FALTANTE  la sucursal recibió menos de lo despachado
    depósito → sucursal   SOBRANTE  la sucursal recibió de más

⚠️ La columna `movimientos.diferencia` NO sirve para los MD. El ERP exporta el
egreso en negativo solo en este tipo, y la ingesta calcula
`diferencia = ingreso − egreso`, con lo que la pata de egreso queda con signo
positivo (un egreso de 1 aparece como +1). Por eso acá el sentido y las
unidades se reconstruyen desde `ingreso` / `egreso`, nunca desde `diferencia`.

Uso:
    from core.distribucion.diferencias import (
        diferencias_camion, resumen_diferencias, total_transferido,
        resumen_transferido, agregar_contraste_transferido,
    )

    df = diferencias_camion("cliente_x", "2026-01-01", "2026-06-30")
    por_suc = resumen_diferencias(df, por="sucursal")

    # Contraste contra el volumen real remitido (RE + RI), para saber si la
    # diferencia es grande o chica en relación a lo que se movió:
    transf = total_transferido("cliente_x", "2026-01-01", "2026-06-30")
    por_suc_contraste = agregar_contraste_transferido(por_suc, transf, "sucursal")
"""

import pandas as pd

from core.comun import (
    conectar, adjuntar_rubros, precios_snapshot, snapshot_usado,
    validar_modo_valorizacion, DEPOSITOS_DISTRIBUCION,
)

# Re-exportado desde core.comun: las diferencias se miran contra estos depósitos.
__all__ = ["diferencias_camion", "resumen_diferencias", "evolucion_diferencias",
           "total_transferido", "resumen_transferido", "agregar_contraste_transferido",
           "DEPOSITOS_DISTRIBUCION", "FALTANTE", "SOBRANTE", "ENTRE_DEPOSITOS"]

FALTANTE = "Faltante"
SOBRANTE = "Sobrante"
ENTRE_DEPOSITOS = "Entre depósitos"


def diferencias_camion(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    depositos: tuple[str, ...] | list[str] = DEPOSITOS_DISTRIBUCION,
    sucursales: list[str] | None = None,
    excluir_sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
    incluir_entre_depositos: bool = True,
) -> pd.DataFrame:
    """
    Detalle de las diferencias de camión del período, una fila por
    (remito, SKU).

    Args:
        proyecto:                contexto.
        fecha_desde/hasta:       rango.
        depositos:               centros de distribución contra los que se mide.
        sucursales:              subconjunto de sucursales (None = todas).
        excluir_sucursales:      sucursales que quedan fuera del análisis (se
                                 aplica después de `sucursales`, así que gana
                                 la exclusión). Útil para sacar depósitos de
                                 roturas, outlets o e-commerce que ensucian el
                                 promedio.
        codigos:                 subconjunto de SKUs (None = todos).
        modo_valorizacion:       "costo" o "lista_1" (los MD vienen con costo 0
                                 en el ERP, así que se valorizan con el snapshot).
        fecha_valorizacion:      snapshot de precios (default: el último).
        incluir_entre_depositos: sumar también los movimientos 001 ↔ 002.

    Returns:
        DataFrame: fecha, numero, deposito, sucursal, sentido, codigo,
        descripcion, rubro, marca, gran_super_rubro, unidades (siempre
        positivas), precio_unitario, valorizado, usuario.
        attrs: desde, hasta, depositos, modo_valorizacion, fecha_valorizacion,
        excluidas (las sucursales excluidas), pares_sin_deposito (los MD entre
        dos sucursales, que quedan afuera) y pares_excluidos (cuántas filas se
        descartaron por la exclusión).
    """
    validar_modo_valorizacion(modo_valorizacion)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
    depos = list(depositos)

    conn = conectar(proyecto)
    try:
        # Cada remito MD tiene dos patas por SKU: la de egreso es el origen y
        # la de ingreso el destino. El sentido se deduce de ahí, no del signo
        # de `diferencia` (ver nota del encabezado).
        # Se agrupa por (numero, codigo) sin fecha en la clave: las dos patas
        # de un mismo remito MD son el mismo evento aunque el ERP les ponga
        # fechas distintas (despacho vs. conciliación), y filtrar la CTE por
        # fecha_desde/hasta acá cortaría una pata que cae justo afuera del
        # rango, dejando a la otra huérfana y a las dos afuera del reporte
        # por el HAVING. Se trae todo el histórico de MD y el rango se aplica
        # después, sobre la fecha representativa del par (la más vieja).
        pares = conn.execute("""
            WITH md AS (
                SELECT numero, fecha, codigo, codigodepo, usuario, ingreso, egreso
                FROM movimientos
                WHERE tipomov = 'REM' AND tipo = 'MD'
            )
            SELECT numero, codigo, MIN(fecha) AS fecha,
                   MAX(codigodepo) FILTER (WHERE ingreso <> 0) AS destino,
                   MAX(codigodepo) FILTER (WHERE egreso  <> 0) AS origen,
                   SUM(ingreso)::DOUBLE                        AS unidades,
                   MAX(usuario)                                AS usuario
            FROM md
            GROUP BY 1, 2
            HAVING MAX(codigodepo) FILTER (WHERE ingreso <> 0) IS NOT NULL
               AND MAX(codigodepo) FILTER (WHERE egreso  <> 0) IS NOT NULL
               AND MIN(fecha) >= $desde::DATE AND MIN(fecha) <= $hasta::DATE
        """, {"desde": desde, "hasta": hasta}).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
        df_val = precios_snapshot(conn, fecha_valorizacion)
        fs = snapshot_usado(conn, fecha_valorizacion)
    finally:
        conn.close()

    cols = ["fecha", "numero", "deposito", "sucursal", "sentido", "codigo",
            "descripcion", "rubro", "marca", "gran_super_rubro", "unidades",
            "precio_unitario", "valorizado", "usuario"]
    excluidas = list(excluir_sucursales or [])
    attrs = dict(desde=desde, hasta=hasta, depositos=depos,
                 modo_valorizacion=modo_valorizacion, fecha_valorizacion=fs,
                 excluidas=excluidas, pares_sin_deposito=0, pares_excluidos=0)

    if pares.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    orig_cd = pares["origen"].isin(depos)
    dest_cd = pares["destino"].isin(depos)

    # Los MD entre dos sucursales no pasan por un centro de distribución: no
    # son diferencias de camión, se cuentan aparte para no perderlos de vista.
    attrs["pares_sin_deposito"] = int((~orig_cd & ~dest_cd).sum())
    pares = pares[orig_cd | dest_cd].copy()
    orig_cd, dest_cd = orig_cd[pares.index], dest_cd[pares.index]

    ambos = orig_cd & dest_cd
    pares["sentido"] = pd.Series(
        pd.NA, index=pares.index, dtype="object").mask(
        dest_cd & ~orig_cd, FALTANTE).mask(
        orig_cd & ~dest_cd, SOBRANTE).mask(ambos, ENTRE_DEPOSITOS)
    # El depósito es la punta que es CD; con 001 ↔ 002 se toma el origen.
    pares["deposito"] = pares["origen"].where(orig_cd, pares["destino"])
    pares["sucursal"] = pares["destino"].where(orig_cd & ~ambos, pares["origen"])

    if not incluir_entre_depositos:
        pares = pares[pares["sentido"] != ENTRE_DEPOSITOS]
    if sucursales:
        pares = pares[pares["sucursal"].isin(list(sucursales))]
    if excluidas:
        fuera = pares["sucursal"].isin(excluidas)
        attrs["pares_excluidos"] = int(fuera.sum())
        pares = pares[~fuera]
    if codigos:
        pares = pares[pares["codigo"].isin(list(codigos))]

    if pares.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    df = pares.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    # Los MD vienen con costo 0 en el ERP: se valorizan con el snapshot.
    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"
    df = df.merge(df_val[["codigo", unit_col]], on="codigo", how="left")
    df["precio_unitario"] = pd.to_numeric(df[unit_col], errors="coerce")
    df["valorizado"] = df["unidades"] * df["precio_unitario"]
    df["fecha"] = pd.to_datetime(df["fecha"])

    df = df[cols].sort_values(["fecha", "numero", "codigo"]).reset_index(drop=True)
    df.attrs.update(attrs)
    return df


def resumen_diferencias(det: pd.DataFrame, por: str = "sucursal") -> pd.DataFrame:
    """
    Agrupa el detalle y contrapone faltantes contra sobrantes.

    Args:
        det: salida de diferencias_camion.
        por: columna de agrupación ("sucursal", "rubro", "gran_super_rubro",
             "marca", "usuario", "codigo"...).

    Returns:
        DataFrame por grupo: faltante_u/$, sobrante_u/$, neto_u/$
        (faltante − sobrante: positivo = falta mercadería), remitos y skus.
        Ordenado por faltante valorizado.
    """
    base = ["faltante_u", "sobrante_u", "neto_u", "faltante_val",
            "sobrante_val", "neto_val", "remitos", "skus"]
    if det.empty:
        return pd.DataFrame(columns=[por] + base)
    if por not in det.columns:
        raise ValueError(f"No se puede agrupar por '{por}': no está en el detalle.")

    d = det.copy()
    es_falt = d["sentido"] == FALTANTE
    es_sobr = d["sentido"] == SOBRANTE
    d["faltante_u"] = d["unidades"].where(es_falt, 0)
    d["sobrante_u"] = d["unidades"].where(es_sobr, 0)
    d["faltante_val"] = d["valorizado"].where(es_falt, 0)
    d["sobrante_val"] = d["valorizado"].where(es_sobr, 0)

    g = d.groupby(por, dropna=False)
    res = pd.DataFrame({
        "faltante_u":   g["faltante_u"].sum(),
        "sobrante_u":   g["sobrante_u"].sum(),
        "faltante_val": g["faltante_val"].sum(),
        "sobrante_val": g["sobrante_val"].sum(),
        "remitos":      g["numero"].nunique(),
        "skus":         g["codigo"].nunique(),
    })
    res["neto_u"] = res["faltante_u"] - res["sobrante_u"]
    res["neto_val"] = res["faltante_val"] - res["sobrante_val"]

    res = (res.reset_index()[[por] + base]
           .sort_values("faltante_val", ascending=False)
           .reset_index(drop=True))
    res.attrs.update(det.attrs)
    return res


def evolucion_diferencias(det: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """
    Serie temporal de faltantes y sobrantes (default: mensual) para ver si la
    cosa mejora o empeora.

    Args:
        det:  salida de diferencias_camion.
        freq: frecuencia de Period ("M" mensual, "W" semanal, "D" diaria).

    Returns:
        DataFrame: periodo, faltante_u, sobrante_u, neto_u, faltante_val,
        sobrante_val, neto_val, remitos.
    """
    cols = ["periodo", "faltante_u", "sobrante_u", "neto_u",
            "faltante_val", "sobrante_val", "neto_val", "remitos"]
    if det.empty:
        return pd.DataFrame(columns=cols)

    d = det.copy()
    d["periodo"] = d["fecha"].dt.to_period(freq).dt.start_time
    res = resumen_diferencias(d, por="periodo")
    return (res[[c for c in cols if c in res.columns]]
            .sort_values("periodo").reset_index(drop=True))


def total_transferido(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    excluir_sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Unidades remitidas por **RE** (remito externo) + **RI** (remito interno)
    en el período, por sucursal y SKU — el volumen real que se movió, para
    contrastar contra `diferencias_camion` (¿la diferencia es grande o chica
    en relación a lo transferido?). Deja afuera MD a propósito: es la
    diferencia que se está contrastando, no el denominador.

    Mismo criterio de `core.inventario.reposicion._unidades_remitidas_por_codigo`
    (sumar `ingreso`, nunca `diferencia`), pero sin incluir MD y agrupado
    también por sucursal, no solo por SKU.

    Args:
        sucursales/excluir_sucursales/codigos: mismos filtros que
            `diferencias_camion`, para que el denominador use el mismo
            recorte que el numerador.
        modo_valorizacion/fecha_valorizacion: igual que `diferencias_camion`.

    Returns:
        DataFrame: sucursal, codigo, descripcion, rubro, marca,
        gran_super_rubro, unidades, precio_unitario, valorizado.
        attrs: desde, hasta, modo_valorizacion, fecha_valorizacion.
    """
    validar_modo_valorizacion(modo_valorizacion)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    cols = ["sucursal", "codigo", "descripcion", "rubro", "marca",
            "gran_super_rubro", "unidades", "precio_unitario", "valorizado"]
    attrs = dict(desde=desde, hasta=hasta, modo_valorizacion=modo_valorizacion,
                 fecha_valorizacion=None)

    conn = conectar(proyecto)
    try:
        filtros = ["tipo IN ('RE', 'RI')",
                   "fecha >= $desde::DATE", "fecha <= $hasta::DATE"]
        params: dict = {"desde": desde, "hasta": hasta}
        if sucursales:
            filtros.append("codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if excluir_sucursales:
            filtros.append("codigodepo NOT IN (SELECT unnest($excl))")
            params["excl"] = list(excluir_sucursales)
        if codigos:
            filtros.append("codigo IN (SELECT unnest($cods))")
            params["cods"] = list(codigos)

        df = conn.execute(f"""
            SELECT codigodepo AS sucursal, codigo, SUM(ingreso)::DOUBLE AS unidades
            FROM movimientos
            WHERE {' AND '.join(filtros)}
            GROUP BY codigodepo, codigo
        """, params).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
        df_val = precios_snapshot(conn, fecha_valorizacion)
        fs = snapshot_usado(conn, fecha_valorizacion)
    finally:
        conn.close()

    attrs["fecha_valorizacion"] = fs
    if df.empty:
        vacio = pd.DataFrame(columns=cols)
        vacio.attrs.update(attrs)
        return vacio

    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"
    df = df.merge(df_val[["codigo", unit_col]], on="codigo", how="left")
    df["precio_unitario"] = pd.to_numeric(df[unit_col], errors="coerce")
    df["valorizado"] = df["unidades"] * df["precio_unitario"]

    df = df[cols].reset_index(drop=True)
    df.attrs.update(attrs)
    return df


def resumen_transferido(transf: pd.DataFrame, por: str = "sucursal") -> pd.DataFrame:
    """
    Agrupa `total_transferido` por la dimensión pedida ("sucursal" o
    "codigo"): total de unidades y $ transferidos.

    Returns:
        DataFrame: [por], transferido_u, transferido_val.
    """
    cols = [por, "transferido_u", "transferido_val"]
    if transf.empty:
        return pd.DataFrame(columns=cols)
    if por not in transf.columns:
        raise ValueError(f"No se puede agrupar por '{por}': no está en el detalle.")

    g = transf.groupby(por, dropna=False)
    res = pd.DataFrame({
        "transferido_u":   g["unidades"].sum(),
        "transferido_val": g["valorizado"].sum(),
    }).reset_index()
    return res[cols]


def agregar_contraste_transferido(
    res: pd.DataFrame, transf: pd.DataFrame, por: str,
) -> pd.DataFrame:
    """
    Suma a la salida de `resumen_diferencias` el contraste contra el total
    transferido (RE + RI) de la misma dimensión: cuánto representa el
    faltante/sobrante sobre el volumen que efectivamente se movió.

    Sin transferido registrado, el % queda en `pd.NA` (no en 0) — no hay que
    sugerir que la diferencia es insignificante cuando en realidad no hay
    con qué compararla (mismo criterio que `pct_merma_sobre_ventas` en
    `core.merma.analisis`).

    Returns:
        `res` + transferido_u, transferido_val, pct_faltante_u,
        pct_sobrante_u, pct_faltante_val, pct_sobrante_val.
    """
    t = resumen_transferido(transf, por=por)
    out = res.merge(t, on=por, how="left")
    out["transferido_u"] = out["transferido_u"].fillna(0.0)
    out["transferido_val"] = out["transferido_val"].fillna(0.0)

    for base, denom in (("faltante_u", "transferido_u"), ("sobrante_u", "transferido_u"),
                        ("faltante_val", "transferido_val"), ("sobrante_val", "transferido_val")):
        pct_col = f"pct_{base}"
        out[pct_col] = pd.NA
        m = out[denom] > 0
        out.loc[m, pct_col] = (out.loc[m, base] / out.loc[m, denom] * 100).round(2)

    return out
