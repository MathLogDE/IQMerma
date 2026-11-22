"""ui/paginas/inicio.py — Página Inicio"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title(f"Bienvenido — {proyecto}")

    conn = get_connection(proyecto)
    # El ERP registra el inventario físico como TIPO='INV' (bajo TIPOMOV='AJU')
    n_inv = conn.execute(
        "SELECT COUNT(DISTINCT fecha || '|' || codigodepo) FROM movimientos WHERE tipo = 'INV'"
    ).fetchone()[0]
    n_movimientos = conn.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
    n_skus = conn.execute("SELECT COUNT(DISTINCT codigo) FROM articulos WHERE activo = true").fetchone()[0]
    n_sucursales = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]
    conn.close()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Inventarios cargados", n_inv)
    col2.metric("Movimientos", f"{n_movimientos:,}")
    col3.metric("SKUs activos", f"{n_skus:,}")
    col4.metric("Sucursales", n_sucursales)

    fmin, fmax = rango_disponible(proyecto)
    if fmin:
        st.caption(f"Datos disponibles: {fmin} → {fmax}")

    st.markdown("---")
    st.subheader("Últimas ingestas")
    df_periodos = listar_periodos_ingesta(proyecto)
    if df_periodos.empty:
        st.info("No hay datos cargados aún. Ingresá los archivos en la página Ingesta.")
    else:
        st.dataframe(df_periodos.head(15), width="stretch", hide_index=True)
