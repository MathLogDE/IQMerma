"""
core/transferencias.py — Sugerencias de redistribución entre sucursales

Cruza las políticas de stock (core.inventario.politicas): un SKU en EXCESO en una
sucursal y con necesidad de REPOSICIÓN en otra es un candidato a transferencia
lateral — se reabastece con capital ya comprado, sin esperar al proveedor.

Reglas:
  - Donante: estado "exceso" → puede ceder hasta (stock − óptimo): queda en
    su nivel objetivo, no se lo desabastece.
  - Receptor: estado "reponer" → necesita (óptimo − stock).
  - Asignación greedy por SKU: primero los receptores de mayor necesidad
    valorizada (clase A primero), tomando del donante con más excedente.
  - Cantidades redondeadas a entero (mínimo 1 unidad).
  - Los depósitos es_logistica quedan fuera (el flujo CD→sucursal es la
    reposición normal, no una transferencia lateral).

Uso:
    from core.distribucion.transferencias import sugerir_transferencias

    df = sugerir_transferencias("cliente_x")   # mismos params que políticas
"""

import math

import pandas as pd

from core.inventario.politicas import calcular_politicas


def sugerir_transferencias(
    proyecto: str,
    fecha_stock: str | None = None,
    dias_demanda: int = 90,
    lead_time_dias: float = 7,
    ciclo_default: float = 30,
    df_politicas: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Genera sugerencias de transferencia lateral entre sucursales.

    Args:
        df_politicas: resultado de calcular_politicas (todas las sucursales)
                      para reutilizar; si es None se calcula acá.

    Returns:
        DataFrame: una fila por (SKU, origen, destino) con unidades, valor a
        costo, stock/óptimo de ambas puntas y clase ABC del destino.
        attrs: fecha_stock, totales.
    """
    df = df_politicas
    if df is None:
        df = calcular_politicas(
            proyecto, codigodepo=None, fecha_stock=fecha_stock,
            dias_demanda=dias_demanda, lead_time_dias=lead_time_dias,
            ciclo_default=ciclo_default, incluir_logistica=False,
        )

    donantes = df[df["estado"] == "exceso"].copy()
    receptores = df[df["estado"] == "reponer"].copy()

    donantes["disponible"] = (donantes["stock"] - donantes["optimo"]).clip(lower=0)
    receptores["necesidad"] = (receptores["optimo"] - receptores["stock"]).clip(lower=0)
    receptores["necesidad_val"] = receptores["necesidad"] * receptores["costo"].fillna(0)

    # solo SKUs presentes en ambos lados
    comunes = set(donantes["codigo"]) & set(receptores["codigo"])
    donantes = donantes[donantes["codigo"].isin(comunes)]
    receptores = receptores[receptores["codigo"].isin(comunes)]

    filas = []
    don_por_sku = {k: g.sort_values("disponible", ascending=False).to_dict("records")
                   for k, g in donantes.groupby("codigo")}

    # receptores: clase A primero, luego mayor necesidad valorizada
    receptores = receptores.sort_values(
        ["clase_abc", "necesidad_val"], ascending=[True, False])

    for _, rec in receptores.iterrows():
        pool = don_por_sku.get(rec["codigo"], [])
        falta = float(rec["necesidad"])
        for don in pool:
            if falta < 1:
                break
            if don["codigodepo"] == rec["codigodepo"] or don["disponible"] < 1:
                continue
            unidades = float(min(don["disponible"], falta))
            unidades = math.floor(unidades)
            if unidades < 1:
                continue
            filas.append({
                "codigo":          rec["codigo"],
                "descripcion":     rec["descripcion"],
                "rubro":           rec["rubro"],
                "gran_super_rubro": rec["gran_super_rubro"],
                "clase_abc":       rec["clase_abc"],
                "origen":          don["codigodepo"],
                "origen_nombre":   don["sucursal"],
                "destino":         rec["codigodepo"],
                "destino_nombre":  rec["sucursal"],
                "unidades":        unidades,
                "valor":           unidades * (don["costo"] if pd.notna(don["costo"]) else 0.0),
                "stock_origen":    don["stock"],
                "optimo_origen":   don["optimo"],
                "stock_destino":   rec["stock"],
                "optimo_destino":  rec["optimo"],
            })
            don["disponible"] -= unidades
            falta -= unidades

    res = pd.DataFrame(filas)
    if not res.empty:
        res = res.sort_values("valor", ascending=False).reset_index(drop=True)

    res.attrs["fecha_stock"] = df.attrs.get("fecha_stock")
    res.attrs["total_unidades"] = float(res["unidades"].sum()) if not res.empty else 0.0
    res.attrs["total_valor"] = float(res["valor"].sum()) if not res.empty else 0.0
    res.attrs["skus"] = int(res["codigo"].nunique()) if not res.empty else 0
    return res
