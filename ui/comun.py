"""
ui/comun.py — Utilidades y fachada de la UI

CSS, paleta, helpers de formato/descarga/controles y re-exportación de las
funciones de core que usan las páginas. Cada página hace `from ui.comun import *`.
"""
import io
import sys
from pathlib import Path
from datetime import date

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_db import get_db_path, get_connection
from core.comun import listar_categorias, listar_fechas_valorizacion
from core.merma.analisis import calcular_merma, merma_por_sucursal
from core.merma.control import evolucion_mensual, movimientos_outliers, ajustes_por_usuario
from core.inventario.salud import analizar_stock, resumen_salud, ESTADOS
from core.inventario.politicas import calcular_politicas, resumen_politicas, ESTADOS_POLITICA
from core.distribucion.transferencias import sugerir_transferencias
from core.comercial.margenes import analizar_margenes, evolucion_costos
from core.forecasting.demanda import forecast_ventas, serie_mensual_real, NIVELES, METRICAS
from core.reporte import (
    generar_reporte, generar_reporte_control, generar_reporte_salud,
    generar_reporte_politicas, generar_reporte_forecast, generar_reporte_transferencias,
    cargar_branding, guardar_branding,
)

# Paleta para categorías en los gráficos
PALETA = ["#ff6b6b", "#ffa94d", "#748ffc", "#64ffda", "#f783ac", "#a9e34b", "#ffd43b"]

# Layout oscuro reutilizado en los gráficos
LAYOUT_OSCURO = dict(
    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)

# CSS (se aplica desde app.py con st.markdown(CSS, unsafe_allow_html=True))
CSS = """
<style>
    /* Fuente principal */
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f1117;
        border-right: 1px solid #1e2130;
    }
    [data-testid="stSidebar"] * {
        color: #e0e0e0 !important;
    }

    /* Métricas */
    [data-testid="stMetric"] {
        background: #1e2130;
        border: 1px solid #2d3148;
        border-radius: 8px;
        padding: 16px;
    }
    [data-testid="stMetricLabel"] {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #8892b0 !important;
    }
    [data-testid="stMetricValue"] {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 1.6rem;
        color: #ccd6f6 !important;
    }

    /* Encabezados */
    h1, h2, h3 {
        font-family: 'IBM Plex Mono', monospace;
        letter-spacing: -0.02em;
    }

    /* Tablas */
    [data-testid="stDataFrame"] {
        border: 1px solid #1e2130;
        border-radius: 8px;
    }

    /* Botones */
    .stButton > button {
        background: #1e2130;
        border: 1px solid #2d3148;
        color: #ccd6f6;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.8rem;
        letter-spacing: 0.05em;
        border-radius: 4px;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        background: #2d3148;
        border-color: #64ffda;
        color: #64ffda;
    }

    /* Selectbox */
    .stSelectbox label {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.75rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #8892b0;
    }

    /* Tag de merma alta */
    .merma-alta { color: #ff6b6b; font-weight: 600; }
    .merma-media { color: #ffa94d; }
    .merma-baja { color: #64ffda; }
</style>
"""


def listar_proyectos() -> list[str]:
    projects_dir = ROOT / "projects"
    if not projects_dir.exists():
        return []
    return sorted([
        p.name for p in projects_dir.iterdir()
        if p.is_dir() and (p / "data.duckdb").exists()
    ])


def listar_sucursales(proyecto: str) -> pd.DataFrame:
    """Sucursales con movimientos, con nombre si existe."""
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT DISTINCT m.codigodepo, d.nombre
        FROM movimientos m
        LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
        WHERE m.codigodepo IS NOT NULL
        ORDER BY m.codigodepo
    """).df()
    conn.close()
    return df


def rango_disponible(proyecto: str) -> tuple[date | None, date | None]:
    """Rango de fechas con movimientos."""
    conn = get_connection(proyecto)
    rm = conn.execute("SELECT MIN(fecha), MAX(fecha) FROM movimientos").fetchone()
    conn.close()
    return rm[0], rm[1]


def categorias_merma(proyecto: str) -> list[str]:
    """Categorías marcadas es_merma, en orden de catálogo."""
    conn = get_connection(proyecto)
    rows = conn.execute("""
        SELECT categoria FROM tipos_categoria
        WHERE es_merma GROUP BY categoria ORDER BY MIN(orden), categoria
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def listar_periodos_ingesta(proyecto: str) -> pd.DataFrame:
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT tabla, periodo, archivo_origen, estado,
               registros_cargados, fecha_ingesta
        FROM periodos_ingesta
        ORDER BY fecha_ingesta DESC
    """).df()
    conn.close()
    return df


def formatear_pesos(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"$ {valor:,.0f}"


def formatear_pct(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"{valor:.2f}%"


def formatear_unidades(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"{valor:,.0f}"


def merma_por_categoria(df: pd.DataFrame, cat: str) -> pd.Series:
    """Magnitud de merma (parte negativa, en positivo) de una columna-categoría."""
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return (-df[cat]).clip(lower=0)


def a_excel(df: pd.DataFrame) -> bytes:
    """Serializa un DataFrame a .xlsx en memoria."""
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def botones_descarga(df: pd.DataFrame, nombre: str, key: str) -> None:
    """Botones Excel + CSV para descargar un listado."""
    c1, c2, _ = st.columns([1, 1, 4])
    c1.download_button(
        "⬇ Excel", a_excel(df), f"{nombre}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key}_xlsx",
    )
    c2.download_button(
        "⬇ CSV", df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        f"{nombre}.csv", "text/csv", key=f"{key}_csv",
    )


@st.cache_data(show_spinner=False)
def comparativa_cacheada(proyecto: str, fecha_desde: str, fecha_hasta: str,
                         modo_valorizacion: str, fecha_valorizacion,
                         codigos: tuple | None) -> pd.DataFrame:
    return merma_por_sucursal(
        proyecto, fecha_desde, fecha_hasta,
        modo_valorizacion=modo_valorizacion,
        fecha_valorizacion=fecha_valorizacion,
        codigos=list(codigos) if codigos is not None else None,
    )


def render_comparativa(df_suc: pd.DataFrame, merma_cats: list[str], key: str) -> None:
    """Tabla + gráfico + descarga de la comparativa por sucursal."""
    if df_suc.empty:
        st.info("Sin datos para la comparativa.")
        return

    df_suc = df_suc.copy()
    df_suc["sucursal"] = df_suc["codigodepo"] + " — " + df_suc["nombre"].fillna("")

    # Tabla
    cols = ["codigodepo", "nombre", "skus", "skus_con_merma"]
    headers = ["Código", "Sucursal", "SKUs", "SKUs c/merma"]
    formato = {}
    for c in merma_cats:
        if c in df_suc.columns:
            cols.append(c); headers.append(f"{c} $"); formato[c] = formatear_pesos
            cu = f"{c} (u)"
            cols.append(cu); headers.append(f"{c} u."); formato[cu] = formatear_unidades
    for c, h, f in [
        ("merma_total_valorizada", "Merma Total $", formatear_pesos),
        ("merma_total_unidades",   "Merma Total u.", formatear_unidades),
        ("venta_neta",             "Venta $",        formatear_pesos),
        ("pct_merma_sobre_ventas", "% Merma",        formatear_pct),
    ]:
        cols.append(c); headers.append(h); formato[c] = f

    df_display = df_suc[cols].copy()
    for c, f in formato.items():
        df_display[c] = df_display[c].apply(f)
    df_display.columns = headers
    st.dataframe(df_display, width="stretch", hide_index=True)

    botones_descarga(df_suc[cols], "comparativa_sucursales", key)

    # Gráfico: merma apilada por categoría + % en eje secundario
    fig = go.Figure()
    for i, cat in enumerate(merma_cats):
        cu = f"{cat} (u)"
        if cat in df_suc.columns:
            fig.add_bar(
                name=cat, x=df_suc["sucursal"],
                y=(-df_suc[cat]).clip(lower=0),
                marker_color=PALETA[i % len(PALETA)],
            )
    fig.add_scatter(
        name="% Merma s/venta", x=df_suc["sucursal"],
        y=df_suc["pct_merma_sobre_ventas"],
        mode="lines+markers", line=dict(color="#64ffda", width=2),
        marker=dict(size=7), yaxis="y2",
    )
    fig.update_layout(
        barmode="stack", height=430,
        plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
        font=dict(color="#ccd6f6", family="IBM Plex Mono"),
        xaxis=dict(tickangle=-30, gridcolor="#1e2130"),
        yaxis=dict(title="Merma $", gridcolor="#1e2130"),
        yaxis2=dict(title="% Merma", overlaying="y", side="right",
                    gridcolor="#1e2130", ticksuffix="%"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(b=120),
    )
    st.plotly_chart(fig, width="stretch")


def controles_periodo(proyecto: str, key_prefix: str):
    """Renderiza fecha_desde, fecha_hasta, modo y fecha de valorización."""
    fmin, fmax = rango_disponible(proyecto)
    if fmin is None:
        st.info("No hay movimientos cargados todavía.")
        return None

    snapshots = listar_fechas_valorizacion(proyecto)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        fecha_desde = st.date_input(
            "Desde", value=fmin, min_value=fmin, max_value=fmax,
            key=f"{key_prefix}_desde",
        )
    with c2:
        fecha_hasta = st.date_input(
            "Hasta", value=fmax, min_value=fmin, max_value=fmax,
            key=f"{key_prefix}_hasta",
        )
    with c3:
        modo_val = st.selectbox(
            "Valorización", ["costo", "lista_1"],
            format_func=lambda x: "A costo" if x == "costo" else "A precio de lista",
            key=f"{key_prefix}_modo",
        )
    with c4:
        if snapshots:
            fecha_val = st.selectbox(
                "Valorizar al (snapshot)", snapshots,
                key=f"{key_prefix}_fval",
                help="Fecha del stock con la que se valoriza. Default: el más reciente.",
            )
        else:
            fecha_val = None
            st.warning("Sin snapshots de stock — no se puede valorizar.")

    return {
        "fecha_desde": str(fecha_desde),
        "fecha_hasta": str(fecha_hasta),
        "modo_valorizacion": modo_val,
        "fecha_valorizacion": fecha_val,
    }
