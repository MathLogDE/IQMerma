"""
core/inventario/series.py — Reconstrucción diaria del stock (serie de tiempo)

Ancla el stock conocido de un snapshot y camina los movimientos día a día
(las ventas restan, los remitos suman, los ajustes suman/restan según signo)
para estimar cómo evolucionó el stock. No es exacto —depende de que los
movimientos estén completos— pero alcanza para detectar **períodos de quiebre**
y tramos de stock muy bajo o muy alto.

    stock(d) = stock(D) + Σ movimientos en (D, d]        si d > D
    stock(d) = stock(D) − Σ movimientos en (d, D]        si d < D

donde D es la fecha del snapshot. Como `diferencia` ya viene con signo
(ingreso − egreso), la fórmula unificada es:

    stock(d) = stock(D) + acum(d) − acum(D)

con `acum` = suma acumulada de `diferencia` ordenada por fecha.

La serie se devuelve **como función escalón**: solo hay una fila por día con
movimiento; entre medio el stock se mantiene. Eso la hace liviana aun con
miles de SKUs y permite medir la duración exacta de cada tramo (días en
quiebre) con `vigencia_dias`.

Uso:
    from core.inventario.series import serie_stock, resumen_quiebres

    serie = serie_stock("cliente_x", "008", "2026-01-01", "2026-06-30")
    resumen = resumen_quiebres(serie)
"""

import pandas as pd

from core.comun import conectar, adjuntar_rubros, catalogo_articulos, MESES

# Tipos de movimiento que se marcan sobre la curva para explicar los saltos:
# remitos (reposición), inventario físico, ajustes de merma y transformaciones
# de SKU — mismos cuatro tipos que TIPOS_MARCA_SUC (más abajo), pero con su
# propia paleta: acá compiten visualmente con la curva de un solo SKU, no con
# el agregado de toda una sucursal.
TIPOS_MARCA = {
    "REM": {"etiqueta": "Remito", "color": "#748ffc", "simbolo": "x"},
    "INV": {"etiqueta": "Inventario físico", "color": "#e64980", "simbolo": "x-open"},
    "CS":  {"etiqueta": "Ajuste (merma)", "color": "#ffa94d", "simbolo": "x-open"},
    "TR":  {"etiqueta": "Transformación", "color": "#e599f7", "simbolo": "diamond-open"},
}


def serie_stock(
    proyecto: str,
    codigodepo: str | list[str],
    fecha_desde: str,
    fecha_hasta: str,
    fecha_stock: str | None = None,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Reconstruye la evolución diaria del stock por SKU y sucursal.

    Args:
        proyecto:             contexto.
        codigodepo:           una sucursal o una lista (para el cruce entre
                              sucursales). Cada una se reconstruye por
                              separado: el stock no se mezcla entre depósitos.
        fecha_desde/hasta:    rango a reconstruir.
        fecha_stock:          snapshot de anclaje (default: el más reciente).
        codigos:              subconjunto de SKUs (None = todos).

    Returns:
        DataFrame (función escalón): codigodepo, codigo, descripcion, rubro,
        fecha, delta (movimiento del día), delta_rem / delta_aju (la parte del
        día que aportaron remitos y ajustes, por `tipomov`, para marcarlos
        sobre la curva), delta_inv / delta_cs / delta_tr (el subconjunto de
        delta_aju que es específicamente inventario físico, ajuste de merma o
        transformación, por `tipo` — más fino que delta_aju para marcar
        distinto cada uno), movs_rem / movs_inv / movs_cs / movs_tr (CUÁNTOS
        movimientos de cada tipo hubo ese día, sin importar si su diferencia
        sumó 0 — un conteo de remitos, inventario/ajuste/transformación en
        neto no distingue "no hubo movimiento" de "hubo uno que no varió el
        stock" — p. ej. un inventario físico contado sin faltantes: se sigue
        marcando el día aunque delta_inv sea 0), stock
        (nivel resultante), vigencia_dias (cuántos días se mantiene ese nivel
        hasta el próximo movimiento o el fin del rango).
        attrs: fecha_stock, desde, hasta, sucursales.

        Incluye también los SKUs sin ningún movimiento en el rango que tienen
        stock distinto de cero en el snapshot de anclaje: aparecen con una
        sola fila plana al inicio del rango (delta=0) en vez de desaparecer
        de la serie. Los SKUs sin movimiento y con stock cero en el snapshot
        sí quedan fuera (no aportan nada al análisis de quiebres).
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)
    if not depos:
        raise ValueError("Hay que indicar al menos una sucursal.")

    conn = conectar(proyecto)
    try:
        # --- snapshot de anclaje ---------------------------------------------
        if fecha_stock:
            fs = conn.execute(
                "SELECT MAX(fecha_snapshot) FROM stock_sucursal WHERE fecha_snapshot <= ?",
                [str(pd.to_datetime(fecha_stock).date())],
            ).fetchone()[0]
        else:
            fs = conn.execute(
                "SELECT MAX(fecha_snapshot) FROM stock_sucursal").fetchone()[0]
        if fs is None:
            raise ValueError("No hay snapshots de stock cargados.")
        fs = str(fs)

        filtro_cod = ""
        filtro_cod_ss = ""
        params = {"depos": depos, "fs": fs, "desde": desde, "hasta": hasta}
        if codigos:
            # Registrada como tabla: la lista puede tener miles de SKUs y no
            # entra cómoda en un IN (...) literal.
            conn.register("_codigos_filtro",
                          pd.DataFrame({"codigo": pd.unique(pd.Series(codigos))}))
            filtro_cod = "AND m.codigo IN (SELECT codigo FROM _codigos_filtro)"
            filtro_cod_ss = "AND ss.codigo IN (SELECT codigo FROM _codigos_filtro)"

        # Movimientos diarios en la ventana que va del mínimo(desde, snapshot)
        # al máximo(hasta, snapshot): hace falta cubrir el tramo entre el
        # snapshot y el rango pedido para poder desandar el acumulado.
        df = conn.execute(f"""
            WITH mov AS (
                SELECT m.codigodepo, m.codigo, m.fecha,
                       SUM(m.diferencia)::DOUBLE AS delta,
                       COALESCE(SUM(m.diferencia) FILTER (WHERE m.tipomov = 'REM'), 0)::DOUBLE
                           AS delta_rem,
                       COALESCE(SUM(m.diferencia) FILTER (WHERE m.tipomov = 'AJU'), 0)::DOUBLE
                           AS delta_aju,
                       COALESCE(SUM(m.diferencia) FILTER (WHERE m.tipo = 'INV'), 0)::DOUBLE
                           AS delta_inv,
                       COALESCE(SUM(m.diferencia) FILTER (WHERE m.tipo = 'CS'), 0)::DOUBLE
                           AS delta_cs,
                       COALESCE(SUM(m.diferencia) FILTER (WHERE m.tipo = 'TR'), 0)::DOUBLE
                           AS delta_tr,
                       COUNT(*) FILTER (WHERE m.tipomov = 'REM')::INTEGER AS movs_rem,
                       COUNT(*) FILTER (WHERE m.tipo = 'INV')::INTEGER AS movs_inv,
                       COUNT(*) FILTER (WHERE m.tipo = 'CS')::INTEGER AS movs_cs,
                       COUNT(*) FILTER (WHERE m.tipo = 'TR')::INTEGER AS movs_tr
                FROM movimientos m
                WHERE m.codigodepo IN (SELECT unnest($depos))
                  AND m.fecha >  LEAST($desde::DATE, $fs::DATE)
                  AND m.fecha <= GREATEST($hasta::DATE, $fs::DATE)
                  {filtro_cod}
                GROUP BY 1, 2, 3
            ),
            stk AS (
                SELECT ss.codigodepo, ss.codigo, SUM(ss.stock)::DOUBLE AS stock_snap
                FROM stock_sucursal ss
                WHERE ss.codigodepo IN (SELECT unnest($depos)) AND ss.fecha_snapshot = $fs::DATE
                  {filtro_cod_ss}
                GROUP BY 1, 2
            ),
            -- Todos los SKUs relevantes: los que tuvieron algún movimiento en
            -- la ventana, MÁS los que tienen stock DISTINTO DE CERO en el
            -- snapshot de anclaje aunque nunca hayan tenido un movimiento (si
            -- no, un SKU quieto —sin ventas ni remitos ni ajustes en toda su
            -- historia— desaparece en silencio de la serie en vez de
            -- mostrarse plano). Se excluyen a propósito los que ya están en
            -- cero en el snapshot: sumarían decenas de miles de SKUs muertos
            -- (nunca tuvieron stock ni movimiento en esta sucursal) sin
            -- aportar nada al análisis de quiebres.
            universo AS (
                SELECT DISTINCT codigodepo, codigo FROM mov
                UNION
                SELECT codigodepo, codigo FROM stk WHERE stock_snap <> 0
            ),
            acum AS (
                SELECT codigodepo, codigo, fecha, delta, delta_rem, delta_aju,
                       delta_inv, delta_cs, delta_tr,
                       movs_rem, movs_inv, movs_cs, movs_tr,
                       SUM(delta) OVER (PARTITION BY codigodepo, codigo ORDER BY fecha
                                        ROWS UNBOUNDED PRECEDING) AS acumulado
                FROM mov
            ),
            -- Acumulado conocido a dos fechas ancla (arg_max, no MAX: la suma
            -- acumulada no es monótona): al día del snapshot (para desandar
            -- hasta el nivel real) y justo antes de "desde" (para el nivel
            -- inicial de un SKU sin movimientos dentro del rango pedido).
            niveles AS (
                SELECT u.codigodepo, u.codigo,
                       COALESCE(fs_ag.acumulado, 0) AS acum_fs,
                       COALESCE(desde_ag.acumulado, 0) AS acum_desde_prev
                FROM universo u
                LEFT JOIN (
                    SELECT codigodepo, codigo, arg_max(acumulado, fecha) AS acumulado
                    FROM acum WHERE fecha <= $fs::DATE GROUP BY 1, 2
                ) fs_ag ON u.codigodepo = fs_ag.codigodepo AND u.codigo = fs_ag.codigo
                LEFT JOIN (
                    SELECT codigodepo, codigo, arg_max(acumulado, fecha) AS acumulado
                    FROM acum WHERE fecha < $desde::DATE GROUP BY 1, 2
                ) desde_ag ON u.codigodepo = desde_ag.codigodepo AND u.codigo = desde_ag.codigo
            ),
            filas_mov AS (
                SELECT a.codigodepo, a.codigo, a.fecha, a.delta, a.delta_rem, a.delta_aju,
                       a.delta_inv, a.delta_cs, a.delta_tr,
                       a.movs_rem, a.movs_inv, a.movs_cs, a.movs_tr,
                       COALESCE(s.stock_snap, 0) + a.acumulado - n.acum_fs AS stock
                FROM acum a
                JOIN niveles n ON a.codigodepo = n.codigodepo AND a.codigo = n.codigo
                LEFT JOIN stk s ON a.codigodepo = s.codigodepo AND a.codigo = s.codigo
                WHERE a.fecha >= $desde::DATE AND a.fecha <= $hasta::DATE
            ),
            -- SKUs del universo sin ningún movimiento dentro de [desde, hasta]
            -- (pueden tener movimientos fuera de esa ventana, o ninguno en
            -- toda su historia): una sola fila plana al inicio del rango con
            -- el nivel vigente en ese momento.
            filas_planas AS (
                SELECT u.codigodepo, u.codigo, $desde::DATE AS fecha,
                       0.0 AS delta, 0.0 AS delta_rem, 0.0 AS delta_aju,
                       0.0 AS delta_inv, 0.0 AS delta_cs, 0.0 AS delta_tr,
                       0 AS movs_rem, 0 AS movs_inv, 0 AS movs_cs, 0 AS movs_tr,
                       COALESCE(s.stock_snap, 0) + n.acum_desde_prev - n.acum_fs AS stock
                FROM universo u
                JOIN niveles n ON u.codigodepo = n.codigodepo AND u.codigo = n.codigo
                LEFT JOIN stk s ON u.codigodepo = s.codigodepo AND u.codigo = s.codigo
                WHERE NOT EXISTS (
                    SELECT 1 FROM acum a
                    WHERE a.codigodepo = u.codigodepo AND a.codigo = u.codigo
                      AND a.fecha >= $desde::DATE AND a.fecha <= $hasta::DATE
                )
            )
            SELECT * FROM filas_mov
            UNION ALL
            SELECT * FROM filas_planas
            ORDER BY codigodepo, codigo, fecha
        """, params).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    attrs = dict(fecha_stock=fs, desde=desde, hasta=hasta, sucursales=depos)

    if df.empty:
        vacio = pd.DataFrame(columns=["codigodepo", "codigo", "fecha", "delta",
                                      "delta_rem", "delta_aju", "delta_inv",
                                      "delta_cs", "delta_tr", "movs_rem",
                                      "movs_inv", "movs_cs", "movs_tr", "stock"])
        vacio.attrs.update(attrs)
        return vacio

    df["fecha"] = pd.to_datetime(df["fecha"])
    fin = pd.to_datetime(hasta)

    # vigencia: días que se mantiene ese nivel hasta el próximo movimiento
    prox = df.groupby(["codigodepo", "codigo"])["fecha"].shift(-1)
    df["vigencia_dias"] = ((prox.fillna(fin + pd.Timedelta(days=1)) - df["fecha"])
                           .dt.days.clip(lower=0))

    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    cols = ["codigodepo", "codigo", "descripcion", "rubro", "marca",
            "gran_super_rubro", "fecha", "delta", "delta_rem", "delta_aju",
            "delta_inv", "delta_cs", "delta_tr", "movs_rem", "movs_inv",
            "movs_cs", "movs_tr", "stock", "vigencia_dias"]
    df = (df[cols].sort_values(["codigodepo", "codigo", "fecha"])
          .reset_index(drop=True))
    df.attrs.update(attrs)
    return df


def _una_sucursal(serie: pd.DataFrame, quien: str) -> None:
    """
    Las funciones por SKU no distinguen sucursal: si la serie trae varias, los
    niveles de stock de distintos depósitos se mezclarían en silencio.
    """
    if "codigodepo" in serie.columns and serie["codigodepo"].nunique() > 1:
        raise ValueError(
            f"{quien} trabaja sobre una sola sucursal, pero la serie trae "
            f"{serie['codigodepo'].nunique()}. Filtrá la serie por codigodepo, "
            "o usá stock_por_sucursal() para el agregado entre sucursales."
        )


def resumen_quiebres(serie: pd.DataFrame, umbral_bajo: float = 0) -> pd.DataFrame:
    """
    Resume la serie por SKU: días en quiebre, días con stock bajo, rachas y
    niveles. `umbral_bajo` define "stock bajo" (por defecto solo el quiebre).
    Requiere una serie de UNA sucursal.

    Returns:
        DataFrame por SKU: dias_quiebre, pct_quiebre, racha_quiebre (la más
        larga), dias_bajo, stock_min, stock_max, stock_prom (ponderado por
        los días que duró cada nivel).
    """
    if serie.empty:
        return pd.DataFrame(columns=["codigo", "dias_quiebre"])
    _una_sucursal(serie, "resumen_quiebres()")

    df = serie.copy()
    dias_rango = (pd.to_datetime(serie.attrs["hasta"])
                  - pd.to_datetime(serie.attrs["desde"])).days + 1

    df["en_quiebre"] = df["stock"] <= 0
    df["en_bajo"] = df["stock"] <= umbral_bajo
    df["dias_q"] = df["vigencia_dias"].where(df["en_quiebre"], 0)
    df["dias_b"] = df["vigencia_dias"].where(df["en_bajo"], 0)
    df["stock_x_dias"] = df["stock"] * df["vigencia_dias"]

    # racha de quiebre más larga: tramos consecutivos con stock <= 0
    df["_grupo"] = (df["en_quiebre"] != df.groupby("codigo")["en_quiebre"].shift()).cumsum()
    rachas = (df[df["en_quiebre"]].groupby(["codigo", "_grupo"])["vigencia_dias"].sum()
              .groupby("codigo").max())

    g = df.groupby("codigo")
    res = pd.DataFrame({
        "dias_quiebre":   g["dias_q"].sum(),
        "dias_bajo":      g["dias_b"].sum(),
        "movimientos":    g.size(),
        "stock_min":      g["stock"].min(),
        "stock_max":      g["stock"].max(),
        "stock_prom":     g["stock_x_dias"].sum() / g["vigencia_dias"].sum().replace(0, pd.NA),
        "stock_final":    g["stock"].last(),
    })
    res["racha_quiebre"] = rachas.reindex(res.index).fillna(0).astype(int)
    res["pct_quiebre"] = (res["dias_quiebre"] / dias_rango * 100).round(1)

    attrs = df.drop_duplicates("codigo").set_index("codigo")
    for c in ("descripcion", "rubro", "marca", "gran_super_rubro"):
        res[c] = attrs[c]

    res = res.reset_index()
    cols = ["codigo", "descripcion", "rubro", "marca", "gran_super_rubro",
            "dias_quiebre", "pct_quiebre", "racha_quiebre", "dias_bajo",
            "stock_min", "stock_prom", "stock_max", "stock_final", "movimientos"]
    res = (res[cols].sort_values(["dias_quiebre", "racha_quiebre"], ascending=False)
           .reset_index(drop=True))
    res.attrs.update(serie.attrs)
    return res


def serie_diaria(serie: pd.DataFrame, codigo: str) -> pd.DataFrame:
    """
    Expande la función escalón de UN SKU a una fila por día (para graficar).
    Requiere una serie de UNA sucursal.
    """
    _una_sucursal(serie, "serie_diaria()")
    s = serie[serie["codigo"] == codigo]
    if s.empty:
        return pd.DataFrame(columns=["fecha", "stock"])
    desde = pd.to_datetime(serie.attrs["desde"])
    hasta = pd.to_datetime(serie.attrs["hasta"])
    dias = pd.date_range(desde, hasta, freq="D")
    ser = (s.set_index("fecha")["stock"].reindex(dias).ffill())
    # antes del primer movimiento: nivel previo = primer stock − primer delta
    if pd.isna(ser.iloc[0]):
        ser.iloc[0] = float(s.iloc[0]["stock"] - s.iloc[0]["delta"])
        ser = ser.ffill()
    return pd.DataFrame({"fecha": dias, "stock": ser.values})


def series_diarias(serie: pd.DataFrame, codigos: list[str]) -> pd.DataFrame:
    """
    Expande la función escalón de VARIOS SKUs a una fila por día y SKU
    (para superponerlos en un mismo gráfico). Requiere una serie de UNA
    sucursal.

    Returns:
        DataFrame largo: fecha, codigo, stock. Vacío si no hay nada que expandir.
    """
    if serie.empty or not codigos:
        return pd.DataFrame(columns=["fecha", "codigo", "stock"])
    _una_sucursal(serie, "series_diarias()")

    s = serie[serie["codigo"].isin(codigos)]
    if s.empty:
        return pd.DataFrame(columns=["fecha", "codigo", "stock"])

    dias = pd.date_range(pd.to_datetime(serie.attrs["desde"]),
                         pd.to_datetime(serie.attrs["hasta"]), freq="D")
    piv = (s.pivot_table(index="fecha", columns="codigo", values="stock",
                         aggfunc="last")
           .reindex(dias).ffill())

    # Antes del primer movimiento de cada SKU el nivel es el previo a ese
    # movimiento (stock resultante − delta), no un hueco.
    primeros = s.sort_values("fecha").groupby("codigo").first()
    previos = (primeros["stock"] - primeros["delta"]).reindex(piv.columns)
    piv = piv.fillna(previos)

    largo = (piv.rename_axis("fecha").reset_index()
             .melt(id_vars="fecha", var_name="codigo", value_name="stock"))
    return largo.dropna(subset=["stock"]).reset_index(drop=True)


def stock_por_sucursal(serie: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega la serie a un total diario **por sucursal**: suma el stock vigente
    de todos los SKUs de la serie cada día. Es la base del cruce entre
    sucursales (filtrar por rubro/marca antes, al reconstruir).

    Returns:
        DataFrame largo: fecha, codigodepo, stock (unidades totales),
        skus_en_quiebre (cuántos de esos SKUs estaban sin stock ese día),
        skus (cuántos SKUs se están sumando).
    """
    cols = ["fecha", "codigodepo", "stock", "skus_en_quiebre", "skus"]
    if serie.empty:
        return pd.DataFrame(columns=cols)

    dias = pd.date_range(pd.to_datetime(serie.attrs["desde"]),
                         pd.to_datetime(serie.attrs["hasta"]), freq="D")
    piv = (serie.pivot_table(index="fecha", columns=["codigodepo", "codigo"],
                             values="stock", aggfunc="last")
           .reindex(dias).ffill())

    # Antes del primer movimiento: el nivel previo (stock resultante − delta).
    primeros = serie.sort_values("fecha").groupby(["codigodepo", "codigo"]).first()
    piv = piv.fillna((primeros["stock"] - primeros["delta"]).reindex(piv.columns))

    def _largo(ancho, nombre):
        return (ancho.T.groupby(level="codigodepo").sum().T
                .rename_axis("fecha").reset_index()
                .melt(id_vars="fecha", var_name="codigodepo", value_name=nombre))

    largo = _largo(piv, "stock")
    for ancho, nombre in ((piv <= 0, "skus_en_quiebre"), (piv.notna(), "skus")):
        largo = largo.merge(_largo(ancho, nombre), on=["fecha", "codigodepo"])
    largo.attrs.update(serie.attrs)
    return largo[cols]


def stock_por_categoria(serie: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """
    Agrega la serie a un total diario **por categoría** (rubro, marca o gran
    super rubro), sumando todas las sucursales de la serie: sirve para ver
    cómo evolucionó el stock de cada categoría del recorte, en vez de por
    sucursal. Complementa a `stock_por_sucursal`.

    Args:
        dimension: "rubro", "marca" o "gran_super_rubro" (columna de `serie`).

    Returns:
        DataFrame largo: fecha, <dimension>, stock (unidades totales),
        skus_en_quiebre, skus.
    """
    cols = ["fecha", dimension, "stock", "skus_en_quiebre", "skus"]
    if serie.empty:
        return pd.DataFrame(columns=cols)

    dias = pd.date_range(pd.to_datetime(serie.attrs["desde"]),
                         pd.to_datetime(serie.attrs["hasta"]), freq="D")
    piv = (serie.pivot_table(index="fecha", columns=["codigodepo", "codigo"],
                             values="stock", aggfunc="last")
           .reindex(dias).ffill())

    # Antes del primer movimiento: el nivel previo (stock resultante − delta).
    primeros = serie.sort_values("fecha").groupby(["codigodepo", "codigo"]).first()
    piv = piv.fillna((primeros["stock"] - primeros["delta"]).reindex(piv.columns))

    # La categoría es del SKU, no de la sucursal: un mismo código aporta la
    # misma categoría en todas las sucursales, así que agrupa a través de ellas.
    cat_por_codigo = (serie.drop_duplicates("codigo").set_index("codigo")[dimension]
                      .fillna("(sin dato)"))
    categorias = piv.columns.get_level_values("codigo").map(cat_por_codigo)

    def _largo(ancho, nombre):
        return (ancho.T.groupby(categorias).sum().T
                .rename_axis("fecha").reset_index()
                .melt(id_vars="fecha", var_name=dimension, value_name=nombre))

    largo = _largo(piv, "stock")
    for ancho, nombre in ((piv <= 0, "skus_en_quiebre"), (piv.notna(), "skus")):
        largo = largo.merge(_largo(ancho, nombre), on=["fecha", dimension])
    largo.attrs.update(serie.attrs)
    return largo[cols]


def marcadores_movimiento(serie: pd.DataFrame,
                          codigos: list[str] | str | None = None,
                          tipos: list[str] | None = None) -> pd.DataFrame:
    """
    Días con remitos, inventario físico, ajustes de merma o transformaciones,
    para marcarlos sobre la curva de stock de un SKU y explicar los saltos (y
    ver cada cuánto entra mercadería). Se marca el día siempre que haya
    existido un movimiento de ese tipo, aunque su diferencia neta haya sido 0
    — un inventario físico contado sin faltantes no mueve el stock, pero
    informa igual que ese día se contó (y no hubo diferencia).

    Args:
        serie:   salida de serie_stock (necesita delta_rem / delta_inv /
                delta_cs / delta_tr y movs_rem / movs_inv / movs_cs / movs_tr).
        codigos: un SKU, una lista, o None para todos.
        tipos:   subconjunto de TIPOS_MARCA (default: todos).

    Returns:
        DataFrame largo: codigo, fecha, tipomov, delta (movimiento neto de ese
        tipo ese día — puede ser 0), stock (nivel al cierre del día, donde va
        la marca).
    """
    vacio = pd.DataFrame(columns=["codigo", "fecha", "tipomov", "delta", "stock"])
    if serie.empty or "movs_rem" not in serie.columns:
        return vacio

    s = serie
    if codigos is not None:
        lista = [codigos] if isinstance(codigos, str) else list(codigos)
        s = s[s["codigo"].isin(lista)]
    if s.empty:
        return vacio

    partes = []
    for tipo, col, col_n in (("REM", "delta_rem", "movs_rem"),
                             ("INV", "delta_inv", "movs_inv"),
                             ("CS", "delta_cs", "movs_cs"),
                             ("TR", "delta_tr", "movs_tr")):
        if tipos and tipo not in tipos:
            continue
        d = s[s[col_n].fillna(0) > 0]
        if d.empty:
            continue
        partes.append(pd.DataFrame({
            "codigo": d["codigo"].values,
            "fecha": d["fecha"].values,
            "tipomov": tipo,
            "delta": d[col].values,
            "stock": d["stock"].values,
        }))
    if not partes:
        return vacio
    return (pd.concat(partes, ignore_index=True)
            .sort_values(["codigo", "fecha"]).reset_index(drop=True))


# Mismos cuatro tipos que TIPOS_MARCA (más arriba), pero con la paleta
# pensada para el marcado agregado por sucursal de `marcadores_sucursal`
# (INV = inventario físico, CS = ajuste que cuenta como merma, TR =
# transformación de SKU).
TIPOS_MARCA_SUC = {
    "REM": {"etiqueta": "Remito", "color": "#748ffc", "simbolo": "x"},
    "INV": {"etiqueta": "Inventario físico", "color": "#64ffda", "simbolo": "x-open"},
    "CS":  {"etiqueta": "Ajuste (merma)", "color": "#ff6b6b", "simbolo": "x-open"},
    "TR":  {"etiqueta": "Transformación", "color": "#e599f7", "simbolo": "diamond-open"},
}


def marcadores_sucursal(serie: pd.DataFrame, agg: pd.DataFrame,
                        tipos: list[str] | None = None) -> pd.DataFrame:
    """
    Días con remitos, inventario físico, ajustes de merma o transformaciones,
    sumando TODOS los SKUs del recorte por sucursal — para marcarlos sobre el
    gráfico agregado de `stock_por_sucursal` (a diferencia de
    `marcadores_movimiento`, que marca por SKU individual).

    Args:
        serie: salida de `serie_stock` (necesita delta_rem / delta_inv /
               delta_cs / delta_tr).
        agg:   salida de `stock_por_sucursal(serie)` — de ahí sale el nivel de
               stock (ya sumado) donde se ubica cada marca.
        tipos: subconjunto de TIPOS_MARCA_SUC (default: todos).

    Returns:
        DataFrame largo: codigodepo, fecha, tipo, delta (suma de ese tipo ese
        día, todos los SKUs del recorte), stock (nivel total de la sucursal
        ese día, donde va la marca).
    """
    vacio = pd.DataFrame(columns=["codigodepo", "fecha", "tipo", "delta", "stock"])
    if serie.empty or agg.empty or "delta_rem" not in serie.columns:
        return vacio

    stock_dia = agg.set_index(["codigodepo", "fecha"])["stock"]

    partes = []
    for tipo, col in (("REM", "delta_rem"), ("INV", "delta_inv"), ("CS", "delta_cs"),
                      ("TR", "delta_tr")):
        if tipos and tipo not in tipos:
            continue
        sum_dia = (serie.groupby(["codigodepo", "fecha"])[col].sum()
                   .loc[lambda s: s != 0])
        if sum_dia.empty:
            continue
        partes.append(pd.DataFrame({
            "codigodepo": sum_dia.index.get_level_values("codigodepo"),
            "fecha":      sum_dia.index.get_level_values("fecha"),
            "tipo":       tipo,
            "delta":      sum_dia.values,
            "stock":      sum_dia.index.map(stock_dia).values,
        }))
    if not partes:
        return vacio
    return (pd.concat(partes, ignore_index=True)
            .sort_values(["codigodepo", "fecha"]).reset_index(drop=True))


def quiebres_por_dia(serie: pd.DataFrame) -> pd.DataFrame:
    """
    Cuántos SKUs estuvieron en quiebre cada día (para el gráfico agregado).
    """
    if serie.empty:
        return pd.DataFrame(columns=["fecha", "skus_en_quiebre"])
    desde = pd.to_datetime(serie.attrs["desde"])
    hasta = pd.to_datetime(serie.attrs["hasta"])
    dias = pd.date_range(desde, hasta, freq="D")

    # matriz escalón: por SKU, stock vigente cada día
    piv = (serie.pivot_table(index="fecha", columns="codigo", values="stock",
                             aggfunc="last")
           .reindex(dias).ffill())
    return pd.DataFrame({
        "fecha": dias,
        "skus_en_quiebre": (piv <= 0).sum(axis=1).values,
        "skus_con_dato": piv.notna().sum(axis=1).values,
    })


# ---------------------------------------------------------------------------
# Resumen de movimientos por período (flujo, no nivel de stock)
# ---------------------------------------------------------------------------
#
# A diferencia de todo lo de arriba (que reconstruye el NIVEL de stock día a
# día desde un snapshot), esto agrega el FLUJO — el movimiento crudo de
# `movimientos` — por bloques de tiempo, por dimensión de catálogo y por
# tipo de movimiento. TIPOMOV/TIPO son atributos del movimiento, no del
# stock, así que solo tienen sentido acá, no en la reconstrucción de arriba.

GRANULARIDADES = {
    "dia": "Día", "semana": "Semana", "quincena": "Quincena",
    "mes": "Mes", "trimestre": "Trimestre",
}
_GRANULARIDAD_SQL = {"dia": "day", "semana": "week", "mes": "month", "trimestre": "quarter"}

DIMENSIONES_RESUMEN = {
    "codigo": "Código", "marca": "Marca", "rubro": "Rubro",
    "gran_super_rubro": "Gran Super Rubro",
}

CLASIFICACIONES = {
    "tipomov": "Tipo de movimiento", "tipo": "Tipo (subtipo ERP)",
    "ambos": "Tipo de movimiento + Tipo (anidado)",
}


def _expr_dimension(dimension: str) -> str:
    origen = "m" if dimension == "codigo" else "c"
    return f"{origen}.{dimension} AS {dimension}"


def etiqueta_periodo(fecha_ini, granularidad: str) -> str:
    """Etiqueta legible del bloque de tiempo a partir de su fecha de inicio."""
    f = pd.to_datetime(fecha_ini)
    if granularidad == "dia":
        return f.strftime("%d/%m/%Y")
    if granularidad == "semana":
        fin = f + pd.Timedelta(days=6)
        return f"{f.strftime('%d/%m')} – {fin.strftime('%d/%m/%Y')}"
    if granularidad == "quincena":
        fin = f + pd.Timedelta(days=14) if f.day == 1 else f + pd.offsets.MonthEnd(0)
        return f"{f.strftime('%d/%m')} – {fin.strftime('%d/%m/%Y')}"
    if granularidad == "mes":
        return f"{MESES[f.month]} {f.year}"
    if granularidad == "trimestre":
        return f"T{(f.month - 1) // 3 + 1} {f.year}"
    return str(f.date())


def resumen_movimientos(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    codigodepo: list[str] | str | None = None,
    granularidad: str = "mes",
    dimension: str = "codigo",
    clasificar_por: str = "tipomov",
) -> pd.DataFrame:
    """
    Resumen de movimientos: suma `diferencia` (neto, con signo), `ingreso` y
    `egreso` por bloque de tiempo, abierto por sucursal, por una dimensión de
    catálogo y por tipo de movimiento.

    Args:
        codigodepo:     una sucursal, una lista, o None = todas. Con varias
                        (o todas), el resultado se abre por sucursal — nunca
                        se suman entre sí.
        granularidad:   una de GRANULARIDADES ("dia","semana","quincena",
                        "mes","trimestre"). "quincena" parte cada mes en
                        1-15 y 16-fin.
        dimension:      una de DIMENSIONES_RESUMEN ("codigo","marca","rubro",
                        "gran_super_rubro").
        clasificar_por: una de CLASIFICACIONES ("tipomov","tipo","ambos" —
                        "ambos" agrega ambas columnas, tipomov como grupo
                        externo y tipo como el interno/anidado).

    Returns:
        DataFrame largo: periodo_ini (fecha de inicio del bloque), codigodepo,
        abreviacion (de la sucursal), <dimension> (+ descripcion si
        dimension="codigo"), [tipomov] [tipo], unidades (suma de diferencia),
        ingreso, egreso, movimientos (cant.). Sin agregar por período: cada
        fila ya es la combinación más fina
        pedida. attrs: granularidad, dimension, clasificar_por, desde, hasta.
    """
    if granularidad not in GRANULARIDADES:
        raise ValueError(f"granularidad debe ser una de {list(GRANULARIDADES)}, no '{granularidad}'")
    if dimension not in DIMENSIONES_RESUMEN:
        raise ValueError(f"dimension debe ser una de {list(DIMENSIONES_RESUMEN)}, no '{dimension}'")
    if clasificar_por not in CLASIFICACIONES:
        raise ValueError(f"clasificar_por debe ser una de {list(CLASIFICACIONES)}, no '{clasificar_por}'")

    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    depos = None
    if codigodepo:
        depos = [codigodepo] if isinstance(codigodepo, str) else list(codigodepo)

    if granularidad == "quincena":
        periodo_expr = """
            CASE WHEN day(m.fecha) <= 15 THEN date_trunc('month', m.fecha)
                 ELSE date_trunc('month', m.fecha) + INTERVAL 15 DAY END
        """
    else:
        periodo_expr = f"date_trunc('{_GRANULARIDAD_SQL[granularidad]}', m.fecha)"

    cols_clasif = []
    if clasificar_por in ("tipomov", "ambos"):
        cols_clasif.append("tipomov")
    if clasificar_por in ("tipo", "ambos"):
        cols_clasif.append("tipo")
    clasif_sql = ", " + ", ".join(f"m.{c}" for c in cols_clasif)

    conn = conectar(proyecto)
    try:
        cat = catalogo_articulos(proyecto)
        conn.register("_cat", cat)

        filtro_depo = "AND m.codigodepo IN (SELECT unnest($depos))" if depos else ""
        params: dict = {"desde": desde, "hasta": hasta}
        if depos:
            params["depos"] = depos

        df = conn.execute(f"""
            SELECT
                {periodo_expr}::DATE AS periodo_ini,
                m.codigodepo,
                {_expr_dimension(dimension)}
                {clasif_sql},
                SUM(m.diferencia)::DOUBLE AS unidades,
                SUM(m.ingreso)::DOUBLE    AS ingreso,
                SUM(m.egreso)::DOUBLE     AS egreso,
                COUNT(*)::INT             AS movimientos
            FROM movimientos m
            LEFT JOIN _cat c ON m.codigo = c.codigo
            WHERE m.fecha >= $desde::DATE AND m.fecha <= $hasta::DATE
              {filtro_depo}
            GROUP BY ALL
        """, params).df()

        if dimension == "codigo" and not df.empty:
            desc = cat.drop_duplicates("codigo").set_index("codigo")["descripcion"]
            df["descripcion"] = df["codigo"].map(desc)

        if not df.empty:
            deps = conn.execute("SELECT codigodepo, abreviacion FROM depositos").df()
            abrev = deps.drop_duplicates("codigodepo").set_index("codigodepo")["abreviacion"]
            df["abreviacion"] = df["codigodepo"].map(abrev).fillna(df["codigodepo"])
    finally:
        conn.close()

    orden = ["periodo_ini", "codigodepo", "abreviacion"]
    if dimension == "codigo":
        orden.append("descripcion")
    orden += [dimension] + cols_clasif + ["unidades", "ingreso", "egreso", "movimientos"]
    df = df[[c for c in orden if c in df.columns]].sort_values(
        ["periodo_ini", "codigodepo", dimension]).reset_index(drop=True)

    df.attrs["granularidad"] = granularidad
    df.attrs["dimension"] = dimension
    df.attrs["clasificar_por"] = clasificar_por
    df.attrs["desde"] = desde
    df.attrs["hasta"] = hasta
    return df
