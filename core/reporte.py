"""
core/reporte.py — Reportes imprimibles (HTML autocontenido) con marca del cliente

Todos los reportes heredan de templates/base.html: encabezado con el logo y
los datos del cliente, color de acento propio y pie personalizado. El HTML es
autocontenido (CSS puro, logo embebido en base64): se descarga desde la UI,
se abre en el navegador y se imprime o guarda como PDF con Ctrl+P.

Branding por proyecto (fuera de git, junto a su DB):
    projects/<proyecto>/branding.json   nombre, datos[], color, pie
    projects/<proyecto>/logo.png|jpg    embebido como data-URI

Generadores (uno por página de la app):
    generar_reporte(...)            Análisis de merma (compat: firma histórica)
    generar_reporte_control(...)    Control de merma
    generar_reporte_salud(...)      Salud de stock
    generar_reporte_politicas(...)  Min / Opt / Max
    generar_reporte_forecast(...)   Forecast
"""

import base64
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

def _dir_proyecto(proyecto: str) -> Path:
    from setup_db import get_db_path
    return get_db_path(proyecto).parent


def cargar_branding(proyecto: str | None) -> dict:
    """
    Lee projects/<proyecto>/branding.json y el logo (si existen).
    Retorna siempre un dict usable: nombre, datos (lista), color, pie, logo_uri.
    """
    branding = {"nombre": "", "datos": [], "color": "", "pie": "", "logo_uri": ""}
    if not proyecto:
        return branding
    d = _dir_proyecto(proyecto)

    fj = d / "branding.json"
    if fj.exists():
        try:
            data = json.loads(fj.read_text(encoding="utf-8"))
            branding.update({k: data.get(k, branding[k]) for k in
                             ("nombre", "datos", "color", "pie")})
            if isinstance(branding["datos"], str):
                branding["datos"] = [x for x in branding["datos"].splitlines() if x.strip()]
        except Exception:
            pass

    for ext, mime in _MIME.items():
        fl = d / f"logo{ext}"
        if fl.exists():
            b64 = base64.b64encode(fl.read_bytes()).decode("ascii")
            branding["logo_uri"] = f"data:{mime};base64,{b64}"
            break
    return branding


def guardar_branding(proyecto: str, nombre: str = "", datos: list | str = "",
                     color: str = "", pie: str = "",
                     logo_bytes: bytes | None = None,
                     logo_ext: str = ".png") -> None:
    """Persiste el branding del proyecto (JSON + archivo de logo)."""
    d = _dir_proyecto(proyecto)
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(datos, str):
        datos = [x for x in datos.splitlines() if x.strip()]
    (d / "branding.json").write_text(
        json.dumps({"nombre": nombre, "datos": datos, "color": color, "pie": pie},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if logo_bytes:
        logo_ext = logo_ext.lower()
        if not logo_ext.startswith("."):
            logo_ext = "." + logo_ext
        # un solo logo por proyecto: borrar variantes anteriores
        for ext in _MIME:
            (d / f"logo{ext}").unlink(missing_ok=True)
        (d / f"logo{logo_ext}").write_bytes(logo_bytes)


# ---------------------------------------------------------------------------
# Filtros de formato y render
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


def _render(template: str, proyecto: str | None, titulo: str,
            meta: dict, **contexto) -> str:
    meta = dict(meta)
    meta.setdefault("generado", datetime.now().strftime("%d/%m/%Y %H:%M"))
    return _env().get_template(template).render(
        titulo=titulo, meta=meta, branding=cargar_branding(proyecto), **contexto,
    )


def _merma_cat(df: pd.DataFrame, cat: str) -> pd.Series:
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return (-df[cat]).clip(lower=0)


def _w(valor: float, maximo: float) -> float:
    return round(valor / maximo * 100, 1) if maximo and maximo > 0 else 0.0


# ---------------------------------------------------------------------------
# 1. Análisis de merma
# ---------------------------------------------------------------------------

def generar_reporte(
    df: pd.DataFrame,
    meta: dict,
    merma_cats: list[str],
    df_sucursales: pd.DataFrame | None = None,
    top_n: int = 20,
) -> str:
    """Reporte de la página Análisis (el proyecto sale de meta['proyecto'])."""
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
        t_merma = float(df_sucursales["merma_total_valorizada"].sum())
        t_venta = float(df_sucursales["venta_neta"].sum())
        suc_total = {
            "skus_con_merma": int(df_sucursales["skus_con_merma"].sum()),
            "merma":          t_merma,
            "merma_u":        float(df_sucursales["merma_total_unidades"].sum()),
            "venta":          t_venta,
            "pct":            (t_merma / t_venta * 100) if t_venta > 0 else None,
        }

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
    meta["modo"] = "a costo" if meta.get("modo") == "costo" else "a precio de lista"

    return _render(
        "reporte_merma.html", meta.get("proyecto"), "Reporte de merma", meta,
        kpi=kpi, composicion=composicion, sucursales=sucursales,
        suc_total=suc_total, rubros=rubros,
        nivel_rubro="gran super rubro" if nivel == "gran_super_rubro" else "rubro",
        top_skus=top_skus,
    )


# ---------------------------------------------------------------------------
# 2. Control de merma
# ---------------------------------------------------------------------------

def generar_reporte_control(
    proyecto: str,
    meta: dict,
    evolucion: pd.DataFrame,
    outliers: pd.DataFrame,
    usuarios: pd.DataFrame,
    top_outliers: int = 25,
    top_usuarios: int = 20,
) -> str:
    """Reporte de la página Control de merma."""
    kpi = None
    ev = []
    if evolucion is not None and not evolucion.empty:
        e_max = float(evolucion["merma_total"].max())
        for _, r in evolucion.iterrows():
            ev.append({
                "mes":     r["mes"],
                "merma":   r["merma_total"],
                "merma_u": r["merma_unidades"],
                "venta":   r["venta_neta"],
                "pct":     r["pct_merma_sobre_ventas"],
                "merma_w": _w(r["merma_total"], e_max),
            })
        if len(evolucion) >= 2:
            ult, ant = evolucion.iloc[-1], evolucion.iloc[-2]
            kpi = {
                "mes":         ult["mes"],
                "merma":       ult["merma_total"],
                "venta":       ult["venta_neta"],
                "pct":         ult["pct_merma_sobre_ventas"],
                "delta_merma": _pesos(ult["merma_total"] - ant["merma_total"]),
            }

    outs = []
    if outliers is not None and not outliers.empty:
        for _, o in outliers.head(top_outliers).iterrows():
            outs.append({
                "fecha":       str(pd.to_datetime(o["fecha"]).date()),
                "sucursal":    o["sucursal"] if pd.notna(o["sucursal"]) else o["codigodepo"],
                "tipo":        o["tipo"], "numero": o["numero"],
                "usuario":     o["usuario"] if pd.notna(o["usuario"]) else "",
                "codigo":      o["codigo"],
                "descripcion": (o["descripcion"] or "")[:38] if pd.notna(o["descripcion"]) else "",
                "unidades":    o["unidades"], "valor": o["valor"],
                "pct":         o["pct_merma_periodo"],
            })

    us = []
    if usuarios is not None and not usuarios.empty:
        u_max = float(usuarios["faltante_valorizado"].max())
        for _, u in usuarios.head(top_usuarios).iterrows():
            us.append({
                "usuario":    u["usuario"], "suc": u["codigodepo"],
                "movs":       u["movimientos"], "skus": u["skus"],
                "faltante":   u["faltante_valorizado"],
                "sobrante":   u["sobrante_valorizado"],
                "neto":       u["neto_valorizado"],
                "pct":        u["pct_faltante"],
                "faltante_w": _w(u["faltante_valorizado"], u_max),
            })

    meta = dict(meta)
    meta["modo"] = "a costo" if meta.get("modo") == "costo" else "a precio de lista"
    return _render(
        "reporte_control.html", proyecto, "Control de merma", meta,
        kpi=kpi, evolucion=ev, outliers=outs, usuarios=us,
    )


# ---------------------------------------------------------------------------
# 3. Salud de stock
# ---------------------------------------------------------------------------

def generar_reporte_salud(
    proyecto: str,
    meta: dict,
    df: pd.DataFrame,
    resumen: dict,
    etiquetas: dict,
    top_n: int = 20,
) -> str:
    """Reporte de la página Salud de stock."""
    kpi = {
        "quiebres":      resumen["por_estado"]["quiebre"],
        "venta_perdida": resumen["venta_perdida_diaria"],
        "muerto_valor":  resumen["stock_muerto_valor"],
        "muertos":       resumen["por_estado"]["muerto"],
        "sobre_valor":   resumen["sobrestock_valor"],
        "sobres":        resumen["por_estado"]["sobrestock"],
        "cobertura":     (f"{resumen['cobertura_mediana']:.0f} días"
                          if resumen["cobertura_mediana"] else "—"),
        "capital":       resumen["stock_valor_total"],
    }

    val_por_estado = df.groupby("estado")["stock_valorizado"].sum()
    v_max = float(val_por_estado.max()) if len(val_por_estado) else 0
    estados = [{
        "nombre":  etiquetas.get(e, e),
        "n":       resumen["por_estado"].get(e, 0),
        "valor":   float(val_por_estado.get(e, 0.0)),
        "valor_w": _w(float(val_por_estado.get(e, 0.0)), v_max),
    } for e in etiquetas]

    def _fila(s, extra):
        base = {
            "codigo":      s["codigo"],
            "descripcion": (s["descripcion"] or "")[:42] if pd.notna(s["descripcion"]) else "",
            "rubro":       s["rubro"] if pd.notna(s["rubro"]) else "",
            "suc":         s["codigodepo"],
        }
        base.update(extra(s))
        return base

    df_q = df[df["estado"] == "quiebre"].nlargest(top_n, "venta_perdida_diaria")
    quiebres = [_fila(s, lambda s: {
        "venta_diaria": f"{s['venta_diaria']:.2f}",
        "perdida":      s["venta_perdida_diaria"],
    }) for _, s in df_q.iterrows()]

    df_m = df[df["estado"] == "muerto"].nlargest(top_n, "stock_valorizado")
    muertos = [_fila(s, lambda s: {
        "stock": s["stock"], "valor": s["stock_valorizado"],
    }) for _, s in df_m.iterrows()]

    return _render(
        "reporte_salud.html", proyecto, "Salud de stock", meta,
        kpi=kpi, estados=estados, quiebres=quiebres, muertos=muertos,
    )


# ---------------------------------------------------------------------------
# 4. Min / Opt / Max
# ---------------------------------------------------------------------------

def generar_reporte_politicas(
    proyecto: str,
    meta: dict,
    df: pd.DataFrame,
    resumen: dict,
    top_n: int = 30,
) -> str:
    """Reporte de la página Min / Opt / Max."""
    df_rep = df[df["estado"] == "reponer"]
    kpi = {
        "reponer":      resumen["por_estado"]["reponer"],
        "compra":       resumen["compra_sugerida"],
        "compra_u":     float(df_rep["compra_sugerida"].sum()),
        "exceso":       resumen["por_estado"]["exceso"],
        "exceso_valor": resumen["exceso_valorizado"],
        "ok":           resumen["por_estado"]["ok"],
        "ciclo":        (f"{resumen['ciclo_mediano']:.0f} días"
                         if resumen["ciclo_mediano"] else "—"),
    }

    mat = resumen.get("matriz_abc_xyz", {})
    matriz = [{
        "clase": clase,
        "x": mat.get((clase, "X"), 0),
        "y": mat.get((clase, "Y"), 0),
        "z": mat.get((clase, "Z"), 0),
    } for clase in ("A", "B", "C")]

    compras = [{
        "codigo":        c["codigo"],
        "descripcion":   (c["descripcion"] or "")[:40] if pd.notna(c["descripcion"]) else "",
        "suc":           c["codigodepo"], "abc": c["clase_abc"],
        "stock":         c["stock"], "minimo": c["minimo"], "optimo": c["optimo"],
        "comprar":       c["compra_sugerida"], "comprar_valor": c["compra_valorizada"],
    } for _, c in df_rep.nlargest(top_n, "compra_valorizada").iterrows()]

    df_exc = df[df["estado"] == "exceso"].nlargest(15, "exceso_valorizado")
    excesos = [{
        "codigo":       e["codigo"],
        "descripcion":  (e["descripcion"] or "")[:40] if pd.notna(e["descripcion"]) else "",
        "suc":          e["codigodepo"], "stock": e["stock"], "maximo": e["maximo"],
        "exceso":       e["exceso_unidades"], "exceso_valor": e["exceso_valorizado"],
    } for _, e in df_exc.iterrows()]

    return _render(
        "reporte_politicas.html", proyecto, "Políticas de stock (Mín/Ópt/Máx)",
        meta, kpi=kpi, matriz=matriz, compras=compras, excesos=excesos,
    )


# ---------------------------------------------------------------------------
# 5. Transferencias
# ---------------------------------------------------------------------------

def generar_reporte_transferencias(
    proyecto: str,
    meta: dict,
    df: pd.DataFrame,
) -> str:
    """
    Reporte operativo de transferencias: una sección por ruta origen→destino
    con la lista de SKUs a enviar (y columna ✓ para tildar en papel).
    """
    kpi = {
        "skus":     int(df["codigo"].nunique()) if not df.empty else 0,
        "unidades": float(df["unidades"].sum()) if not df.empty else 0.0,
        "valor":    float(df["valor"].sum()) if not df.empty else 0.0,
    }

    rutas = []
    if not df.empty:
        for (og, ogn, de, den), g in df.groupby(
                ["origen", "origen_nombre", "destino", "destino_nombre"]):
            items = [{
                "codigo":        i["codigo"],
                "descripcion":   (i["descripcion"] or "")[:44] if pd.notna(i["descripcion"]) else "",
                "rubro":         i["rubro"] if pd.notna(i["rubro"]) else "",
                "abc":           i["clase_abc"],
                "unidades":      i["unidades"],
                "valor":         i["valor"],
                "stock_origen":  i["stock_origen"],
                "stock_destino": i["stock_destino"],
            } for _, i in g.sort_values("valor", ascending=False).iterrows()]
            rutas.append({
                "origen":   f"{og} — {ogn}" if pd.notna(ogn) else og,
                "destino":  f"{de} — {den}" if pd.notna(den) else de,
                "lineas":   items,
                "unidades": float(g["unidades"].sum()),
                "valor":    float(g["valor"].sum()),
            })
        rutas.sort(key=lambda r: -r["valor"])

    meta = dict(meta)
    meta["n_sugerencias"] = len(df)
    return _render(
        "reporte_transferencias.html", proyecto, "Transferencias sugeridas",
        meta, kpi=kpi, rutas=rutas,
    )


# ---------------------------------------------------------------------------
# 6. Forecast
# ---------------------------------------------------------------------------

def generar_reporte_forecast(
    proyecto: str,
    meta: dict,
    df_fc: pd.DataFrame,
    grupo_detalle: str | None = None,
    meses_historia: int = 12,
) -> str:
    """Reporte de la página Forecast."""
    fc = df_fc[df_fc["tipo"] == "forecast"]
    meses = sorted(fc["mes"].unique().tolist())
    mapes = df_fc.attrs.get("mape", {})
    atipicos = df_fc.attrs.get("meses_atipicos", {})

    grupos = []
    for g, gg in fc.groupby("grupo"):
        serie = gg.set_index("mes")["unidades"]
        m = mapes.get(str(g))
        grupos.append({
            "nombre":      g,
            "valores":     [float(serie.get(mm, 0.0)) for mm in meses],
            "total":       float(gg["unidades"].sum()),
            "total_valor": float(gg["valor"].sum()),
            "mape":        f"{m:.0f}%" if m is not None else "—",
        })
    grupos.sort(key=lambda x: -x["total"])

    total_fila = None
    if len(grupos) > 1:
        total_fila = {
            "valores":     [sum(g["valores"][i] for g in grupos) for i in range(len(meses))],
            "total":       sum(g["total"] for g in grupos),
            "total_valor": sum(g["total_valor"] for g in grupos),
        }

    historia = None
    if grupo_detalle is not None:
        g = df_fc[df_fc["grupo"] == grupo_detalle]
        at_g = set(atipicos.get(str(grupo_detalle), []))
        reales = g[g["tipo"] == "real"].tail(meses_historia)
        filas = pd.concat([reales, g[g["tipo"] == "forecast"]])
        u_max = float(filas["unidades"].max()) if len(filas) else 0
        historia = {
            "grupo": grupo_detalle,
            "filas": [{
                "mes":      f["mes"], "tipo": f["tipo"],
                "unidades": f["unidades"],
                "atipico":  f["mes"] in at_g,
                "w":        _w(float(f["unidades"]), u_max),
            } for _, f in filas.iterrows()],
        }

    at_txt = "; ".join(f"{g}: {', '.join(m)}" for g, m in list(atipicos.items())[:5])
    return _render(
        "reporte_forecast.html", proyecto, "Forecast", meta,
        meses=meses, grupos=grupos, total_fila=total_fila,
        historia=historia, atipicos=at_txt,
    )
