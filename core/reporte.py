"""
core/reporte.py — Reporte imprimible de merma (HTML autocontenido)

Renderiza templates/reporte.html (Jinja2) con el resultado del análisis.
El HTML es autocontenido (CSS inline, gráficos de barras en CSS puro, sin
JS ni dependencias externas): se descarga desde la UI, se abre en el
navegador y se imprime o guarda como PDF con Ctrl+P.

Uso:
    from core.reporte import generar_reporte

    html = generar_reporte(
        df=df_merma,                 # resultado de calcular_merma (filtrado o no)
        meta={...},                  # proyecto, sucursal, fechas, modo, filtros
        df_sucursales=df_comp,       # opcional: resultado de merma_por_sucursal
        merma_cats=[...],            # categorías que cuentan como merma
        top_n=20,
    )
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


# ---------------------------------------------------------------------------
# Filtros de formato (formato argentino)
# ---------------------------------------------------------------------------

def _pesos(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"$ {v:,.0f}"


def _unidades(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.0f}"


def _pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.2f}%"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    env.filters["pesos"] = _pesos
    env.filters["unidades"] = _unidades
    env.filters["pct"] = _pct
    return env


def _merma_cat(df: pd.DataFrame, cat: str) -> pd.Series:
    """Magnitud de merma (parte negativa, en positivo) de una columna-categoría."""
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return (-df[cat]).clip(lower=0)


def _w(valor: float, maximo: float) -> float:
    """Ancho de barra CSS (0-100), protegido contra máximo 0."""
    return round(valor / maximo * 100, 1) if maximo > 0 else 0.0


# ---------------------------------------------------------------------------
# Generador
# ---------------------------------------------------------------------------

def generar_reporte(
    df: pd.DataFrame,
    meta: dict,
    merma_cats: list[str],
    df_sucursales: pd.DataFrame | None = None,
    top_n: int = 20,
) -> str:
    """
    Genera el HTML del reporte.

    Args:
        df:            resultado de calcular_merma (ya filtrado por la UI)
        meta:          dict con proyecto, sucursal, fecha_desde, fecha_hasta,
                       modo, fecha_valorizacion y filtros (texto o "")
        merma_cats:    categorías que suman a la merma (para la composición)
        df_sucursales: resultado de merma_por_sucursal (o None si es una sola)
        top_n:         cantidad de SKUs en el top

    Returns:
        HTML como string.
    """
    merma_total = float(df["merma_total_valorizada"].sum())
    venta_total = float(df["venta_neta"].sum())

    kpi = {
        "skus":           len(df),
        "skus_con_merma": int((df["merma_total_valorizada"] > 0).sum()),
        "merma":          merma_total,
        "merma_u":        float(df["merma_total_unidades"].sum()),
        "venta":          venta_total,
        "venta_u":        float(df["unidades_vendidas"].sum()),
        "pct":            (merma_total / venta_total * 100) if venta_total > 0 else None,
    }

    # --- composición por categoría ------------------------------------------
    composicion = []
    for c in merma_cats:
        v = float(_merma_cat(df, c).sum())
        cu = f"{c} (u)"
        u = float((-df[cu]).clip(lower=0).sum()) if cu in df.columns else 0.0
        if v > 0:
            composicion.append({"nombre": c, "valor": v, "unidades": u})
    comp_max = max((c["valor"] for c in composicion), default=0)
    for c in composicion:
        c["share"] = c["valor"] / merma_total * 100 if merma_total > 0 else 0
        c["share_w"] = _w(c["valor"], comp_max)
    composicion.sort(key=lambda c: -c["valor"])

    # --- comparativa por sucursal ---------------------------------------------
    sucursales, suc_total = [], None
    if df_sucursales is not None and not df_sucursales.empty:
        suc_max = float(df_sucursales["merma_total_valorizada"].max())
        for _, s in df_sucursales.iterrows():
            nombre = s["nombre"] if pd.notna(s["nombre"]) else s["codigodepo"]
            sucursales.append({
                "nombre":         f"{s['codigodepo']} — {nombre}",
                "skus_con_merma": s["skus_con_merma"],
                "merma":          s["merma_total_valorizada"],
                "merma_u":        s["merma_total_unidades"],
                "venta":          s["venta_neta"],
                "pct":            s["pct_merma_sobre_ventas"],
                "merma_w":        _w(s["merma_total_valorizada"], suc_max),
            })
        # Totales de la tabla (suma de filas — puede diferir levemente del KPI
        # global, que netea cada SKU entre sucursales)
        t_merma = float(df_sucursales["merma_total_valorizada"].sum())
        t_venta = float(df_sucursales["venta_neta"].sum())
        suc_total = {
            "skus_con_merma": int(df_sucursales["skus_con_merma"].sum()),
            "merma":          t_merma,
            "merma_u":        float(df_sucursales["merma_total_unidades"].sum()),
            "venta":          t_venta,
            "pct":            (t_merma / t_venta * 100) if t_venta > 0 else None,
        }

    # --- merma por gran super rubro (o rubro) ----------------------------------
    nivel = "gran_super_rubro" if df["gran_super_rubro"].notna().any() else "rubro"
    df_r = (
        df.groupby(nivel, dropna=False)
        .agg(merma=("merma_total_valorizada", "sum"), venta=("venta_neta", "sum"))
        .reset_index()
        .sort_values("merma", ascending=False)
    )
    df_r = df_r[df_r["merma"] > 0].head(12)
    r_max = float(df_r["merma"].max()) if not df_r.empty else 0
    rubros = [{
        "nombre":  r[nivel] if pd.notna(r[nivel]) else "Sin clasificar",
        "merma":   r["merma"],
        "venta":   r["venta"],
        "pct":     (r["merma"] / r["venta"] * 100) if r["venta"] > 0 else None,
        "share":   (r["merma"] / merma_total * 100) if merma_total > 0 else 0,
        "share_w": _w(r["merma"], r_max),
    } for _, r in df_r.iterrows()]

    # --- top SKUs ---------------------------------------------------------------
    df_top = df[df["merma_total_valorizada"] > 0].nlargest(top_n, "merma_total_valorizada")
    top_skus = [{
        "codigo":      s["codigo"],
        "descripcion": (s["descripcion"] or "")[:50] if pd.notna(s["descripcion"]) else "",
        "rubro":       s["rubro"] if pd.notna(s["rubro"]) else "",
        "merma":       s["merma_total_valorizada"],
        "merma_u":     s["merma_total_unidades"],
        "venta":       s["venta_neta"],
        "pct":         s["pct_merma_sobre_ventas"],
    } for _, s in df_top.iterrows()]

    meta = dict(meta)
    meta.setdefault("generado", datetime.now().strftime("%d/%m/%Y %H:%M"))
    meta["modo"] = "a costo" if meta.get("modo") == "costo" else "a precio de lista"

    template = _env().get_template("reporte.html")
    return template.render(
        meta=meta, kpi=kpi, composicion=composicion,
        sucursales=sucursales, suc_total=suc_total, rubros=rubros,
        nivel_rubro="gran super rubro" if nivel == "gran_super_rubro" else "rubro",
        top_skus=top_skus,
    )
