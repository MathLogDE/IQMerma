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
    generar_reporte(...)               Análisis de merma (compat: firma histórica)
    generar_reporte_control(...)       Control de merma
    generar_reporte_salud(...)         Salud de stock
    generar_reporte_politicas(...)     Min / Opt / Max
    generar_reporte_transferencias(...) Transferencias sugeridas
    generar_reporte_forecast(...)      Forecast
    generar_reporte_diferencias(...)   Diferencias de camión
    generar_reporte_interanual(...)    Evolución interanual
    generar_reporte_cruce(...)         Cruce por sucursal
    generar_reporte_series(...)        Serie de stock y quiebres
    generar_reporte_reposicion(...)    Perfil de reposición
    generar_reporte_comprobantes(...)  Comprobantes y canasta
    generar_reporte_avance_general(...) Avance de inventario — matriz por sucursal
    generar_reporte_avance_conteo(...)  Avance de inventario — conteo y diferencias
    generar_reporte_pedido_sucursal(...) Pedido desde sucursal
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

# Etiquetas de modo_valorizacion para los reportes de Merma (generar_reporte,
# generar_reporte_control). "mixto": ventas a precio de venta, resto a costo.
_ETIQUETAS_MODO = {
    "costo": "a costo",
    "lista_1": "a precio de lista",
    "mixto": "mixto (ventas a precio de venta, resto a costo)",
}

# Isologo de producto (no confundir con branding["logo_uri"], que es el logo
# del CLIENTE por proyecto): se embebe una sola vez al importar el módulo.
_LOGO_IQMERMA_PATH = Path(__file__).parent.parent / "ui" / "iqmerma_logo.png"


def _cargar_logo_iqmerma() -> str:
    if not _LOGO_IQMERMA_PATH.exists():
        return ""
    b64 = base64.b64encode(_LOGO_IQMERMA_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


LOGO_IQMERMA_URI = _cargar_logo_iqmerma()


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

def _sin_cero_negativo(v: float) -> float:
    """Un neto que se cancela da -0.0 y se imprimiría como '-0'."""
    return 0.0 if abs(v) < 0.5 else v


def _pesos(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"$ {_sin_cero_negativo(v):,.0f}"


def _unidades(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{_sin_cero_negativo(v):,.0f}"


def _pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.2f}%"


def _dias(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.1f} días"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    env.filters["pesos"] = _pesos
    env.filters["unidades"] = _unidades
    env.filters["pct"] = _pct
    env.filters["dias"] = _dias
    return env


def _render(template: str, proyecto: str | None, titulo: str,
            meta: dict, **contexto) -> str:
    meta = dict(meta)
    meta.setdefault("generado", datetime.now().strftime("%d/%m/%Y %H:%M"))
    return _env().get_template(template).render(
        titulo=titulo, meta=meta, branding=cargar_branding(proyecto),
        logo_iqmerma=LOGO_IQMERMA_URI, **contexto,
    )


def _merma_cat(df: pd.DataFrame, cat: str) -> pd.Series:
    """Aporte de la categoría a la merma (ya viene con el signo aplicado,
    ver core.merma._ensamblar: faltante positivo, ajuste positivo negativo)."""
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return df[cat]


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
    skus_inv: int | None = None,
    skus_total: int | None = None,
    pct_skus_contados: float | None = None,
    nivel: str | None = None,
    vista: str = "ambos",
) -> str:
    """Reporte de la página Análisis (el proyecto sale de meta['proyecto']).

    `skus_inv`/`skus_total`/`pct_skus_contados`: SKUs con conteo de
    inventario (INV) sobre el total de SKUs con movimientos, calculado en
    calcular_merma sobre el universo completo del período (no se recalcula
    con los filtros de la UI, a diferencia del resto de `kpi`).

    `nivel`: "rubro" | "super_rubro" | "gran_super_rubro" para forzar el
    agrupamiento de la sección "Merma por X" — None (default) mantiene el
    auto-detect histórico (gran_super_rubro si el df lo trae, si no rubro).
    `vista`: "valorizado" | "unidades" | "ambos" (default) — qué columnas
    muestran las tablas del imprimible (composición, comparativa por
    sucursal, rubros, top SKUs).
    """
    if nivel is not None and nivel not in ("rubro", "super_rubro", "gran_super_rubro"):
        raise ValueError(
            f"nivel debe ser 'rubro', 'super_rubro' o 'gran_super_rubro', no '{nivel}'"
        )
    vista = (vista or "ambos").lower()
    if vista not in ("valorizado", "unidades", "ambos"):
        raise ValueError(f"vista debe ser 'valorizado', 'unidades' o 'ambos', no '{vista}'")

    merma_total = float(df["merma_total_valorizada"].sum())
    venta_total = float(df["venta_neta"].sum())

    kpi = {
        "skus":              len(df),
        "skus_con_merma":    int((df["merma_total_valorizada"] > 0).sum()),
        "merma":             merma_total,
        "merma_u":           float(df["merma_total_unidades"].sum()),
        "venta":             venta_total,
        "venta_u":           float(df["unidades_vendidas"].sum()),
        "pct":               (merma_total / venta_total * 100) if venta_total > 0 else None,
        "skus_inv":          skus_inv,
        "skus_total":        skus_total,
        "pct_skus_contados": pct_skus_contados,
    }

    composicion = []
    for c in merma_cats:
        v = float(_merma_cat(df, c).sum())            # aporte con signo
        cu = f"{c} (u)"
        u = float(df[cu].sum()) if cu in df.columns else 0.0
        if abs(v) > 0:
            composicion.append({"nombre": c, "valor": v, "unidades": u})
    comp_max = max((abs(c["valor"]) for c in composicion), default=0)
    for c in composicion:
        c["share"] = c["valor"] / merma_total * 100 if merma_total else 0
        c["share_w"] = _w(abs(c["valor"]), comp_max)
        c["neg"] = c["valor"] < 0
    composicion.sort(key=lambda c: -c["valor"])

    sucursales, suc_total = [], None
    es_dinamico = (df_sucursales is not None
                   and "dias_promedio_corte" in df_sucursales.columns)
    if df_sucursales is not None and not df_sucursales.empty:
        suc_max = float(df_sucursales["merma_total_valorizada"].abs().max())
        for _, s in df_sucursales.iterrows():
            nombre = s["nombre"] if pd.notna(s["nombre"]) else s["codigodepo"]
            sucursales.append({
                "nombre":            f"{s['codigodepo']} — {nombre}",
                "skus_con_merma":    s["skus_con_merma"],
                "merma":             s["merma_total_valorizada"],
                "merma_u":           s["merma_total_unidades"],
                "venta":             s["venta_neta"],
                "pct":               s["pct_merma_sobre_ventas"],
                "pct_skus_contados": s.get("pct_skus_contados"),
                "dias_promedio":     s.get("dias_promedio_corte") if es_dinamico else None,
                "merma_w":           _w(abs(s["merma_total_valorizada"]), suc_max),
                "neg":               s["merma_total_valorizada"] < 0,
            })
        t_merma = float(df_sucursales["merma_total_valorizada"].sum())
        t_venta = float(df_sucursales["venta_neta"].sum())
        t_skus = int(df_sucursales["skus"].sum()) if "skus" in df_sucursales else 0
        t_skus_inv = int(df_sucursales["skus_inv"].sum()) if "skus_inv" in df_sucursales else 0
        t_dias = None
        if es_dinamico:
            t_pares = df_sucursales["n_pares_corte"].sum()
            if t_pares > 0:
                t_dias = float(
                    (df_sucursales["dias_promedio_corte"] * df_sucursales["n_pares_corte"]).sum()
                    / t_pares
                )
        suc_total = {
            "skus_con_merma":    int(df_sucursales["skus_con_merma"].sum()),
            "merma":             t_merma,
            "merma_u":           float(df_sucursales["merma_total_unidades"].sum()),
            "venta":             t_venta,
            "pct":               (t_merma / t_venta * 100) if t_venta > 0 else None,
            "pct_skus_contados": (t_skus_inv / t_skus * 100) if t_skus > 0 else None,
            "dias_promedio":     t_dias,
            "neg":               t_merma < 0,
        }

    if nivel is None:
        nivel = "gran_super_rubro" if df["gran_super_rubro"].notna().any() else "rubro"
    agg_r = dict(merma=("merma_total_valorizada", "sum"),
                merma_u=("merma_total_unidades", "sum"),
                venta=("venta_neta", "sum"))
    if "tuvo_inv" in df.columns:
        agg_r["skus"] = ("codigo", "count")
        agg_r["skus_inv"] = ("tuvo_inv", "sum")
    df_r = (
        df.groupby(nivel, dropna=False)
        .agg(**agg_r)
        .reset_index()
        .sort_values("merma", ascending=False)
    )
    # se conservan los rubros con merma negativa (sobrante neto): se muestran
    # en verde. Se toman los 12 de mayor impacto absoluto.
    df_r = df_r.reindex(df_r["merma"].abs().sort_values(ascending=False).index).head(12)
    r_max = float(df_r["merma"].abs().max()) if not df_r.empty else 0
    rubros = [{
        "nombre":  r[nivel] if pd.notna(r[nivel]) else "Sin clasificar",
        "merma":   r["merma"],
        "merma_u": r["merma_u"],
        "venta":   r["venta"],
        "pct":     (r["merma"] / r["venta"] * 100) if r["venta"] > 0 else None,
        "pct_skus_contados": (r["skus_inv"] / r["skus"] * 100)
                             if "skus" in r and r["skus"] > 0 else None,
        "share":   (r["merma"] / merma_total * 100) if merma_total else 0,
        "share_w": _w(abs(r["merma"]), r_max),
        "neg":     r["merma"] < 0,
    } for _, r in df_r.iterrows()]

    df_top = df[df["merma_total_valorizada"] > 0].nlargest(top_n, "merma_total_valorizada")
    top_skus = [{
        "codigo":      s["codigo"],
        "descripcion": (s["descripcion"] or "")[:50] if pd.notna(s["descripcion"]) else "",
        "rubro":       s["rubro"] if pd.notna(s["rubro"]) else "",
        "merma":       s["merma_total_valorizada"],
        "merma_u":     s["merma_total_unidades"],
        "neg":         s["merma_total_valorizada"] < 0,
        "venta":       s["venta_neta"],
        "pct":         s["pct_merma_sobre_ventas"],
    } for _, s in df_top.iterrows()]

    meta = dict(meta)
    meta["modo"] = _ETIQUETAS_MODO.get(meta.get("modo"), meta.get("modo"))

    _ETQ_NIVEL = {"rubro": "rubro", "super_rubro": "super rubro",
                 "gran_super_rubro": "gran super rubro"}
    return _render(
        "reporte_merma.html", meta.get("proyecto"), "Reporte de merma", meta,
        kpi=kpi, composicion=composicion, sucursales=sucursales,
        suc_total=suc_total, rubros=rubros,
        nivel_rubro=_ETQ_NIVEL.get(nivel, nivel),
        top_skus=top_skus, es_dinamico=es_dinamico, vista=vista,
    )


# ---------------------------------------------------------------------------
# 1b. Avance de inventario
# ---------------------------------------------------------------------------

_ETIQUETAS_NIVEL_AVANCE = {"rubro": "Rubro", "super_rubro": "Super Rubro",
                          "gran_super_rubro": "Gran Super Rubro"}
_ETIQUETAS_METODO_AVANCE = {"movimientos": "SKUs con movimiento en el período",
                           "catalogo": "Catálogo activo"}


def generar_reporte_avance_general(proyecto: str, meta: dict, df_matriz: pd.DataFrame) -> str:
    """
    Reporte de la matriz de avance de conteo (avance_matriz): una fila por
    nivel_valor, una columna por sucursal, con el % de avance en cada celda.
    """
    suc = df_matriz.attrs.get("sucursales", [])
    cols = [s["codigodepo"] for s in suc]

    filas = []
    valores_planos = []
    for _, r in df_matriz.iterrows():
        valores, barras = [], []
        for c in cols:
            v = r[c] if c in df_matriz.columns else None
            if pd.notna(v):
                v = float(v)
                valores_planos.append(v)
            valores.append(_pct(v) if pd.notna(v) else "—")
            barras.append(round(v, 1) if pd.notna(v) else 0.0)
        filas.append({"etiqueta": r["nivel_valor"], "valores": valores, "barras": barras})

    resumen = {
        "promedio": (sum(valores_planos) / len(valores_planos)) if valores_planos else None,
        "minimo":   min(valores_planos) if valores_planos else None,
        "maximo":   max(valores_planos) if valores_planos else None,
    }

    meta = dict(meta)
    meta["nivel"] = _ETIQUETAS_NIVEL_AVANCE.get(
        df_matriz.attrs.get("nivel"), df_matriz.attrs.get("nivel"))
    meta["metodo"] = _ETIQUETAS_METODO_AVANCE.get(
        df_matriz.attrs.get("metodo_denominador"), df_matriz.attrs.get("metodo_denominador"))

    return _render(
        "reporte_avance_general.html", proyecto, "Avance de inventario por sucursal", meta,
        filas=filas, sucursales=suc, nivel_label=meta["nivel"], resumen=resumen,
    )


def generar_reporte_avance_conteo(proyecto: str, meta: dict, df: pd.DataFrame) -> str:
    """
    Reporte de totales de conteo (avance_conteo): SKUs contados, unidades
    contadas y diferencia (unidades + valorizada), sin desagregar por
    sucursal — sea una o varias, el reporte solo totaliza.
    """
    kpi = {
        "skus_contados":         int(len(df)),
        "unidades_contadas":     float(df["unidades_contadas"].sum()) if not df.empty else 0.0,
        "diferencia_unidades":   float(df["diferencia_unidades"].sum()) if not df.empty else 0.0,
        "diferencia_valorizada": float(df["diferencia_valorizada"].sum()) if not df.empty else 0.0,
        "skus_con_faltante":     int((df["diferencia_unidades"] > 0).sum()) if not df.empty else 0,
        "skus_con_sobrante":     int((df["diferencia_unidades"] < 0).sum()) if not df.empty else 0,
    }
    meta = dict(meta)
    meta["modo"] = _ETIQUETAS_MODO.get(meta.get("modo"), meta.get("modo"))
    return _render(
        "reporte_avance_conteo.html", proyecto, "Avance de inventario — Conteo y diferencias",
        meta, kpi=kpi,
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
        e_max = float(evolucion["merma_total"].abs().max())
        for _, r in evolucion.iterrows():
            ev.append({
                "mes":     r["mes"],
                "merma":   r["merma_total"],
                "merma_u": r["merma_unidades"],
                "venta":   r["venta_neta"],
                "pct":     r["pct_merma_sobre_ventas"],
                "merma_w": _w(abs(r["merma_total"]), e_max),
                "neg":     r["merma_total"] < 0,
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
    meta["modo"] = _ETIQUETAS_MODO.get(meta.get("modo"), meta.get("modo"))
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


# ---------------------------------------------------------------------------
# 7. Diferencias de camión
# ---------------------------------------------------------------------------

def generar_reporte_diferencias(
    proyecto: str,
    meta: dict,
    det: pd.DataFrame,
    top_n: int = 25,
) -> str:
    """
    Reporte de las diferencias de camión (MD): faltantes contra sobrantes por
    sucursal, evolución del período y los SKUs que más pesan.

    Args:
        det:   detalle de core.distribucion.diferencias.diferencias_camion.
        top_n: cuántas filas listar en los rankings de sucursal y SKU.
    """
    from core.distribucion.diferencias import (
        resumen_diferencias, evolucion_diferencias, FALTANTE, SOBRANTE,
    )

    falt = det[det["sentido"] == FALTANTE] if not det.empty else det
    sobr = det[det["sentido"] == SOBRANTE] if not det.empty else det
    kpi = {
        "faltante_val": float(falt["valorizado"].sum()) if not det.empty else 0.0,
        "faltante_u":   float(falt["unidades"].sum()) if not det.empty else 0.0,
        "sobrante_val": float(sobr["valorizado"].sum()) if not det.empty else 0.0,
        "sobrante_u":   float(sobr["unidades"].sum()) if not det.empty else 0.0,
        "remitos":      int(det["numero"].nunique()) if not det.empty else 0,
        "skus":         int(det["codigo"].nunique()) if not det.empty else 0,
    }
    kpi["neto_val"] = kpi["faltante_val"] - kpi["sobrante_val"]
    kpi["neto_u"] = kpi["faltante_u"] - kpi["sobrante_u"]

    nombres = meta.get("nombres_sucursal", {})

    def _suc(c):
        n = nombres.get(c)
        return f"{c} — {n}" if n else str(c)

    sucursales, meses, skus = [], [], []
    if not det.empty:
        # --- por sucursal: la barra compara faltante contra sobrante ---------
        res = resumen_diferencias(det, por="sucursal").head(top_n)
        tope = float(pd.concat([res["faltante_val"], res["sobrante_val"]]).max())
        sucursales = [{
            "sucursal":     _suc(r["sucursal"]),
            "faltante_u":   r["faltante_u"],
            "faltante_val": r["faltante_val"],
            "faltante_w":   _w(r["faltante_val"], tope),
            "sobrante_u":   r["sobrante_u"],
            "sobrante_val": r["sobrante_val"],
            "sobrante_w":   _w(r["sobrante_val"], tope),
            "neto_val":     r["neto_val"],
            "remitos":      r["remitos"],
        } for _, r in res.iterrows()]

        ev = evolucion_diferencias(det, freq="M")
        meses = [{
            "periodo":      r["periodo"].strftime("%m/%Y"),
            "faltante_u":   r["faltante_u"],
            "faltante_val": r["faltante_val"],
            "sobrante_u":   r["sobrante_u"],
            "sobrante_val": r["sobrante_val"],
            "neto_val":     r["neto_val"],
            "remitos":      r["remitos"],
        } for _, r in ev.iterrows()]

        # --- SKUs que más pesan (por faltante valorizado) --------------------
        por_sku = resumen_diferencias(det, por="codigo").head(top_n)
        desc = (det.drop_duplicates("codigo").set_index("codigo")
                [["descripcion", "rubro"]])
        skus = [{
            "codigo":       r["codigo"],
            "descripcion":  str(desc["descripcion"].get(r["codigo"], "") or "")[:44],
            "rubro":        str(desc["rubro"].get(r["codigo"], "") or ""),
            "faltante_u":   r["faltante_u"],
            "faltante_val": r["faltante_val"],
            "sobrante_u":   r["sobrante_u"],
            "neto_val":     r["neto_val"],
        } for _, r in por_sku.iterrows()]

    meta = dict(meta)
    meta.pop("nombres_sucursal", None)
    return _render(
        "reporte_diferencias.html", proyecto, "Diferencias de camión", meta,
        kpi=kpi, sucursales=sucursales, meses=meses, skus=skus, top_n=top_n,
    )


# ---------------------------------------------------------------------------
# 8. Evolución interanual
# ---------------------------------------------------------------------------

def generar_reporte_interanual(
    proyecto: str,
    meta: dict,
    comp: pd.DataFrame,
    serie: pd.DataFrame,
    comp_sucursales: pd.DataFrame | None = None,
    vs_remitos: pd.DataFrame | None = None,
    top_n: int = 25,
) -> str:
    """
    Reporte de la comparación entre años: totales por año, mes a mes, ranking
    por la dimensión elegida y —si se pasan— el cruce entre sucursales y el
    de ventas contra reposición.

    Args:
        comp:            salida de comparar_anios (trae los attrs del análisis).
        serie:           salida de serie_mensual_anios.
        comp_sucursales: salida de comparar_anios con dimension="codigodepo",
                         para la sección de sucursales (None = no se incluye).
        vs_remitos:      salida de ventas_vs_remitos (None = no se incluye).
        top_n:           filas del ranking.
    """
    from core.inventario.interanual import METRICAS, DIMENSIONES, MESES
    from core.graficos import (
        svg_lineas, svg_barras_lineas, svg_barras_divergentes, colores,
    )

    a = comp.attrs
    anios = [str(x) for x in a["anios"]]
    es_pesos = METRICAS[a["metrica"]]["valorizada"]
    fmt = _pesos if es_pesos else _unidades
    nombres = meta.get("nombres_sucursal", {})
    cortos = meta.get("nombres_cortos", {})

    def _suc(c):
        n = nombres.get(c)
        return f"{c} — {n}" if n else str(c)

    def _suc_corto(c):
        """En el gráfico entra el nombre corto; el código queda en la tabla."""
        return cortos.get(c) or nombres.get(c) or str(c)

    def _variacion(base: float, ult: float) -> float | None:
        return (ult - base) / abs(base) * 100 if base else None

    # --- totales por año ------------------------------------------------------
    totales = []
    previo = None
    for anio in anios:
        v = float(a["total_por_anio"].get(int(anio), 0.0))
        totales.append({"anio": anio, "valor": fmt(v),
                        "variacion": _variacion(previo, v) if previo else None})
        previo = v

    # --- mes a mes (tabla + gráfico de líneas, uno por año) -------------------
    meses, graf_meses = [], ""
    if not serie.empty:
        piv = serie.pivot_table(index="mes", columns="anio", values="valor",
                                aggfunc="sum").fillna(0.0)
        tope = float(piv.abs().to_numpy().max()) if piv.size else 0.0
        for mes, fila in piv.sort_index().iterrows():
            vals = [float(fila.get(int(x), 0.0)) for x in anios]
            meses.append({
                "etiqueta": MESES.get(int(mes), str(mes)),
                "valores":  [fmt(v) for v in vals],
                "barras":   [_w(abs(v), tope) for v in vals],
                "var_abs":  None,
                "var_pct":  _variacion(vals[0], vals[-1]),
            })
        orden = sorted(piv.index)
        graf_meses = svg_lineas(
            [MESES.get(int(m), str(m)) for m in orden],
            [{"nombre": x,
              "valores": [float(piv.loc[m].get(int(x), 0.0)) for m in orden]}
             for x in anios],
            titulo_y=METRICAS[a["metrica"]]["etiqueta"])

    def _filas(df: pd.DataFrame, etiquetar=None) -> list:
        if df is None or df.empty:
            return []
        tope = float(df[anios].abs().to_numpy().max()) if len(df) else 0.0
        out = []
        for _, r in df.iterrows():
            vals = [float(r[x]) for x in anios]
            out.append({
                "etiqueta":  etiquetar(r) if etiquetar else str(r["etiqueta"])[:48],
                "valores":   [fmt(v) for v in vals],
                "barras":    [_w(abs(v), tope) for v in vals],
                "var_abs":   fmt(float(r["variacion_abs"])),
                "var_pct":   (float(r["variacion_pct"])
                              if pd.notna(r["variacion_pct"]) else None),
            })
        return out

    ranking = _filas(comp.head(top_n))

    # --- sucursales: tabla + barras divergentes de la variación ---------------
    sucursales, graf_sucursales = [], ""
    if comp_sucursales is not None and not comp_sucursales.empty:
        sucursales = _filas(comp_sucursales,
                            etiquetar=lambda r: _suc(r["codigodepo"]))
        orden = comp_sucursales.sort_values("variacion_abs", ascending=False)
        graf_sucursales = svg_barras_divergentes([{
            "etiqueta": _suc_corto(r["codigodepo"]),
            "valor":    float(r["variacion_abs"]),
            "texto":    (f"{r['variacion_pct']:+.1f}%"
                         if pd.notna(r["variacion_pct"]) else fmt(r["variacion_abs"])),
        } for _, r in orden.iterrows()])

    # --- ventas contra reposición ---------------------------------------------
    remitos = None
    if vs_remitos is not None and not vs_remitos.empty:
        vr = vs_remitos
        fmt_vr = _pesos if vr.attrs.get("valorizado") else _unidades
        piv = vr.pivot_table(index="mes", columns="anio",
                             values=["ventas", "remitos"], aggfunc="sum").fillna(0.0)
        tope_v = float(piv["ventas"].to_numpy().max()) if piv.size else 0.0
        tope_r = float(piv["remitos"].to_numpy().max()) if piv.size else 0.0
        filas_vr = []
        for mes in sorted(vr["mes"].unique()):
            fila = {"etiqueta": MESES.get(int(mes), str(mes)), "anios": []}
            for x in a["anios"]:
                d = vr[(vr["mes"] == mes) & (vr["anio"] == x)]
                v = float(d["ventas"].sum())
                r = float(d["remitos"].sum())
                fila["anios"].append({
                    "ventas": fmt_vr(v), "ventas_w": _w(v, tope_v),
                    "remitos": fmt_vr(r), "remitos_w": _w(r, tope_r),
                    "cobertura": f"{r / v:,.2f}" if v else "—",
                })
            filas_vr.append(fila)

        orden_m = sorted(vr["mes"].unique())
        etq_m = [MESES.get(int(m), str(m)) for m in orden_m]

        def _serie_vr(col, anio):
            return [float(vr[(vr["mes"] == m) & (vr["anio"] == anio)][col].sum())
                    for m in orden_m]

        pal = colores(len(a["anios"]))
        graf_vr = svg_barras_lineas(
            etq_m,
            [{"nombre": f"Ventas {x}", "valores": _serie_vr("ventas", x),
              "color": pal[i]} for i, x in enumerate(a["anios"])],
            [{"nombre": f"Remitos {x}", "valores": _serie_vr("remitos", x),
              "color": pal[i]} for i, x in enumerate(a["anios"])],
            titulo_izq="Ventas", titulo_der="Remitos")

        var = vr.attrs["variaciones"]
        remitos = {
            "grafico":   graf_vr,
            "filas":     filas_vr,
            "var_venta": var["ventas"],
            "var_remito": var["remitos"],
            "brecha":    vr.attrs["brecha"],
            "excluidos": ", ".join(vr.attrs.get("excluidos") or []),
            "totales":   [{"anio": str(x),
                           "ventas": fmt_vr(vr.attrs["totales"]["ventas"].get(x, 0.0)),
                           "remitos": fmt_vr(vr.attrs["totales"]["remitos"].get(x, 0.0))}
                          for x in a["anios"]],
        }

    meta = dict(meta)
    meta.pop("nombres_sucursal", None)
    meta.setdefault("metrica", METRICAS[a["metrica"]]["etiqueta"])
    meta.setdefault("meses", ", ".join(
        MESES[m] for m in (a["meses"] or range(1, 13))))
    meta.setdefault("anios", " · ".join(anios))
    if es_pesos:
        meta.setdefault("valorizacion",
                        f"{a['modo_valorizacion']} — snapshot {a['fecha_valorizacion']}")
    if a.get("dia_corte"):
        meta.setdefault("dia_corte", a["dia_corte"])
        if a.get("meses_dia_corte"):
            meta.setdefault("meses_dia_corte", ", ".join(
                MESES[m] for m in a["meses_dia_corte"]))

    return _render(
        "reporte_interanual.html", proyecto, "Evolución interanual", meta,
        anios=anios, totales=totales, meses=meses, ranking=ranking,
        sucursales=sucursales, remitos=remitos,
        graf_meses=graf_meses, graf_sucursales=graf_sucursales,
        dimension=DIMENSIONES[a["dimension"]], es_pesos=es_pesos,
    )


# ---------------------------------------------------------------------------
# 9. Cruce por sucursal
# ---------------------------------------------------------------------------

def generar_reporte_cruce(
    proyecto: str,
    meta: dict,
    agg: pd.DataFrame,
    top_n: int = 25,
) -> str:
    """
    Reporte del cruce de stock por sucursal: evolución del período (stock
    inicial vs. final) para el recorte de catálogo elegido.

    Args:
        agg: salida de core.inventario.series.stock_por_sucursal (fecha,
             codigodepo, stock, skus_en_quiebre, skus).
        meta: además de los campos usuales, espera "skus" (SKUs con
              movimiento en la serie) y "nombres_sucursal" (dict código→nombre).
    """
    nombres = meta.get("nombres_sucursal", {})

    def _suc(c):
        n = nombres.get(c)
        return f"{c} — {n}" if n else str(c)

    kpi = {"sucursales": 0, "skus": int(meta.get("skus", 0)),
           "stock_inicial": 0.0, "stock_final": 0.0, "variacion_pct": None}

    sucursales = []
    if not agg.empty:
        g = agg.sort_values("fecha").groupby("codigodepo")
        res = pd.DataFrame({
            "stock_inicial": g["stock"].first(),
            "stock_final":   g["stock"].last(),
            "quiebre_prom":  g["skus_en_quiebre"].mean(),
            "skus":          g["skus"].max(),
        }).reset_index()
        res["variacion_pct"] = ((res["stock_final"] - res["stock_inicial"])
                                / res["stock_inicial"].replace(0, pd.NA) * 100)

        kpi["sucursales"] = int(res["codigodepo"].nunique())
        kpi["stock_inicial"] = float(res["stock_inicial"].sum())
        kpi["stock_final"] = float(res["stock_final"].sum())
        kpi["variacion_pct"] = (
            (kpi["stock_final"] - kpi["stock_inicial"]) / kpi["stock_inicial"] * 100
            if kpi["stock_inicial"] else None
        )

        tope = float(res["stock_final"].abs().max())
        sucursales = [{
            "sucursal":      _suc(r["codigodepo"]),
            "skus":          int(r["skus"]),
            "stock_inicial": r["stock_inicial"],
            "stock_final":   r["stock_final"],
            "stock_w":       _w(abs(r["stock_final"]), tope),
            "variacion_pct": None if pd.isna(r["variacion_pct"]) else float(r["variacion_pct"]),
            "quiebre_prom":  r["quiebre_prom"],
        } for _, r in res.sort_values("stock_final", ascending=False).head(top_n).iterrows()]

    return _render(
        "reporte_cruce.html", proyecto, "Cruce por sucursal", meta,
        kpi=kpi, sucursales=sucursales,
    )


# ---------------------------------------------------------------------------
# 10. Serie de stock y quiebres
# ---------------------------------------------------------------------------

def generar_reporte_series(
    proyecto: str,
    meta: dict,
    resumen: pd.DataFrame,
    top_n: int = 25,
) -> str:
    """
    Reporte de la serie de stock reconstruida: quiebres por SKU para la
    sucursal y el período elegidos.

    Args:
        resumen: salida de core.inventario.series.resumen_quiebres.
    """
    kpi = {
        "skus":              len(resumen),
        "con_quiebre":       int((resumen["dias_quiebre"] > 0).sum()) if not resumen.empty else 0,
        "dias_quiebre_prom": (float(resumen["dias_quiebre"].mean())
                              if not resumen.empty else None),
        "racha_max":         (float(resumen["racha_quiebre"].max())
                              if not resumen.empty else None),
        "negativos":         int((resumen["stock_min"] < 0).sum()) if not resumen.empty else 0,
    }

    quiebres = []
    if not resumen.empty:
        top = resumen[resumen["dias_quiebre"] > 0].nlargest(top_n, "dias_quiebre")
        tope = float(top["pct_quiebre"].max()) if len(top) else 0.0
        quiebres = [{
            "codigo":       r["codigo"],
            "descripcion":  (r["descripcion"] or "")[:42] if pd.notna(r["descripcion"]) else "",
            "rubro":        r["rubro"] if pd.notna(r["rubro"]) else "",
            "dias_quiebre": r["dias_quiebre"],
            "pct_quiebre":  r["pct_quiebre"],
            "pct_w":        _w(float(r["pct_quiebre"] or 0), tope),
            "racha":        r["racha_quiebre"],
            "stock_final":  r["stock_final"],
            "movimientos":  r["movimientos"],
        } for _, r in top.iterrows()]

    return _render(
        "reporte_series.html", proyecto, "Serie de stock y quiebres", meta,
        kpi=kpi, quiebres=quiebres,
    )


# ---------------------------------------------------------------------------
# 11. Perfil de reposición
# ---------------------------------------------------------------------------

def generar_reporte_reposicion(
    proyecto: str,
    meta: dict,
    perfil: pd.DataFrame,
    top_n: int = 25,
) -> str:
    """
    Reporte del perfil de reposición: cadencia de remitos, ventas y días
    hasta cruzar la barrera elegida contando desde el último remito, por SKU.

    Args:
        perfil: salida de core.inventario.reposicion.perfil_reposicion.
    """
    from core.inventario.reposicion import resumen_reposicion
    from core.graficos import svg_ranking_horizontal

    kpi = resumen_reposicion(perfil)

    urgentes = (perfil[perfil["quebro_barrera"] == True]  # noqa: E712
                .nsmallest(top_n, "dias_hasta_quiebre_barrera"))
    tope = float(urgentes["dias_hasta_quiebre_barrera"].max()) if len(urgentes) else 0.0
    ranking = [{
        "codigo":       r["codigo"],
        "descripcion":  (r["descripcion"] or "")[:42] if pd.notna(r["descripcion"]) else "",
        "rubro":        r["rubro"] if pd.notna(r["rubro"]) else "",
        "dias":         r["dias_hasta_quiebre_barrera"],
        "dias_w":       _w(float(r["dias_hasta_quiebre_barrera"]), tope),
        "cadencia":     r["cadencia_dias"],
        "ventas_promedio": r["ventas_promedio"],
    } for _, r in urgentes.iterrows()]
    graf_ranking = svg_ranking_horizontal([
        {"etiqueta": f"{r['codigo']} — {str(r['descripcion'] or '')[:28]}",
         "valor": float(r["dias_hasta_quiebre_barrera"]),
         "texto": f"{r['dias_hasta_quiebre_barrera']:.0f} d"}
        for _, r in urgentes.iterrows()
    ])

    return _render(
        "reporte_reposicion.html", proyecto, "Perfil de reposición", meta,
        kpi=kpi, ranking=ranking, graf_ranking=graf_ranking,
    )


# ---------------------------------------------------------------------------
# 12. Comprobantes y canasta
# ---------------------------------------------------------------------------

def generar_reporte_comprobantes(
    proyecto: str,
    meta: dict,
    resumen: pd.DataFrame,
    evolucion: pd.DataFrame | None = None,
    evolucion_por_sucursal: pd.DataFrame | None = None,
    afinidad: pd.DataFrame | None = None,
    top_n: int = 25,
) -> str:
    """
    Reporte de comprobantes: comparativa por sucursal y por tipo de
    comprobante, evolución mensual de las 4 métricas (si se pasa) y afinidad
    entre productos / canasta (si se pasa).

    Args:
        resumen:   salida de core.comercial.comprobantes.resumen_comprobantes
                   (una fila por sucursal × tipo de comprobante).
        evolucion: "combinado" de evolucion_comprobantes_multi (largo, con
                   columna `metrica`), o None para no incluir la sección.
        evolucion_por_sucursal: "por_sucursal" de evolucion_comprobantes_multi
                   — si se pasa, cada métrica trae además un gráfico por
                   sucursal debajo del combinado.
        afinidad:  salida de afinidad_skus, o None para no incluirla.
        top_n:     filas de los rankings.
    """
    from core.comercial.comprobantes import METRICAS_COMPROBANTE
    from core.graficos import svg_lineas, svg_ranking_horizontal, PALETA_CATEGORICA

    nombres = meta.get("nombres_sucursal", {})

    def _suc(c):
        n = nombres.get(c)
        return f"{c} — {n}" if n else str(c)

    kpi = {"sucursales": 0, "tipos": 0, "comprobantes": 0, "ticket_promedio": None}
    por_sucursal, graf_sucursal, por_tipo = [], "", []

    if not resumen.empty:
        kpi["sucursales"] = int(resumen["codigodepo"].nunique())
        kpi["tipos"] = int(resumen["tipo"].nunique())
        kpi["comprobantes"] = int(resumen["cantidad_comprobantes"].sum())
        total_valor = float(resumen["valor_total"].sum())
        kpi["ticket_promedio"] = (total_valor / kpi["comprobantes"]
                                  if kpi["comprobantes"] else None)

        # Ponderado por cantidad_comprobantes: promediar promedios sin pesar
        # subestimaría los tipos/sucursales con más volumen.
        r = resumen.copy()
        r["referencias_x_cant"] = r["referencias_prom"] * r["cantidad_comprobantes"]
        r["unidades_x_cant"] = r["unidades_prom"] * r["cantidad_comprobantes"]

        agg_suc = r.groupby("codigodepo").agg(
            cantidad_comprobantes=("cantidad_comprobantes", "sum"),
            referencias_x_cant=("referencias_x_cant", "sum"),
            unidades_x_cant=("unidades_x_cant", "sum"),
            valor_total=("valor_total", "sum"),
        ).reset_index()
        agg_suc["referencias_prom"] = agg_suc["referencias_x_cant"] / agg_suc["cantidad_comprobantes"]
        agg_suc["unidades_prom"] = agg_suc["unidades_x_cant"] / agg_suc["cantidad_comprobantes"]
        agg_suc["valor_prom"] = agg_suc["valor_total"] / agg_suc["cantidad_comprobantes"]
        agg_suc = agg_suc.sort_values("cantidad_comprobantes", ascending=False).head(top_n)

        tope = float(agg_suc["cantidad_comprobantes"].max()) if len(agg_suc) else 0.0
        for _, row in agg_suc.iterrows():
            por_sucursal.append({
                "sucursal":    _suc(row["codigodepo"]),
                "cantidad":    _unidades(row["cantidad_comprobantes"]),
                "cantidad_w":  _w(row["cantidad_comprobantes"], tope),
                "referencias": f"{row['referencias_prom']:.1f}",
                "unidades":    f"{row['unidades_prom']:.1f}",
                "ticket":      _pesos(row["valor_prom"]),
            })
        graf_sucursal = svg_ranking_horizontal([{
            "etiqueta": _suc(row["codigodepo"])[:34],
            "valor":    float(row["cantidad_comprobantes"]),
            "texto":    f"{row['cantidad_comprobantes']:,.0f}",
        } for _, row in agg_suc.iterrows()])

        agg_tipo = r.groupby(["tipo", "categoria"]).agg(
            cantidad_comprobantes=("cantidad_comprobantes", "sum"),
            referencias_x_cant=("referencias_x_cant", "sum"),
            unidades_x_cant=("unidades_x_cant", "sum"),
            valor_total=("valor_total", "sum"),
        ).reset_index()
        agg_tipo["referencias_prom"] = agg_tipo["referencias_x_cant"] / agg_tipo["cantidad_comprobantes"]
        agg_tipo["unidades_prom"] = agg_tipo["unidades_x_cant"] / agg_tipo["cantidad_comprobantes"]
        agg_tipo["valor_prom"] = agg_tipo["valor_total"] / agg_tipo["cantidad_comprobantes"]
        agg_tipo = agg_tipo.sort_values("cantidad_comprobantes", ascending=False)
        for _, row in agg_tipo.iterrows():
            por_tipo.append({
                "tipo":        row["tipo"],
                "categoria":   row["categoria"],
                "cantidad":    _unidades(row["cantidad_comprobantes"]),
                "referencias": f"{row['referencias_prom']:.1f}",
                "unidades":    f"{row['unidades_prom']:.1f}",
                "ticket":      _pesos(row["valor_prom"]),
            })

    evolucion_secciones = []
    if evolucion is not None and not evolucion.empty:
        from core.comun import MESES

        def _serie_svg(d: pd.DataFrame, titulo_y: str) -> str:
            piv = d.pivot_table(index="mes", columns="anio", values="valor",
                                aggfunc="sum").fillna(0.0)
            orden = sorted(piv.index)
            return svg_lineas(
                [MESES.get(int(m), str(m)) for m in orden],
                [{"nombre": str(a), "valores": [float(piv.loc[m].get(a, 0.0)) for m in orden]}
                 for a in piv.columns],
                titulo_y=titulo_y)

        for metrica, info in METRICAS_COMPROBANTE.items():
            d = evolucion[evolucion["metrica"] == metrica]
            if d.empty:
                continue
            graf_desagrupado = ""
            graf_por_sucursal = []
            if evolucion_por_sucursal is not None and not evolucion_por_sucursal.empty:
                d_suc = evolucion_por_sucursal[evolucion_por_sucursal["metrica"] == metrica]
                cods = sorted(d_suc["codigodepo"].unique())
                anios_suc = sorted(d_suc["anio"].unique())
                anio_reciente = anios_suc[-1] if anios_suc else None

                # Desagrupado: todas las sucursales en un solo gráfico, un
                # color fijo por sucursal y línea punteada para los años
                # viejos (solo el más reciente va sólido) — mismo criterio
                # que el "Abrir por sucursal" interactivo de la pantalla.
                piv_d = d_suc.pivot_table(index="mes", columns=["codigodepo", "anio"],
                                          values="valor", aggfunc="sum").fillna(0.0)
                orden_d = sorted(piv_d.index)
                series_d = []
                for i, cod in enumerate(cods):
                    color = PALETA_CATEGORICA[i % len(PALETA_CATEGORICA)]
                    for anio in anios_suc:
                        if (cod, anio) not in piv_d.columns:
                            continue
                        series_d.append({
                            "nombre": f"{_suc(cod)} · {anio}",
                            "valores": [float(piv_d.loc[m, (cod, anio)]) for m in orden_d],
                            "color": color,
                            "dash": anio != anio_reciente,
                        })
                graf_desagrupado = svg_lineas(
                    [MESES.get(int(m), str(m)) for m in orden_d], series_d,
                    titulo_y=info["etiqueta"])

                for cod in cods:
                    graf_por_sucursal.append({
                        "sucursal": _suc(cod),
                        "svg": _serie_svg(d_suc[d_suc["codigodepo"] == cod], info["etiqueta"]),
                    })
            evolucion_secciones.append({
                "titulo": info["etiqueta"],
                "combinado": _serie_svg(d, info["etiqueta"]),
                "desagrupado": graf_desagrupado,
                "por_sucursal": graf_por_sucursal,
            })

    canasta, graf_canasta = [], ""
    if afinidad is not None and not afinidad.empty:
        top_af = afinidad.head(top_n)
        for _, row in top_af.iterrows():
            canasta.append({
                "par": (f"{str(row['descripcion_a'] or row['codigo_a'])[:28]} + "
                       f"{str(row['descripcion_b'] or row['codigo_b'])[:28]}"),
                "comprobantes": _unidades(row["comprobantes_conjuntos"]),
                "soporte":      f"{row['soporte']:.2f}%",
                "lift":         f"{row['lift']:.2f}",
            })
        graf_canasta = svg_ranking_horizontal([{
            "etiqueta": (f"{str(row['descripcion_a'] or row['codigo_a'])[:20]} + "
                        f"{str(row['descripcion_b'] or row['codigo_b'])[:20]}"),
            "valor":    float(row["comprobantes_conjuntos"]),
            "texto":    f"{row['comprobantes_conjuntos']:,.0f}",
        } for _, row in top_af.iterrows()])

    return _render(
        "reporte_comprobantes.html", proyecto, "Comprobantes y canasta", meta,
        kpi=kpi, por_sucursal=por_sucursal, graf_sucursal=graf_sucursal,
        por_tipo=por_tipo, evolucion_secciones=evolucion_secciones,
        canasta=canasta, graf_canasta=graf_canasta,
    )


# ---------------------------------------------------------------------------
# 13. Pedido desde sucursal
# ---------------------------------------------------------------------------

def _fecha_o_guion(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return str(pd.to_datetime(v).date())


def generar_reporte_pedido_sucursal(
    proyecto: str,
    meta: dict,
    perfil: pd.DataFrame,
) -> str:
    """
    Hoja de pedido imprimible (apaisada) para una sucursal: una fila por
    artículo del recorte filtrado, con los datos para decidir cuánto pedir y
    una columna en blanco para que el repositor anote la cantidad a mano.

    Args:
        perfil: salida de core.inventario.reposicion.perfil_reposicion, con
                `ean` y `pedido_recomendado` ya mergeados (ver
                ui.paginas.inventario_pedido_sucursal). Se ordena acá por
                pedido_recomendado descendente — lo que más conviene pedir
                primero, arriba.
    """
    perfil = perfil.sort_values("pedido_recomendado", ascending=False)
    filas = [{
        "codigo":       r["codigo"],
        "descripcion":  r["descripcion"] if pd.notna(r["descripcion"]) else "",
        "rubro":        r["rubro"] if pd.notna(r["rubro"]) else "",
        "marca":        r["marca"] if pd.notna(r["marca"]) else "",
        "ean":          r["ean"] if pd.notna(r.get("ean")) else "",
        "uxb":          _unidades(r["uxb"]),
        "venta_ajustada": _unidades(r["ventas_promedio_ajustado"]),
        "dias_con_stock": _unidades(r["dias_con_stock"]),
        "dias_quiebre": _unidades(r["dias_quiebre"]),
        "stock_sucursal": _unidades(r["stock_sucursal"]),
        "stock_cd_001": _unidades(r.get("stock_cd_001")),
        "ultimo_remito": _fecha_o_guion(r["fecha_ultimo_remito"]),
        "ultima_venta": _fecha_o_guion(r["fecha_ultima_venta"]),
        "pedido":       _unidades(max(0.0, r["pedido_recomendado"]))
                        if pd.notna(r["pedido_recomendado"]) else "—",
    } for _, r in perfil.iterrows()]

    return _render(
        "reporte_pedido_sucursal.html", proyecto, "Pedido desde sucursal", meta,
        filas=filas,
    )


# ---------------------------------------------------------------------------
# 14. Transformaciones
# ---------------------------------------------------------------------------

def generar_reporte_transformaciones(
    proyecto: str,
    meta: dict,
    detalle: pd.DataFrame,
    pares: pd.DataFrame,
    top_n: int = 25,
) -> str:
    """
    Reporte de transformaciones (tipo='TR'): comparativa por sucursal, top
    SKUs dados de baja y top SKUs de liquidación, y el detalle de pares
    baja -> alta por comprobante.

    Args:
        detalle: salida de core.merma.transformaciones.detalle_transformaciones.
        pares:   salida de core.merma.transformaciones.pares_transformacion
                 (mismo recorte que `detalle`).
        top_n:   filas de los rankings y de la tabla de pares.
    """
    from core.graficos import svg_ranking_horizontal

    nombres = meta.get("nombres_sucursal", {})

    def _suc(c):
        n = nombres.get(c)
        return f"{c} — {n}" if n else str(c)

    kpi = {"sucursales": 0, "comprobantes": 0, "unidades_baja": 0.0,
           "unidades_alta": 0.0, "valor_baja": 0.0, "valor_alta": 0.0,
           "neto_valor": 0.0}
    por_sucursal, graf_sucursal = [], ""
    top_baja, graf_baja = [], ""
    top_alta, graf_alta = [], ""
    filas_pares = []

    if not detalle.empty:
        baja = detalle[detalle["sentido"] == "Baja"]
        alta = detalle[detalle["sentido"] == "Alta"]
        kpi["sucursales"] = int(detalle["codigodepo"].nunique())
        kpi["comprobantes"] = int(detalle.drop_duplicates(["numero", "codigodepo"]).shape[0])
        kpi["unidades_baja"] = float(baja["unidades"].sum())
        kpi["unidades_alta"] = float(alta["unidades"].sum())
        kpi["valor_baja"] = float(baja["valor"].sum())
        kpi["valor_alta"] = float(alta["valor"].sum())
        kpi["neto_valor"] = kpi["valor_alta"] - kpi["valor_baja"]

        agg_suc = detalle.groupby(["codigodepo", "sentido"]).agg(
            unidades=("unidades", "sum"), valor=("valor", "sum"),
        ).unstack("sentido", fill_value=0.0)
        agg_suc.columns = [f"{c[0]}_{c[1].lower()}" for c in agg_suc.columns]
        for c in ("unidades_baja", "valor_baja", "unidades_alta", "valor_alta"):
            if c not in agg_suc.columns:
                agg_suc[c] = 0.0
        agg_suc = agg_suc.reset_index().sort_values("valor_baja", ascending=False).head(top_n)

        tope = float(agg_suc["valor_baja"].max()) if len(agg_suc) else 0.0
        for _, row in agg_suc.iterrows():
            por_sucursal.append({
                "sucursal":      _suc(row["codigodepo"]),
                "unidades_baja": _unidades(row["unidades_baja"]),
                "valor_baja":    _pesos(row["valor_baja"]),
                "valor_baja_w":  _w(row["valor_baja"], tope),
                "unidades_alta": _unidades(row["unidades_alta"]),
                "valor_alta":    _pesos(row["valor_alta"]),
            })
        graf_sucursal = svg_ranking_horizontal([{
            "etiqueta": _suc(row["codigodepo"])[:34],
            "valor":    float(row["valor_baja"]),
            "texto":    _pesos(row["valor_baja"]),
        } for _, row in agg_suc.iterrows()])

        def _top_sku(d: pd.DataFrame):
            g = (d.groupby(["codigo", "descripcion"], dropna=False).agg(
                    unidades=("unidades", "sum"), valor=("valor", "sum"))
                 .reset_index().sort_values("valor", ascending=False).head(top_n))
            filas = [{
                "codigo":      r["codigo"],
                "descripcion": r["descripcion"] if pd.notna(r["descripcion"]) else "",
                "unidades":    _unidades(r["unidades"]),
                "valor":       _pesos(r["valor"]),
            } for _, r in g.iterrows()]
            graf = svg_ranking_horizontal([{
                "etiqueta": f"{r['codigo']} — {str(r['descripcion'] or '')[:24]}",
                "valor":    float(r["valor"]),
                "texto":    _pesos(r["valor"]),
            } for _, r in g.iterrows()])
            return filas, graf

        top_baja, graf_baja = _top_sku(baja)
        top_alta, graf_alta = _top_sku(alta)

    if not pares.empty:
        for _, r in pares.head(top_n).iterrows():
            filas_pares.append({
                "numero":     r["numero"],
                "sucursal":   _suc(r["codigodepo"]),
                "fecha":      _fecha_o_guion(r["fecha"]),
                "skus_baja":  r["skus_baja"],
                "skus_alta":  r["skus_alta"],
                "valor_baja": _pesos(r["valor_baja"]),
                "valor_alta": _pesos(r["valor_alta"]),
                "neto_valor": _pesos(r["neto_valor"]),
            })

    return _render(
        "reporte_transformaciones.html", proyecto, "Transformaciones", meta,
        kpi=kpi, por_sucursal=por_sucursal, graf_sucursal=graf_sucursal,
        top_baja=top_baja, graf_baja=graf_baja,
        top_alta=top_alta, graf_alta=graf_alta,
        pares=filas_pares,
    )
