"""
core/merma/transformaciones.py — Informes de transformaciones (TIPO='TR')

Reconstruye, línea a línea, los movimientos de producción simple que
transforman un SKU en otro: dan de baja el SKU original y dan de alta el
SKU de liquidación que lo reemplaza. El signo de `diferencia` distingue el
sentido de cada línea (diferencia<0 -> Baja, diferencia>0 -> Alta); ambas
líneas comparten `tipo='TR'` y normalmente el mismo `numero` de comprobante
(ver `pares_transformacion`, que las empareja).

Estos movimientos ya suman a la merma en `core.merma.analisis` (categoría
"Transformación" de `tipos_categoria`) — este módulo da el detalle línea a
línea que ese cálculo agregado no expone.

Valorización a precio constante del snapshot elegido (mismo criterio que
`core.comercial.comprobantes` y `core.inventario.interanual`): unidades por
el precio del snapshot, no el `costo` transaccional de la línea.

Uso:
    from core.merma.transformaciones import detalle_transformaciones, pares_transformacion

    detalle = detalle_transformaciones("cliente_x", "2026-01-01", "2026-06-30")
    pares = pares_transformacion(detalle)
"""

import pandas as pd

from core.comun import (
    conectar, precios_snapshot, snapshot_usado, validar_modo_valorizacion,
    adjuntar_rubros,
)

DETALLE_COLS = ["numero", "codigodepo", "fecha", "codigo", "descripcion", "rubro",
                "marca", "gran_super_rubro", "sentido", "unidades", "valor"]

PARES_COLS = ["numero", "codigodepo", "fecha", "skus_baja", "skus_alta",
              "unidades_baja", "valor_baja", "unidades_alta", "valor_alta",
              "neto_unidades", "neto_valor"]


def detalle_transformaciones(
    proyecto: str,
    fecha_desde: str,
    fecha_hasta: str,
    sucursales: list[str] | None = None,
    codigos: list[str] | None = None,
    modo_valorizacion: str = "costo",
    fecha_valorizacion: str | None = None,
) -> pd.DataFrame:
    """
    Detalle línea a línea de movimientos tipo='TR' (Transformación) en el
    período.

    Args:
        codigos: subconjunto de SKUs (recorte de catálogo por rubro/marca/
                 GSR, ya resuelto en la UI) — filtra a nivel de línea, igual
                 criterio que `core.comercial.comprobantes._comprobantes`.

    Returns:
        DataFrame: numero, codigodepo, fecha, codigo, descripcion, rubro,
        marca, gran_super_rubro, sentido ("Baja" | "Alta"), unidades
        (siempre positivas), valor (unidades * precio constante del
        snapshot). Ordenado por fecha, numero, codigodepo.
        attrs: desde, hasta, modo_valorizacion, fecha_valorizacion.
    """
    validar_modo_valorizacion(modo_valorizacion)
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    if desde > hasta:
        raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")

    unit_col = "costo" if modo_valorizacion == "costo" else "lista_1"
    attrs = dict(desde=desde, hasta=hasta, modo_valorizacion=modo_valorizacion)

    conn = conectar(proyecto)
    try:
        conn.register("_precios_tr", precios_snapshot(conn, fecha_valorizacion)
                      [["codigo", unit_col]].rename(columns={unit_col: "precio"}))
        attrs["fecha_valorizacion"] = snapshot_usado(conn, fecha_valorizacion)

        filtros = ["m.tipo = 'TR'", "m.fecha >= $desde::DATE", "m.fecha <= $hasta::DATE"]
        params: dict = {"desde": desde, "hasta": hasta}
        if sucursales:
            filtros.append("m.codigodepo IN (SELECT unnest($sucs))")
            params["sucs"] = list(sucursales)
        if codigos:
            conn.register("_codigos_tr", pd.DataFrame(
                {"codigo": pd.unique(pd.Series(list(codigos)))}))
            filtros.append("m.codigo IN (SELECT codigo FROM _codigos_tr)")

        df = conn.execute(f"""
            SELECT m.numero, m.codigodepo, m.fecha, m.codigo,
                   CASE WHEN m.diferencia < 0 THEN 'Baja' ELSE 'Alta' END AS sentido,
                   ABS(m.diferencia)::DOUBLE AS unidades,
                   (ABS(m.diferencia) * COALESCE(p.precio, 0))::DOUBLE AS valor
            FROM movimientos m
            LEFT JOIN _precios_tr p ON m.codigo = p.codigo
            WHERE {' AND '.join(filtros)}
            ORDER BY m.fecha, m.numero, m.codigodepo
        """, params).df()

        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()

    if df.empty:
        vacio = pd.DataFrame(columns=DETALLE_COLS)
        vacio.attrs.update(attrs)
        return vacio

    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df.merge(df_art, on="codigo", how="left")
    df = adjuntar_rubros(df, df_est)

    df = df[DETALLE_COLS].reset_index(drop=True)
    df.attrs.update(attrs)
    return df


def pares_transformacion(detalle: pd.DataFrame) -> pd.DataFrame:
    """
    Agrupa el detalle por comprobante (numero + codigodepo + fecha) y arma
    el par "SKU(s) dado de baja -> SKU(s) de liquidación" de cada
    transformación, con unidades y valor de cada lado — el nivel de detalle
    que responde "qué SKU se transformó en cuál".

    Args:
        detalle: salida de `detalle_transformaciones`.

    Returns:
        DataFrame: numero, codigodepo, fecha, skus_baja, skus_alta,
        unidades_baja, valor_baja, unidades_alta, valor_alta, neto_unidades
        (alta - baja), neto_valor (alta - baja). Ordenado por fecha
        descendente.
    """
    if detalle.empty:
        return pd.DataFrame(columns=PARES_COLS)

    grupo = ["numero", "codigodepo", "fecha"]
    lados = []
    for sentido, suf in (("Baja", "baja"), ("Alta", "alta")):
        d = detalle[detalle["sentido"] == sentido]
        agg = d.groupby(grupo).agg(**{
            f"skus_{suf}":     ("codigo", lambda s: ", ".join(sorted(set(s)))),
            f"unidades_{suf}": ("unidades", "sum"),
            f"valor_{suf}":    ("valor", "sum"),
        })
        lados.append(agg)

    res = lados[0].join(lados[1], how="outer").reset_index()
    res["skus_baja"] = res["skus_baja"].fillna("")
    res["skus_alta"] = res["skus_alta"].fillna("")
    for c in ("unidades_baja", "valor_baja", "unidades_alta", "valor_alta"):
        res[c] = res[c].fillna(0.0)
    res["neto_unidades"] = res["unidades_alta"] - res["unidades_baja"]
    res["neto_valor"] = res["valor_alta"] - res["valor_baja"]

    res = (res[PARES_COLS].sort_values("fecha", ascending=False)
           .reset_index(drop=True))
    return res
