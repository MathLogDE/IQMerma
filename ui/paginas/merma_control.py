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
        suc_largo, _ = etiquetas_sucursal(sucursales)
        suc_sel = st.multiselect(
            "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
            format_func=lambda c: suc_largo.get(c, str(c)), key="control_sucs",
        )
        ctrl = controles_periodo(proyecto, "control", modos=list(MODOS_VALORIZACION_MERMA))
        agrupar_por = st.radio(
            "Agrupar ajustes por", ["usuario", "tipo_aj"], horizontal=True,
            format_func=lambda a: "Usuario" if a == "usuario" else "Motivo de ajuste",
            key="control_agrupar",
            help="Motivo de ajuste es el campo que informa el ERP (INVENTARIO, "
                 "ANULACIÓN, RECARGA...); usuario es quién hizo el movimiento.",
        )

        if ctrl and st.button("Analizar"):
            with st.spinner("Analizando..."):
                try:
                    depo = suc_sel or None
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
                    st.session_state["ctrl_us"] = ajustes_por(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"],
                        agrupar_por=agrupar_por, **comunes)
                    st.session_state["ctrl_meta"] = {
                        "proyecto": proyecto,
                        "sucursal": etiqueta_seleccion_sucursales(suc_sel, suc_largo),
                        "fecha_desde": ctrl["fecha_desde"],
                        "fecha_hasta": ctrl["fecha_hasta"],
                        "modo": ctrl["modo_valorizacion"],
                        "fecha_valorizacion": ctrl["fecha_valorizacion"] or "—",
                    }
                except Exception as e:
                    st.error(f"Error: {e}")

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
                    xaxis=dict(gridcolor=color_grilla()),
                    yaxis=dict(title="Merma $", gridcolor=color_grilla()),
                    yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                gridcolor=color_grilla(), ticksuffix="%"),
                    **layout_grafico(),
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

        # --- 3. Ajustes por usuario / motivo ---------------------------------
        col_grupo = None
        if "ctrl_us" in st.session_state:
            us = st.session_state["ctrl_us"]
            col_grupo = "usuario" if "usuario" in us.columns else "tipo_aj"
            etiqueta_grupo = "Usuario" if col_grupo == "usuario" else "Motivo de ajuste"
            st.markdown("---")
            st.markdown(f"### Merma por {etiqueta_grupo.lower()}")
            if us.empty:
                st.info("Sin ajustes en el período.")
            else:
                st.caption(
                    f"Faltantes y sobrantes valorizados por {etiqueta_grupo.lower()} × "
                    "sucursal. Mucho volumen en ambos sentidos = correcciones cruzadas "
                    "(revisar operatoria)."
                )
                g1, g2 = st.columns([1, 1])
                with g1:
                    top_u = (us.groupby(col_grupo)["faltante_valorizado"].sum()
                             .sort_values(ascending=False).head(10))
                    fig = go.Figure(go.Bar(
                        x=top_u.values, y=top_u.index, orientation="h",
                        marker_color="#ff6b6b",
                    ))
                    fig.update_layout(
                        height=380, xaxis=dict(title="Faltante $", gridcolor=color_grilla()),
                        yaxis=dict(autorange="reversed", gridcolor=color_grilla()),
                        **layout_grafico(),
                    )
                    st.plotly_chart(fig, width="stretch")
                with g2:
                    df_u = us.copy()
                    for c in ["faltante_valorizado", "sobrante_valorizado", "neto_valorizado"]:
                        df_u[c] = df_u[c].apply(formatear_pesos)
                    df_u["pct_faltante"] = df_u["pct_faltante"].apply(formatear_pct)
                    df_u = df_u[[col_grupo, "codigodepo", "movimientos", "skus",
                                 "faltante_valorizado", "sobrante_valorizado",
                                 "neto_valorizado", "pct_faltante"]]
                    df_u.columns = [etiqueta_grupo, "Suc.", "Movs", "SKUs",
                                    "Faltante $", "Sobrante $", "Neto $", "% Falt."]
                    st.dataframe(df_u, width="stretch", hide_index=True, height=380)
                botones_descarga(us, f"merma_por_{col_grupo}", "us")

        # --- Reporte imprimible ------------------------------------------------
        if "ctrl_meta" in st.session_state and "ctrl_ev" in st.session_state:
            st.markdown("---")
            meta_c = st.session_state["ctrl_meta"]
            html_c = generar_reporte_control(
                proyecto, meta_c,
                st.session_state.get("ctrl_ev"),
                st.session_state.get("ctrl_out"),
                # el reporte muestra esta tabla como "por usuario": si se
                # agrupó por motivo, se omite en vez de mostrar la columna
                # equivocada.
                st.session_state.get("ctrl_us") if col_grupo == "usuario" else None,
            )
            boton_reporte(html_c,
                          f"control_merma_{meta_c['fecha_desde']}_{meta_c['fecha_hasta']}.html",
                          "dl_rep_control")
