"""ui/paginas/forecasting.py — Página Forecast"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Forecast de ventas")
    st.caption(
        "Proyección mensual de **unidades vendidas** por nivel de agregación "
        "(a nivel SKU la demanda es errática y el forecast sería ruido). "
        "Modelo: estacionalidad + tendencia robusta amortiguada; los meses "
        "atípicos se detectan y no dominan el ajuste. El error esperado (MAPE) "
        "se mide por backtest contra los últimos meses reales."
    )

    ETIQUETA_NIVEL = {
        "total": "Total", "gran_super_rubro": "Gran Super Rubro",
        "rubro": "Rubro", "sucursal": "Sucursal",
    }
    ETIQUETA_METRICA = {
        "ventas": "Ventas (salida)",
        "transferencias": "Transferencias recibidas",
    }
    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay movimientos cargados.")
    else:
        TODAS_F = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metrica = st.selectbox(
                "Métrica a proyectar", list(METRICAS),
                format_func=lambda m: ETIQUETA_METRICA[m], key="fc_metrica",
                help="Proyectar transferencias sirve para planificar el "
                     "abastecimiento; compararlas contra ventas muestra si "
                     "una caída de venta es de demanda o de abastecimiento.",
            )
        with c2:
            nivel = st.selectbox(
                "Nivel de agregación", list(NIVELES),
                format_func=lambda n: ETIQUETA_NIVEL[n], key="fc_nivel",
            )
        with c3:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_F] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas" if c == TODAS_F
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="fc_suc", disabled=(nivel == "sucursal"),
            )
        with c4:
            horizonte = st.selectbox(
                "Horizonte", [3, 6, 12], index=1,
                format_func=lambda h: f"{h} meses", key="fc_hor",
            )

        superponer = st.checkbox(
            f"Superponer la serie real de "
            f"{'transferencias' if metrica == 'ventas' else 'ventas'}",
            value=True, key="fc_overlay",
            help="Si las transferencias caen antes que las ventas, la caída "
                 "es de abastecimiento, no de demanda.",
        )

        if st.button("Proyectar"):
            with st.spinner("Proyectando..."):
                try:
                    depo = (None if (suc_sel == TODAS_F or nivel == "sucursal")
                            else suc_sel)
                    df_fc = forecast_ventas(
                        proyecto, codigodepo=depo,
                        nivel=nivel, horizonte=horizonte, backtest=3,
                        metrica=metrica,
                    )
                    st.session_state["df_fc"] = df_fc
                    otra = "transferencias" if metrica == "ventas" else "ventas"
                    st.session_state["df_fc_otra"] = serie_mensual_real(
                        proyecto, codigodepo=depo, nivel=nivel, metrica=otra,
                    )
                    st.session_state["fc_otra_nombre"] = ETIQUETA_METRICA[otra]
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_fc" in st.session_state:
            df_fc = st.session_state["df_fc"]
            if df_fc.empty:
                st.info("Sin datos suficientes para proyectar.")
            else:
                st.markdown("---")
                mapes = df_fc.attrs.get("mape", {})
                atipicos = df_fc.attrs.get("meses_atipicos", {})
                excluidos = df_fc.attrs.get("grupos_excluidos", [])

                grupos = sorted(df_fc["grupo"].unique().tolist())
                grupo_sel = (st.selectbox("Grupo", grupos, key="fc_grupo")
                             if len(grupos) > 1 else grupos[0])

                g = df_fc[df_fc["grupo"] == grupo_sel]
                reales = g[g["tipo"] == "real"]
                fc = g[g["tipo"] == "forecast"]

                k1, k2, k3 = st.columns(3)
                if not fc.empty:
                    prox = fc.iloc[0]
                    k1.metric(f"Forecast {prox['mes']}",
                              f"{prox['unidades']:,.0f} u.",
                              delta=formatear_pesos(prox["valor"]), delta_color="off")
                    k2.metric(f"Total {len(fc)} meses",
                              f"{fc['unidades'].sum():,.0f} u.",
                              delta=formatear_pesos(fc["valor"].sum()), delta_color="off")
                mape_g = mapes.get(str(grupo_sel))
                k3.metric("Error esperado (MAPE backtest)",
                          f"{mape_g:.0f}%" if mape_g is not None else "—",
                          help="Promedio del error porcentual al predecir los "
                               "últimos 3 meses reales con el modelo.")

                at_g = atipicos.get(str(grupo_sel), [])
                if at_g:
                    st.warning(
                        f"Meses atípicos detectados (no dominan el ajuste, "
                        f"conviene investigarlos en Control de merma / con el ERP): "
                        f"{', '.join(at_g)}"
                    )

                # --- gráfico ------------------------------------------------
                fig = go.Figure()
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_sup"],
                    mode="lines", line=dict(width=0), showlegend=False,
                )
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_inf"],
                    mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(255,169,77,0.15)",
                )
                fig.add_scatter(
                    name="Real", x=reales["mes"], y=reales["unidades"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=6),
                )
                df_at = reales[reales["mes"].isin(at_g)]
                if not df_at.empty:
                    fig.add_scatter(
                        name="Atípico", x=df_at["mes"], y=df_at["unidades"],
                        mode="markers",
                        marker=dict(size=11, color="#ff6b6b", symbol="x"),
                    )
                if superponer and "df_fc_otra" in st.session_state:
                    df_o = st.session_state["df_fc_otra"]
                    if not df_o.empty:
                        df_og = (df_o[df_o["grupo"] == grupo_sel]
                                 .sort_values("mes"))
                        if not df_og.empty:
                            fig.add_scatter(
                                name=st.session_state.get("fc_otra_nombre", "Otra métrica"),
                                x=df_og["mes"], y=df_og["unidades"],
                                mode="lines",
                                line=dict(color="#748ffc", width=1.5, dash="dot"),
                            )
                fig.add_scatter(
                    name="Forecast", x=fc["mes"], y=fc["unidades"],
                    mode="lines+markers",
                    line=dict(color="#ffa94d", width=2, dash="dash"),
                    marker=dict(size=7),
                )
                fig.update_layout(
                    height=440,
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(title="Unidades / mes", gridcolor="#1e2130"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, width="stretch")

                # --- tabla de proyección (todos los grupos) -------------------
                st.markdown("#### Proyección por grupo")
                df_t = df_fc[df_fc["tipo"] == "forecast"].pivot_table(
                    index="grupo", columns="mes", values="unidades", aggfunc="sum",
                ).round(0)
                st.dataframe(
                    df_t.style.format("{:,.0f}"), width="stretch",
                )
                botones_descarga(
                    df_fc[df_fc["tipo"] == "forecast"], "forecast_ventas", "fc")

                meta_f = {
                    "proyecto": proyecto,
                    "metrica": ETIQUETA_METRICA.get(
                        df_fc.attrs.get("metrica", "ventas"), "Ventas"),
                    "nivel": ETIQUETA_NIVEL.get(nivel, nivel),
                    "sucursal": ("Todas" if (suc_sel == TODAS_F or nivel == "sucursal")
                                 else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                    "horizonte": horizonte,
                }
                html_f = generar_reporte_forecast(
                    proyecto, meta_f, df_fc, grupo_detalle=grupo_sel)
                st.download_button(
                    "🖨 Descargar reporte (HTML imprimible)", html_f.encode("utf-8"),
                    f"forecast_{meta_f['horizonte']}m.html",
                    "text/html", key="dl_rep_fc",
                )

                if excluidos:
                    st.caption(
                        f"{len(excluidos)} grupos sin proyección por historia "
                        f"insuficiente (< 12 meses): {', '.join(excluidos[:8])}"
                        + ("…" if len(excluidos) > 8 else "")
                    )
