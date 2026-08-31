"""
ui/app_cliente.py — Entrada de MermaIQ para acceso web de clientes

Instancia de SOLO LECTURA pensada para exponerse por túnel a internet (ver
plan de acceso web). Diferencias con ui/app.py:
  - Nunca importa/registra "Ingesta" ni ninguna página de escritura.
  - Activa setup_db.activar_modo_solo_lectura() antes de tocar cualquier
    conexión: toda query de este proceso abre el .duckdb con read_only=True,
    así nunca compite por el lock exclusivo con una ingesta corriendo en la
    instancia admin.
  - Exige login (streamlit-authenticator, auth/usuarios.yaml) y cada usuario
    ve solo los proyectos listados en su propio `proyectos:` del YAML — no
    el selector abierto de todos los clientes que tiene ui/app.py.

Correr con (puerto distinto al de la instancia admin):
    python -m streamlit run ui/app_cliente.py --server.port 8502
"""

import sys
from pathlib import Path

import streamlit as st
import streamlit_authenticator as stauth
import yaml

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import setup_db
setup_db.activar_modo_solo_lectura()

from ui.comun import CSS
from ui.paginas import (
    inicio,
    merma_analisis, merma_control, merma_avance, merma_transformaciones,
    inventario_salud, inventario_politicas, inventario_series, inventario_cruce,
    inventario_interanual, inventario_reposicion, inventario_pedido_sucursal,
    inventario_pedido_general,
    distribucion_transferencias, distribucion_diferencias,
    comercial_margenes, comercial_comprobantes, forecasting,
)
from core.auth import PAGINAS_DISPONIBLES, TODAS_LAS_PAGINAS

AUTH_CONFIG = ROOT / "auth" / "usuarios.yaml"


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
# Login
# ---------------------------------------------------------------------------

if not AUTH_CONFIG.exists():
    st.error(
        f"No existe {AUTH_CONFIG}. Copiá auth/usuarios.ejemplo.yaml a "
        f"auth/usuarios.yaml y cargá al menos un usuario."
    )
    st.stop()

authenticator = stauth.Authenticate(str(AUTH_CONFIG))
authenticator.login()

estado = st.session_state.get("authentication_status")
if estado is False:
    st.error("Usuario o contraseña incorrectos.")
    st.stop()
elif estado is None:
    st.info("Ingresá tus credenciales para continuar.")
    st.stop()

username = st.session_state["username"]

# Leído aparte del propio YAML (no vía el authenticator): así no depende de
# atributos internos de streamlit_authenticator, que son API privada.
config = yaml.safe_load(AUTH_CONFIG.read_text(encoding="utf-8"))
datos_usuario = config.get("credentials", {}).get("usernames", {}).get(username, {})
proyectos_permitidos = set(datos_usuario.get("proyectos", []))

# Sin `paginas` guardado (usuario creado antes de este permiso) = ve todas,
# igual que antes de que existiera esta restricción.
_paginas_guardadas = datos_usuario.get("paginas")
paginas_permitidas = set(
    _paginas_guardadas if _paginas_guardadas is not None else TODAS_LAS_PAGINAS
)


# ---------------------------------------------------------------------------
# Navegación por dominios (sin Ingesta), filtrada a lo que el usuario puede ver
# ---------------------------------------------------------------------------

SECCIONES = {
    seccion: [p for p in paginas if p in paginas_permitidas]
    for seccion, paginas in PAGINAS_DISPONIBLES.items()
}
SECCIONES = {seccion: paginas for seccion, paginas in SECCIONES.items() if paginas}

RENDER = {
    "Inicio":            inicio.render,
    "Análisis":          merma_analisis.render,
    "Control de merma":  merma_control.render,
    "Avance de inventario": merma_avance.render,
    "Transformaciones":  merma_transformaciones.render,
    "Salud de stock":    inventario_salud.render,
    "Serie de stock":    inventario_series.render,
    "Cruce por sucursal": inventario_cruce.render,
    "Evolución interanual": inventario_interanual.render,
    "Perfil de reposición": inventario_reposicion.render,
    "Pedido desde sucursal": inventario_pedido_sucursal.render,
    "Pedido general de distribución": inventario_pedido_general.render,
    "Min / Opt / Max":   inventario_politicas.render,
    "Transferencias":    distribucion_transferencias.render,
    "Diferencias de camión": distribucion_diferencias.render,
    "Márgenes":          comercial_margenes.render,
    "Comprobantes y canasta": comercial_comprobantes.render,
    "Forecast":          forecasting.render,
}


def listar_proyectos_permitidos() -> list[str]:
    projects_dir = ROOT / "projects"
    if not projects_dir.exists():
        return []
    return sorted([
        p.name for p in projects_dir.iterdir()
        if p.is_dir() and p.name in proyectos_permitidos
        and (p / "data.duckdb").exists()
    ])


with st.sidebar:
    st.image(str(ROOT / "ui" / "iqmerma_logo_dark.png"), width="stretch")
    st.markdown("---")

    proyectos = listar_proyectos_permitidos()
    if not proyectos:
        st.warning(
            "Tu usuario no tiene proyectos asignados todavía. "
            "Consultá al administrador."
        )
        authenticator.logout("Cerrar sesión", "sidebar")
        st.stop()

    proyecto = st.selectbox("PROYECTO", proyectos, key="proyecto_activo")
    st.markdown("---")

    if not SECCIONES:
        st.warning(
            "Tu usuario no tiene páginas habilitadas todavía. "
            "Consultá al administrador."
        )
        authenticator.logout("Cerrar sesión", "sidebar")
        st.stop()

    seccion = st.selectbox("SECCIÓN", list(SECCIONES), key="seccion_activa")
    pagina = st.radio("PÁGINA", SECCIONES[seccion], key="pagina_activa")

    st.markdown("---")
    st.markdown(
        f"<small style='color:#4a5568;font-family:IBM Plex Mono,monospace'>"
        f"{st.session_state.get('name', username)} · proyecto: {proyecto}</small>",
        unsafe_allow_html=True,
    )
    authenticator.logout("Cerrar sesión", "sidebar")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

RENDER[pagina](proyecto)
