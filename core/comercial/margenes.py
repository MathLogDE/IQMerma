"""
core/margenes.py — Márgenes y evolución de costos

Dos análisis sobre stock (costo / lista_1) + movimientos:

  1. analizar_margenes(): por SKU, margen teórico (lista vs costo), venta y
     margen bruto del período, merma a costo y la MERMA COMO % DEL MARGEN —
     el KPI más duro: cuánto de lo que el producto deja se pierde en merma.
  2. evolucion_costos(): mediana de costo y lista por snapshot × gran super
     rubro → inflación de reposición. Necesita ≥ 2 snapshots de stock; con
     uno solo devuelve la foto (la UI avisa).

Todas las cifras del período usan la misma convención que core.merma:
unidades de movimientos (es_venta / es_merma) valorizadas al snapshot.
"""

import duckdb
import pandas as pd

from core.comun import (
    conectar, mapa_estructura, precios_snapshot, snapshot_usado, adjuntar_rubros,
)


def analizar_margenes(
    proyecto: str,
    codigodepo: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Margen por SKU para el período (None = toda la historia).

    Returns:
        DataFrame por SKU: costo, lista_1, margen_unitario, margen_pct,
        unidades_vendidas, venta_valorizada (a lista), costo_mercaderia,
        margen_bruto, merma_costo, merma_sobre_margen (%).
        attrs: fecha_valorizacion.
    """
    conn = conectar(proyecto)
    try:
        filtro_depo = "AND m.codigodepo = $depo" if codigodepo else ""
        filtro_desde = "AND m.fecha >= $desde::DATE" if fecha_desde else ""
        filtro_hasta = "AND m.fecha <= $hasta::DATE" if fecha_hasta else ""
        params = {}
        if codigodepo:
            params["depo"] = codigodepo
        if fecha_desde:
            params["desde"] = str(pd.to_datetime(fecha_desde).date())
        if fecha_hasta:
            params["hasta"] = str(pd.to_datetime(fecha_hasta).date())

        # Misma convención que core.merma: la merma es el neto (con signo) de
        # las categorías es_merma por SKU, negado — las categorías (INV vs CS)
        # se compensan dentro del mismo código.
        df_mov = conn.execute(f"""
            WITH neto AS (
                SELECT m.codigo, t.es_venta, t.es_merma,
                       SUM(m.diferencia) AS neto_unidades
                FROM movimientos m
                JOIN tipos_categoria t ON m.tipo = t.tipo
                WHERE (t.es_venta OR t.es_merma)
                  {filtro_depo} {filtro_desde} {filtro_hasta}
                GROUP BY 1, 2, 3
            )
            SELECT codigo,
                   -SUM(neto_unidades) FILTER (WHERE es_venta)  AS unidades_vendidas,
                   -SUM(neto_unidades) FILTER (WHERE es_merma)  AS merma_unidades
            FROM neto
            GROUP BY 1
        """, params if params else []).df()

        df_val = precios_snapshot(conn, fecha_valorizacion)
        fv_usada = snapshot_usado(conn, fecha_valorizacion)

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    df = df_mov.merge(df_val, on="codigo", how="left")
    for c in ("unidades_vendidas", "merma_unidades"):
        df[c] = df[c].fillna(0.0).astype(float)
    for c in ("costo", "lista_1"):
        df[c] = df[c].astype(float)

    df["margen_unitario"] = df["lista_1"] - df["costo"]
    df["margen_pct"] = pd.NA
    ok = df["lista_1"] > 0
    df.loc[ok, "margen_pct"] = (
        (df.loc[ok, "margen_unitario"] / df.loc[ok, "lista_1"]) * 100
    ).round(2)

    df["venta_valorizada"] = (df["unidades_vendidas"].clip(lower=0) * df["lista_1"]).fillna(0.0)
    df["costo_mercaderia"] = (df["unidades_vendidas"].clip(lower=0) * df["costo"]).fillna(0.0)
    df["margen_bruto"] = df["venta_valorizada"] - df["costo_mercaderia"]
    df["merma_costo"] = (df["merma_unidades"] * df["costo"]).fillna(0.0)

    df["merma_sobre_margen"] = pd.NA
    m = df["margen_bruto"] > 0
    df.loc[m, "merma_sobre_margen"] = (
        df.loc[m, "merma_costo"] / df.loc[m, "margen_bruto"] * 100
    ).round(2)

    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    cols = ["codigo", "descripcion", "rubro", "marca", "super_rubro",
            "gran_super_rubro", "costo", "lista_1", "margen_unitario",
            "margen_pct", "unidades_vendidas", "venta_valorizada",
            "costo_mercaderia", "margen_bruto", "merma_unidades",
            "merma_costo", "merma_sobre_margen"]
    df = (df[cols]
          .sort_values("margen_bruto", ascending=False)
          .reset_index(drop=True))
    df.attrs["fecha_valorizacion"] = fv_usada
    return df


def evolucion_costos(proyecto: str) -> pd.DataFrame:
    """
    Mediana de costo y lista_1 por snapshot × gran super rubro (inflación de
    reposición). Con un solo snapshot devuelve la foto actual.

    Returns:
        DataFrame: fecha_snapshot, gran_super_rubro, costo_mediano,
        lista_mediana, skus. attrs["n_snapshots"].
    """
    conn = conectar(proyecto)
    try:
        df = conn.execute("""
            SELECT s.fecha_snapshot, s.codigo,
                   median(s.costo)   AS costo,
                   median(s.lista_1) AS lista_1
            FROM stock_sucursal s
            WHERE s.costo > 0
            GROUP BY 1, 2
        """).df()
        df_art = conn.execute("SELECT codigo, rubro FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    if df.empty:
        vacio = pd.DataFrame(columns=["fecha_snapshot", "gran_super_rubro"])
        vacio.attrs["n_snapshots"] = 0
        return vacio

    mapa = mapa_estructura(df_est)
    rubro_art = df_art.set_index("codigo")["rubro"].astype("string").str.strip()
    clave = df["codigo"].map(rubro_art)
    df["gran_super_rubro"] = clave.map(
        mapa.set_index("_clave")["gran_super_rubro"]).fillna("Sin clasificar")

    res = (df.groupby(["fecha_snapshot", "gran_super_rubro"], dropna=False)
           .agg(costo_mediano=("costo", "median"),
                lista_mediana=("lista_1", "median"),
                skus=("codigo", "nunique"))
           .reset_index()
           .sort_values(["gran_super_rubro", "fecha_snapshot"])
           .reset_index(drop=True))
    res.attrs["n_snapshots"] = int(df["fecha_snapshot"].nunique())
    return res
