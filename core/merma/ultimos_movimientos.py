"""
core/merma/ultimos_movimientos.py — Último movimiento por tipo de comprobante

Para una sucursal, por SKU, la fecha del último movimiento registrado de
cada tipo de comprobante (FA, FB, INV, RI, NCB, CS...) — una columna por
tipo. Pensado como exportable (Excel/CSV), sin reporte imprimible.
"""
import pandas as pd

from core.comun import conectar, tipos_movimiento


def ultimo_movimiento_por_tipo(
    proyecto: str,
    codigodepo: str,
    codigos: list[str] | None = None,
) -> pd.DataFrame:
    """
    Por SKU, la fecha del último movimiento registrado de cada tipo de
    comprobante (columna `tipo` de `movimientos`) en una sucursal — una
    columna por tipo. Los tipos sin ningún movimiento para un SKU quedan
    en NaT.

    Args:
        codigodepo: sucursal (una sola).
        codigos:    subconjunto opcional de SKUs (None = todos los que
                    tengan algún movimiento en esa sucursal). Si se pasa,
                    el universo resultante es exactamente ese conjunto —
                    un SKU filtrado sin movimientos en la sucursal sale
                    igual, con todas las columnas en NaT.

    Returns:
        DataFrame con columna `codigo` + una columna por cada `tipo` de
        tipos_movimiento(proyecto), en su orden de catálogo. df.attrs
        agrega `tipos` (esa misma lista) para que la UI arme headers sin
        tener que volver a consultar el catálogo de tipos.
    """
    conn = conectar(proyecto)
    try:
        params = {"depo": codigodepo}
        filtro_cod = ""
        if codigos:
            filtro_cod = "AND codigo IN (SELECT unnest($codigos))"
            params["codigos"] = list(codigos)
        df_largo = conn.execute(f"""
            SELECT codigo, tipo, MAX(fecha) AS ultima_fecha
            FROM movimientos
            WHERE codigodepo = $depo {filtro_cod}
            GROUP BY codigo, tipo
        """, params).df()
    finally:
        conn.close()

    tipos = tipos_movimiento(proyecto)["tipo"].tolist()
    universo = sorted(set(codigos)) if codigos else sorted(df_largo["codigo"].unique())

    if df_largo.empty:
        wide = pd.DataFrame({"codigo": universo})
        for t in tipos:
            wide[t] = pd.NaT
    else:
        wide = (df_largo.pivot_table(index="codigo", columns="tipo",
                                     values="ultima_fecha", aggfunc="max")
                        .reindex(index=universo, columns=tipos)
                        .reset_index())
        for t in tipos:
            wide[t] = pd.to_datetime(wide[t]).dt.date

    wide.attrs["tipos"] = tipos
    return wide
