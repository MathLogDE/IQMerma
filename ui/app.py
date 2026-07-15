"""
ui/app.py — Entrada de MermaIQ

Config de página, CSS, selección de proyecto y navegación por secciones.
La lógica de cada página vive en ui/paginas/<pagina>.py (función render).
Los helpers y la fachada de core están en ui/comun.py.

Correr con:
    python -m streamlit run ui/app.py
"""

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.comun import CSS
from ui.paginas import (
    inicio, ingesta,
    merma_analisis, merma_control,
    inventario_salud, inventario_politicas,
    distribucion_transferencias, comercial_margenes, forecasting,
)


# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MermaIQ",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Navegación por dominios
# ---------------------------------------------------------------------------

SECCIONES = {
    "General":       ["Inicio", "Ingesta"],
    "Merma":         ["Análisis", "Control de merma"],
    "Inventario":    ["Salud de stock", "Min / Opt / Max"],
    "Distribución":  ["Transferencias"],
    "Comercial":     ["Márgenes"],
    "Forecasting":   ["Forecast"],
}

RENDER = {
    "Inicio":            inicio.render,
    "Ingesta":           ingesta.render,
    "Análisis":          merma_analisis.render,
    "Control de merma":  merma_control.render,
    "Salud de stock":    inventario_salud.render,
    "Min / Opt / Max":   inventario_politicas.render,
    "Transferencias":    distribucion_transferencias.render,
    "Márgenes":          comercial_margenes.render,
    "Forecast":          forecasting.render,
}


def listar_proyectos() -> list[str]:
    projects_dir = ROOT / "projects"
    if not projects_dir.exists():
        return []
    return sorted([
        p.name for p in projects_dir.iterdir()
        if p.is_dir() and (p / "data.duckdb").exists()
    ])


with st.sidebar:
    st.markdown("## 📦 MermaIQ")
    st.markdown("---")

    proyectos = listar_proyectos()
    if not proyectos:
        st.warning("No hay proyectos. Creá uno con:\n`python setup_db.py --project nombre`")
        st.stop()

    proyecto = st.selectbox("PROYECTO", proyectos, key="proyecto_activo")
    st.markdown("---")

    seccion = st.selectbox("SECCIÓN", list(SECCIONES), key="seccion_activa")
    pagina = st.radio("PÁGINA", SECCIONES[seccion], key="pagina_activa")

    st.markdown("---")
    st.markdown(
        f"<small style='color:#4a5568;font-family:IBM Plex Mono,monospace'>"
        f"proyecto: {proyecto}</small>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

RENDER[pagina](proyecto)
