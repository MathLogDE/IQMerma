"""
core/nc_anexos.py — Anexos de una No Conformidad con datos de IQMerma

Cada anexo se genera mientras la NC es BORRADOR y se guarda tal cual (HTML,
parámetros, fecha de corte de los datos, hash) en `nc_anexos`
(core/no_conformidades.py). Al emitir quedan congelados: una reingesta del ERP
no cambia los números de una NC ya emitida. Lo que se vio en la vista previa es
lo que se emite.

Tipos:
    serie_stock   Serie de stock diaria de uno o más códigos en la sucursal,
                  con inventarios y ajustes marcados (core/inventario/series.py).
    merma         Merma de la sucursal en un período, opcionalmente por rubro,
                  con los peores SKU y los códigos de la NC destacados
                  (core/merma/analisis.py). Su total sirve como línea base.

`generar(proyecto, tipo, params)` devuelve un dict listo para
`no_conformidades.agregar_anexo`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

TIPOS = {
    "serie_stock": "Serie de stock del SKU",
    "merma": "Merma del inventario",
}

MAX_CODIGOS_SERIE = 6

# Colores de impresión de las marcas (más oscuros que los de pantalla).
_MARCAS = {
    "INV": ("Inventario físico", "#c2255c"),
    "CS":  ("Ajuste (merma)", "#d9480f"),
    "MD":  ("Dif. de camión", "#1971c2"),
    "TR":  ("Transformación", "#9c36b5"),
}


def _env():
    from core.reporte import _env as env_reportes
    env = env_reportes()
    env.autoescape = True
    return env


def _corte(proyecto: str, codigodepo: str, hasta: str) -> str | None:
    """Último día con movimientos de la sucursal (≤ hasta): fecha de corte de los datos."""
    from core.comun import conectar
    conn = conectar(proyecto)
    try:
        r = conn.execute("SELECT MAX(fecha) FROM movimientos WHERE codigodepo = ? "
                         "AND fecha <= ?::DATE", [codigodepo, hasta]).fetchone()[0]
    finally:
        conn.close()
    return str(pd.to_datetime(r).date()) if r is not None else None


def descripciones(proyecto: str, codigos: list[str]) -> dict:
    from core.comun import conectar
    if not codigos:
        return {}
    conn = conectar(proyecto)
    try:
        df = conn.execute("SELECT codigo, descripcion FROM articulos "
                          "WHERE codigo IN (SELECT unnest(?))", [codigos]).df()
    finally:
        conn.close()
    return dict(zip(df["codigo"], df["descripcion"]))


def rubros(proyecto: str) -> list[str]:
    from core.comun import conectar
    conn = conectar(proyecto)
    try:
        df = conn.execute("SELECT DISTINCT rubro FROM articulos WHERE rubro IS NOT NULL "
                          "ORDER BY rubro").df()
    finally:
        conn.close()
    return df["rubro"].astype(str).tolist()


def rango_default(hoy: date | None = None) -> tuple[date, date]:
    hoy = hoy or date.today()
    return hoy - timedelta(days=365), hoy


# ---------------------------------------------------------------------------
# Serie de stock
# ---------------------------------------------------------------------------

def _anexo_serie(proyecto: str, p: dict) -> dict:
    from core.inventario.series import (
        serie_stock, serie_diaria, marcadores_movimiento, resumen_quiebres,
    )
    from core.graficos import svg_serie_stock

    codigos = [c.strip() for c in p["codigos"] if str(c).strip()]
    if not codigos:
        raise ValueError("Indicá al menos un código.")
    if len(codigos) > MAX_CODIGOS_SERIE:
        raise ValueError(f"Hasta {MAX_CODIGOS_SERIE} códigos por anexo.")
    serie = serie_stock(proyecto, p["codigodepo"], p["desde"], p["hasta"], codigos=codigos)
    resumen = resumen_quiebres(serie).set_index("codigo") if not serie.empty else pd.DataFrame()
    desc = descripciones(proyecto, codigos)

    bloques = []
    for c in codigos:
        b = {"codigo": c, "descripcion": desc.get(c, ""), "svg": "", "eventos": [],
             "sin_datos": False}
        d = serie_diaria(serie, c) if not serie.empty else pd.DataFrame()
        if d.empty:
            b["sin_datos"] = True
            bloques.append(b)
            continue
        fechas = [f.date() for f in pd.to_datetime(d["fecha"])]
        stock = [float(v) for v in d["stock"]]
        mk = marcadores_movimiento(serie, c, list(_MARCAS))
        marcas = [{"fecha": pd.to_datetime(r.fecha).date(), "stock": float(r.stock),
                   "color": _MARCAS[r.tipomov][1], "etiqueta": _MARCAS[r.tipomov][0],
                   "texto": f"{r.delta:+,.0f}" if r.delta else "0"}
                  for r in mk.itertuples() if r.tipomov in _MARCAS]
        b["svg"] = svg_serie_stock(fechas, stock, marcas)
        b["eventos"] = [{"fecha": m["fecha"].strftime("%d/%m/%Y"), "tipo": m["etiqueta"],
                         "delta": m["texto"], "stock": f"{m['stock']:,.0f}"}
                        for m in sorted(marcas, key=lambda m: m["fecha"])][-12:]
        b["stock_final"] = stock[-1]
        if c in resumen.index:
            r = resumen.loc[c]
            b.update({"dias_quiebre": int(r.get("dias_quiebre", 0) or 0),
                      "stock_min": float(r.get("stock_min", 0) or 0),
                      "stock_max": float(r.get("stock_max", 0) or 0),
                      "stock_prom": float(r.get("stock_prom", 0) or 0)})
        bloques.append(b)

    corte = _corte(proyecto, p["codigodepo"], p["hasta"])
    html = _env().get_template("nc_anexo_serie.html").render(
        bloques=bloques, p=p, corte=corte, fecha_stock=serie.attrs.get("fecha_stock"),
        desde=pd.to_datetime(p["desde"]).strftime("%d/%m/%Y"),
        hasta=pd.to_datetime(p["hasta"]).strftime("%d/%m/%Y"),
        marcas=[v for v in _MARCAS.values()])
    titulo = "Serie de stock — " + ", ".join(codigos)
    return {"tipo": "serie_stock", "titulo": titulo, "html": html, "corte_datos": corte,
            "resumen": {"codigos": codigos, "fecha_stock": str(serie.attrs.get("fecha_stock"))}}


# ---------------------------------------------------------------------------
# Merma
# ---------------------------------------------------------------------------

_ETIQ_MODO = {"costo": "a costo", "lista_1": "a precio de lista",
              "mixto": "mixto (ventas a lista, resto a costo)"}


def _categorias_merma(proyecto: str) -> set[str]:
    from core.comun import conectar
    conn = conectar(proyecto)
    try:
        df = conn.execute("SELECT DISTINCT categoria FROM tipos_categoria WHERE es_merma").df()
    finally:
        conn.close()
    return set(df["categoria"])


def _anexo_merma(proyecto: str, p: dict) -> dict:
    from core.merma.analisis import calcular_merma

    modo = p.get("modo_valorizacion") or "costo"
    df = calcular_merma(proyecto, p["codigodepo"], p["desde"], p["hasta"],
                        modo_valorizacion=modo)
    rubros_sel = p.get("rubros") or []
    if rubros_sel:
        df = df[df["rubro"].astype(str).isin(rubros_sel)]
    top_n = int(p.get("top_n") or 15)
    destacar = [c.strip() for c in (p.get("codigos") or []) if str(c).strip()]

    merma = float(df["merma_total_valorizada"].sum())
    merma_u = float(df["merma_total_unidades"].sum())
    venta = float(df["venta_neta"].sum())
    pct = merma / venta * 100 if venta > 0 else None

    def filas(sub):
        return [{"codigo": r.codigo, "descripcion": r.descripcion or "", "rubro": r.rubro or "",
                 "unidades": r.merma_total_unidades, "valor": r.merma_total_valorizada,
                 "pct": r.pct_merma_sobre_ventas if pd.notna(r.pct_merma_sobre_ventas) else None,
                 "destacado": r.codigo in destacar}
                for r in sub.itertuples()]

    top = df[df["merma_total_valorizada"] > 0].nlargest(top_n, "merma_total_valorizada")
    extra = df[df["codigo"].isin(destacar) & ~df["codigo"].isin(top["codigo"])]
    no_encontrados = [c for c in destacar if c not in set(df["codigo"])]

    cats_merma = _categorias_merma(proyecto)
    cats = [c for c in df.attrs.get("categorias", []) if c in df.columns and c in cats_merma]
    por_cat = [{"categoria": c, "valor": float(df[c].sum())} for c in cats]
    por_cat = [x for x in por_cat if abs(x["valor"]) >= 0.5]

    corte = _corte(proyecto, p["codigodepo"], p["hasta"])
    fval = df.attrs.get("fecha_valorizacion")
    html = _env().get_template("nc_anexo_merma.html").render(
        p=p, modo=_ETIQ_MODO.get(modo, modo), merma=merma, merma_u=merma_u, venta=venta,
        pct=pct, skus=int((df["merma_total_valorizada"] > 0).sum()), top=filas(top),
        extra=filas(extra), no_encontrados=no_encontrados, por_cat=por_cat, corte=corte,
        fecha_valorizacion=fval, rubros=rubros_sel, top_n=top_n,
        desde=pd.to_datetime(p["desde"]).strftime("%d/%m/%Y"),
        hasta=pd.to_datetime(p["hasta"]).strftime("%d/%m/%Y"))
    periodo = (f"{pd.to_datetime(p['desde']):%d/%m/%Y}–{pd.to_datetime(p['hasta']):%d/%m/%Y}")
    titulo = f"Merma {periodo}" + (f" · {', '.join(rubros_sel)}" if rubros_sel else "")
    fuente = (f"Merma {p.get('sucursal_nombre') or p['codigodepo']} {periodo}, "
              f"{_ETIQ_MODO.get(modo, modo)}" + (f", rubros: {', '.join(rubros_sel)}"
                                                 if rubros_sel else "")
              + (f" (datos al {corte})" if corte else ""))
    return {"tipo": "merma", "titulo": titulo, "html": html, "corte_datos": corte,
            "resumen": {"merma_valorizada": round(merma, 2), "merma_unidades": merma_u,
                        "venta_neta": round(venta, 2),
                        "pct_merma": round(pct, 4) if pct is not None else None,
                        "fuente": fuente, "fecha_valorizacion": str(fval)}}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def generar(proyecto: str, tipo: str, params: dict) -> dict:
    """Genera un anexo. `params` lleva siempre codigodepo, desde y hasta (ISO)."""
    p = dict(params)
    for k in ("desde", "hasta"):
        if isinstance(p.get(k), date):
            p[k] = p[k].isoformat()
    if p["desde"] > p["hasta"]:
        raise ValueError("La fecha desde no puede ser posterior a la fecha hasta.")
    if tipo == "serie_stock":
        out = _anexo_serie(proyecto, p)
    elif tipo == "merma":
        out = _anexo_merma(proyecto, p)
    else:
        raise ValueError(f"Tipo de anexo desconocido: {tipo}")
    out["params"] = p
    return out
