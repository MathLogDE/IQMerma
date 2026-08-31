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
    merma_analisis, merma_control, merma_avance, merma_ultimos_movimientos,
    merma_transformaciones,
    inventario_salud, inventario_politicas, inventario_series, inventario_cruce,
    inventario_interanual, inventario_reposicion, inventario_pedido_sucursal,
    inventario_pedido_general, inventario_pedido_general2,
    distribucion_transferencias, distribucion_diferencias,
    comercial_margenes, comercial_comprobantes, forecasting,
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
    "Merma":         ["Análisis", "Control de merma", "Avance de inventario",
                      "Últimos movimientos", "Transformaciones"],
    "Inventario":    ["Salud de stock", "Serie de stock", "Cruce por sucursal",
                      "Evolución interanual", "Perfil de reposición",
                      "Pedido desde sucursal", "Pedido general de distribución",
                      "Pedido general de distribución 2",
                      "Min / Opt / Max"],
    "Distribución":  ["Transferencias", "Diferencias de camión"],
    "Comercial":     ["Márgenes", "Comprobantes y canasta"],
    "Forecasting":   ["Forecast"],
}

RENDER = {
    "Inicio":            inicio.render,
    "Ingesta":           ingesta.render,
    "Análisis":          merma_analisis.render,
    "Control de merma":  merma_control.render,
    "Avance de inventario": merma_avance.render,
    "Últimos movimientos": merma_ultimos_movimientos.render,
    "Transformaciones":  merma_transformaciones.render,
    "Salud de stock":    inventario_salud.render,
    "Serie de stock":    inventario_series.render,
    "Cruce por sucursal": inventario_cruce.render,
    "Evolución interanual": inventario_interanual.render,
    "Perfil de reposición": inventario_reposicion.render,
    "Pedido desde sucursal": inventario_pedido_sucursal.render,
    "Pedido general de distribución": inventario_pedido_general.render,
    "Pedido general de distribución 2": inventario_pedido_general2.render,
    "Min / Opt / Max":   inventario_politicas.render,
    "Transferencias":    distribucion_transferencias.render,
    "Diferencias de camión": distribucion_diferencias.render,
    "Márgenes":          comercial_margenes.render,
    "Comprobantes y canasta": comercial_comprobantes.render,
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
    # El sidebar es fondo oscuro fijo (ver CSS en ui/comun.py) sin importar
    # el tema de Streamlit, así que siempre usa la variante de texto claro.
    st.image(str(ROOT / "ui" / "iqmerma_logo_dark.png"), width="stretch")
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
