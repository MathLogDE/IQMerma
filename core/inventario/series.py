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

from core.comun import conectar, adjuntar_rubros


def serie_stock(
    proyecto: str,
    codigodepo: str,
    fecha_desde: str,
    fecha_hasta: str,
    fecha_stock: str | None = None,
    codigos: list[str] | None = None,
    solo_con_movimiento: bool = True,
) -> pd.DataFrame:
    """
    Reconstruye la evolución diaria del stock por SKU en una sucursal.

    Args:
        proyecto, codigodepo: contexto.
        fecha_desde/hasta:    rango a reconstruir.
        fecha_stock:          snapshot de anclaje (default: el más reciente).
        codigos:              subconjunto de SKUs (None = todos).
        solo_con_movimiento:  excluye SKUs sin movimientos en el rango.

    Returns:
        DataFrame (función escalón): codigo, descripcion, rubro, fecha,
        delta (movimiento del día), stock (nivel resultante), vigencia_dias
        (cuántos días se mantiene ese nivel hasta el próximo movimiento o el
        fin del rango). attrs: fecha_stock, desde, hasta.
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

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
        params = {"depo": codigodepo, "fs": fs, "desde": desde, "hasta": hasta}
        if codigos:
            lista = ", ".join("'" + c.replace("'", "''") + "'" for c in codigos)
            filtro_cod = f"AND m.codigo IN ({lista})"

        # Movimientos diarios en la ventana que va del mínimo(desde, snapshot)
        # al máximo(hasta, snapshot): hace falta cubrir el tramo entre el
        # snapshot y el rango pedido para poder desandar el acumulado.
        df = conn.execute(f"""
            WITH mov AS (
                SELECT m.codigo, m.fecha, SUM(m.diferencia)::DOUBLE AS delta
                FROM movimientos m
                WHERE m.codigodepo = $depo
                  AND m.fecha >  LEAST($desde::DATE, $fs::DATE)
                  AND m.fecha <= GREATEST($hasta::DATE, $fs::DATE)
                  {filtro_cod}
                GROUP BY 1, 2
            ),
            acum AS (
                SELECT codigo, fecha, delta,
                       SUM(delta) OVER (PARTITION BY codigo ORDER BY fecha
                                        ROWS UNBOUNDED PRECEDING) AS acumulado
                FROM mov
            ),
            -- acumulado AL DÍA del snapshot: el del último movimiento <= fs
            -- (arg_max, no MAX: la suma acumulada no es monótona)
            base AS (
                SELECT codigo,
                       COALESCE(arg_max(acumulado, fecha) FILTER (WHERE fecha <= $fs::DATE), 0)
                           AS acum_snap
                FROM acum GROUP BY 1
            ),
            stk AS (
                SELECT codigo, SUM(stock)::DOUBLE AS stock_snap
                FROM stock_sucursal
                WHERE codigodepo = $depo AND fecha_snapshot = $fs::DATE
                GROUP BY 1
            )
            SELECT a.codigo, a.fecha, a.delta,
                   COALESCE(s.stock_snap, 0) + a.acumulado - b.acum_snap AS stock
            FROM acum a
            JOIN base b ON a.codigo = b.codigo
            LEFT JOIN stk s ON a.codigo = s.codigo
            WHERE a.fecha >= $desde::DATE AND a.fecha <= $hasta::DATE
            ORDER BY a.codigo, a.fecha
        """, params).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    if df.empty:
        vacio = pd.DataFrame(columns=["codigo", "fecha", "delta", "stock"])
        vacio.attrs.update(fecha_stock=fs, desde=desde, hasta=hasta)
        return vacio

    df["fecha"] = pd.to_datetime(df["fecha"])
    fin = pd.to_datetime(hasta)

    # vigencia: días que se mantiene ese nivel hasta el próximo movimiento
    prox = df.groupby("codigo")["fecha"].shift(-1)
    df["vigencia_dias"] = ((prox.fillna(fin + pd.Timedelta(days=1)) - df["fecha"])
                           .dt.days.clip(lower=0))

    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    cols = ["codigo", "descripcion", "rubro", "marca", "gran_super_rubro",
            "fecha", "delta", "stock", "vigencia_dias"]
    df = df[cols].sort_values(["codigo", "fecha"]).reset_index(drop=True)
    df.attrs.update(fecha_stock=fs, desde=desde, hasta=hasta)
    return df


def resumen_quiebres(serie: pd.DataFrame, umbral_bajo: float = 0) -> pd.DataFrame:
    """
    Resume la serie por SKU: días en quiebre, días con stock bajo, rachas y
    niveles. `umbral_bajo` define "stock bajo" (por defecto solo el quiebre).

    Returns:
        DataFrame por SKU: dias_quiebre, pct_quiebre, racha_quiebre (la más
        larga), dias_bajo, stock_min, stock_max, stock_prom (ponderado por
        los días que duró cada nivel).
    """
    if serie.empty:
        return pd.DataFrame(columns=["codigo", "dias_quiebre"])

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
    """
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
