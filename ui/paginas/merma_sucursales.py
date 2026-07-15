"""ui/paginas/merma_sucursales.py — Página Sucursales"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Resumen por sucursal")

    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay datos cargados.")
    else:
        ctrl = controles_periodo(proyecto, "sucursales")

        if ctrl and st.button("Calcular todas las sucursales"):
            with st.spinner("Calculando..."):
                try:
                    df_res = merma_por_sucursal(proyecto, **ctrl)
                    st.session_state["df_sucursales"] = df_res
                    st.session_state["cats_merma_suc"] = [
                        c for c in categorias_merma(proyecto)
                        if c in df_res.columns
                    ]
                    st.session_state["fval_suc"] = df_res.attrs.get("fecha_valorizacion")
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_sucursales" in st.session_state:
            df_res = st.session_state["df_sucursales"]
            merma_cats = st.session_state.get("cats_merma_suc", [])

            sin_log = st.checkbox(
                "Excluir depósitos logísticos (CD)", value=True, key="suc_sinlog",
                help="Los CD no venden: distorsionan el % de merma. "
                     "Marcalos en Ingesta → Depósitos.",
            )
            if sin_log and "es_logistica" in df_res.columns:
                df_res = df_res[~df_res["es_logistica"].fillna(False)]

            merma_t = df_res["merma_total_valorizada"].sum()
            venta_t = df_res["venta_neta"].sum()
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Sucursales", len(df_res))
            col2.metric("Merma total", formatear_pesos(merma_t),
                        delta=f"{df_res['merma_total_unidades'].sum():,.0f} unidades",
                        delta_color="off")
            col3.metric("Venta total", formatear_pesos(venta_t))
            col4.metric("% Merma global",
                        formatear_pct((merma_t / venta_t * 100) if venta_t > 0 else None))
            fval_suc = st.session_state.get("fval_suc")
            if fval_suc:
                st.caption(f"Valorizado con snapshot de stock del **{fval_suc}**")

            render_comparativa(df_res, merma_cats, "suc")
