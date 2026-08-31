"""ui/paginas/distribucion_transferencias.py — Página Transferencias"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Transferencias sugeridas")
    st.caption(
        "Redistribución lateral: SKUs en **exceso** en una sucursal que otra "
        "necesita **reponer** — se reabastece con capital ya comprado, sin "
        "esperar al proveedor. El donante nunca baja de su nivel óptimo. "
        "Los depósitos logísticos quedan fuera (CD→sucursal es la reposición normal)."
    )

    snapshots = listar_fechas_valorizacion(proyecto)
    if not snapshots:
        st.info("Se necesita al menos un snapshot de stock.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="tr_fs")
        with c2:
            dias_demanda = st.selectbox(
                "Ventana de demanda", [56, 90, 180], index=1,
                format_func=lambda d: f"{d} días", key="tr_vent")
        with c3:
            lead_time = st.number_input("Lead time (días)", 1, 120, 7, key="tr_lead")

        if st.button("Sugerir transferencias"):
            with st.spinner("Calculando políticas y cruzando excesos con faltantes..."):
                try:
                    df_tr = sugerir_transferencias(
                        proyecto, fecha_stock=fecha_stock,
                        dias_demanda=dias_demanda, lead_time_dias=lead_time,
                    )
                    st.session_state["df_transf"] = df_tr
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_transf" in st.session_state:
            df_full = st.session_state["df_transf"]
            st.markdown("---")

            if df_full.empty:
                st.info("No hay cruces exceso ↔ reposición entre sucursales con estos parámetros.")
            else:
                # Filtros
                fc1, fc2 = st.columns(2)
                with fc1:
                    f_ori = st.multiselect(
                        "Origen", sorted(df_full["origen"].unique().tolist()), key="tr_f_ori")
                with fc2:
                    f_des = st.multiselect(
                        "Destino", sorted(df_full["destino"].unique().tolist()), key="tr_f_des")

                df = df_full
                if f_ori:
                    df = df[df["origen"].isin(f_ori)]
                if f_des:
                    df = df[df["destino"].isin(f_des)]
                df, _ = filtros_resultado(df, "tr_f", dimensiones=("gran_super_rubro",))

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Sugerencias", f"{len(df):,}")
                k2.metric("SKUs", f"{df['codigo'].nunique():,}")
                k3.metric("Unidades", f"{df['unidades'].sum():,.0f}")
                k4.metric("Valor a costo", formatear_pesos(df["valor"].sum()),
                          delta="reposición sin comprar", delta_color="off")
                st.caption(f"Stock al **{df_full.attrs.get('fecha_stock')}**")

                # Tabla
                st.markdown("#### Detalle")
                cols = ["codigo", "descripcion", "rubro", "clase_abc",
                        "origen", "origen_nombre", "destino", "destino_nombre",
                        "unidades", "valor", "stock_origen", "optimo_origen",
                        "stock_destino", "optimo_destino"]
                df_display = df[cols].copy()
                for c in ["unidades", "stock_origen", "optimo_origen",
                          "stock_destino", "optimo_destino"]:
                    df_display[c] = df_display[c].apply(formatear_unidades)
                df_display["valor"] = df_display["valor"].apply(formatear_pesos)
                df_display.columns = ["Código", "Descripción", "Rubro", "ABC",
                                      "Origen", "Suc. origen", "Destino", "Suc. destino",
                                      "Enviar u.", "Valor $", "Stock orig.", "Ópt orig.",
                                      "Stock dest.", "Ópt dest."]
                st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
                botones_descarga(df[cols], "transferencias_sugeridas", "tr")

                # Matriz origen × destino
                st.markdown("#### Valor por ruta (origen → destino)")
                mat = df.pivot_table(index="origen", columns="destino",
                                     values="valor", aggfunc="sum").fillna(0)
                fig = go.Figure(go.Heatmap(
                    z=mat.values, x=mat.columns, y=mat.index,
                    colorscale=[[0, "#1e2130"], [1, "#64ffda"]],
                    text=(mat.values / 1000).round(0), texttemplate="%{text:,}K",
                    showscale=False,
                ))
                fig.update_layout(
                    height=420,
                    xaxis=dict(title="Destino"), yaxis=dict(title="Origen"),
                    **layout_grafico(),
                )
                st.plotly_chart(fig, width="stretch")

                # Reporte imprimible
                st.markdown("---")
                meta_t = {
                    "proyecto": proyecto,
                    "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                    "dias_demanda": dias_demanda,
                }
                html_t = generar_reporte_transferencias(proyecto, meta_t, df)
                boton_reporte(html_t, f"transferencias_{meta_t['fecha_stock']}.html",
                              "dl_rep_transf")
