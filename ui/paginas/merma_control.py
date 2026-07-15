"""ui/paginas/merma_control.py — Página Control de merma"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Control de merma")

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
    else:
        TODAS_C = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        suc_sel = st.selectbox(
            "Sucursal", [TODAS_C] + sucursales["codigodepo"].tolist(),
            format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_C
                                   else f"{c} — {suc_label.get(c, '')}".strip(" —")),
            key="control_suc",
        )
        ctrl = controles_periodo(proyecto, "control")

        if ctrl and st.button("Analizar"):
            with st.spinner("Analizando..."):
                try:
                    depo = None if suc_sel == TODAS_C else suc_sel
                    comunes = dict(
                        codigodepo=depo,
                        modo_valorizacion=ctrl["modo_valorizacion"],
                        fecha_valorizacion=ctrl["fecha_valorizacion"],
                    )
                    st.session_state["ctrl_ev"] = evolucion_mensual(
                        proyecto, fecha_desde=ctrl["fecha_desde"],
                        fecha_hasta=ctrl["fecha_hasta"], **comunes)
                    st.session_state["ctrl_out"] = movimientos_outliers(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"],
                        top_n=100, **comunes)
                    st.session_state["ctrl_us"] = ajustes_por_usuario(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"], **comunes)
                    st.session_state["ctrl_meta"] = {
                        "proyecto": proyecto,
                        "sucursal": ("Todas las sucursales" if suc_sel == TODAS_C
                                     else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                        "fecha_desde": ctrl["fecha_desde"],
                        "fecha_hasta": ctrl["fecha_hasta"],
                        "modo": ctrl["modo_valorizacion"],
                        "fecha_valorizacion": ctrl["fecha_valorizacion"] or "—",
                    }
                except Exception as e:
                    st.error(f"Error: {e}")

        layout_oscuro = dict(
            plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
            font=dict(color="#ccd6f6", family="IBM Plex Mono"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )

        # --- 1. Evolución mensual ------------------------------------------
        if "ctrl_ev" in st.session_state:
            ev = st.session_state["ctrl_ev"]
            st.markdown("---")
            st.markdown("### Evolución mensual de la merma")
            if ev.empty:
                st.info("Sin movimientos en el período.")
            else:
                if len(ev) >= 2:
                    ult, ant = ev.iloc[-1], ev.iloc[-2]
                    pu = float(ult["pct_merma_sobre_ventas"]) if pd.notna(ult["pct_merma_sobre_ventas"]) else 0.0
                    pa = float(ant["pct_merma_sobre_ventas"]) if pd.notna(ant["pct_merma_sobre_ventas"]) else 0.0
                    c1, c2, c3 = st.columns(3)
                    c1.metric(f"Merma {ult['mes']}", formatear_pesos(ult["merma_total"]),
                              delta=formatear_pesos(ult["merma_total"] - ant["merma_total"]),
                              delta_color="inverse")
                    c2.metric(f"Venta {ult['mes']}", formatear_pesos(ult["venta_neta"]))
                    c3.metric(f"% Merma {ult['mes']}", formatear_pct(ult["pct_merma_sobre_ventas"]),
                              delta=f"{pu - pa:+.2f} pp", delta_color="inverse")

                cats_ev = [c for c in ev.attrs.get("categorias_merma", []) if c in ev.columns]
                fig = go.Figure()
                for i, cat in enumerate(cats_ev):
                    fig.add_bar(name=cat, x=ev["mes"], y=ev[cat],
                                marker_color=PALETA[i % len(PALETA)])
                fig.add_scatter(
                    name="% Merma s/venta", x=ev["mes"], y=ev["pct_merma_sobre_ventas"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=7), yaxis="y2",
                )
                fig.update_layout(
                    barmode="stack", height=420,
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(title="Merma $", gridcolor="#1e2130"),
                    yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                gridcolor="#1e2130", ticksuffix="%"),
                    **layout_oscuro,
                )
                st.plotly_chart(fig, width="stretch")
                botones_descarga(ev, "evolucion_mensual_merma", "ev")

        # --- 2. Outliers ------------------------------------------------------
        if "ctrl_out" in st.session_state:
            out = st.session_state["ctrl_out"]
            st.markdown("---")
            st.markdown("### Movimientos de mayor impacto (outliers)")
            if out.empty:
                st.info("Sin movimientos de merma en el período.")
            else:
                st.caption(
                    f"Merma bruta del período (faltantes por movimiento): "
                    f"**{formatear_pesos(out.attrs.get('merma_periodo'))}** · "
                    "un movimiento que concentre un % alto merece revisión en el ERP. "
                    "Pares de igual magnitud y signo opuesto suelen ser anulaciones."
                )
                top_n = st.slider("Mostrar top", 10, 100, 25, key="out_topn")
                df_o = out.head(top_n).copy()
                df_o["fecha"] = pd.to_datetime(df_o["fecha"]).dt.date
                df_o["valor"] = df_o["valor"].apply(formatear_pesos)
                df_o["unidades"] = df_o["unidades"].apply(formatear_unidades)
                df_o["pct_merma_periodo"] = df_o["pct_merma_periodo"].apply(formatear_pct)
                df_o = df_o[["fecha", "codigodepo", "sucursal", "tipo", "numero",
                             "usuario", "codigo", "descripcion", "unidades",
                             "valor", "pct_merma_periodo"]]
                df_o.columns = ["Fecha", "Cód.", "Sucursal", "Tipo", "N°", "Usuario",
                                "SKU", "Descripción", "Unid.", "Valor $", "% Merma per."]
                st.dataframe(df_o, width="stretch", hide_index=True)
                botones_descarga(out, "outliers_merma", "out")

        # --- 3. Ajustes por usuario -----------------------------------------
        if "ctrl_us" in st.session_state:
            us = st.session_state["ctrl_us"]
            st.markdown("---")
            st.markdown("### Merma por usuario")
            if us.empty:
                st.info("Sin ajustes en el período.")
            else:
                st.caption(
                    "Faltantes y sobrantes valorizados por usuario × sucursal. "
                    "Mucho volumen en ambos sentidos = correcciones cruzadas (revisar operatoria)."
                )
                g1, g2 = st.columns([1, 1])
                with g1:
                    top_u = (us.groupby("usuario")["faltante_valorizado"].sum()
                             .sort_values(ascending=False).head(10))
                    fig = go.Figure(go.Bar(
                        x=top_u.values, y=top_u.index, orientation="h",
                        marker_color="#ff6b6b",
                    ))
                    fig.update_layout(
                        height=380, xaxis=dict(title="Faltante $", gridcolor="#1e2130"),
                        yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                        **layout_oscuro,
                    )
                    st.plotly_chart(fig, width="stretch")
                with g2:
                    df_u = us.copy()
                    for c in ["faltante_valorizado", "sobrante_valorizado", "neto_valorizado"]:
                        df_u[c] = df_u[c].apply(formatear_pesos)
                    df_u["pct_faltante"] = df_u["pct_faltante"].apply(formatear_pct)
                    df_u = df_u[["usuario", "codigodepo", "movimientos", "skus",
                                 "faltante_valorizado", "sobrante_valorizado",
                                 "neto_valorizado", "pct_faltante"]]
                    df_u.columns = ["Usuario", "Suc.", "Movs", "SKUs",
                                    "Faltante $", "Sobrante $", "Neto $", "% Falt."]
                    st.dataframe(df_u, width="stretch", hide_index=True, height=380)
                botones_descarga(us, "merma_por_usuario", "us")

        # --- Reporte imprimible ------------------------------------------------
        if "ctrl_meta" in st.session_state and "ctrl_ev" in st.session_state:
            st.markdown("---")
            meta_c = st.session_state["ctrl_meta"]
            html_c = generar_reporte_control(
                proyecto, meta_c,
                st.session_state.get("ctrl_ev"),
                st.session_state.get("ctrl_out"),
                st.session_state.get("ctrl_us"),
            )
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_c.encode("utf-8"),
                f"control_merma_{meta_c['fecha_desde']}_{meta_c['fecha_hasta']}.html",
                "text/html", key="dl_rep_control",
            )
