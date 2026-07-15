"""ui/paginas/inventario_salud.py — Página Salud de stock"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Salud de stock")

    ETIQUETA_ESTADO = {
        "quiebre":    "🔴 Quiebre",
        "critico":    "🟠 Crítico",
        "ok":         "🟢 OK",
        "sobrestock": "🔵 Sobrestock",
        "muerto":     "⚫ Muerto",
    }
    COLOR_ESTADO = {
        "quiebre": "#ff6b6b", "critico": "#ffa94d", "ok": "#64ffda",
        "sobrestock": "#748ffc", "muerto": "#8892b0",
    }

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)

    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
    else:
        TODAS_S = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c0, c1, c2 = st.columns([2, 1, 1])
        with c0:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_S] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_S
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="salud_suc",
            )
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="salud_fs")
        with c2:
            dias_ventana = st.selectbox(
                "Ventana de demanda", [30, 60, 90, 180], index=2,
                format_func=lambda d: f"{d} días", key="salud_vent",
            )

        c3, c4 = st.columns([2, 1])
        with c3:
            banda = st.slider(
                "Banda de cobertura saludable (días)", 0, 365, (7, 60),
                key="salud_banda",
                help="Debajo del mínimo: crítico. Encima del máximo: sobrestock.",
            )
        with c4:
            incluir_log = st.checkbox(
                "Incluir depósitos logísticos", value=False, key="salud_log",
                help="Los CD no venden: quiebre/muerto no aplican. "
                     "Marcá los depósitos con marcar_logistica().",
            )

        if st.button("Analizar stock"):
            with st.spinner("Analizando..."):
                try:
                    df_salud = analizar_stock(
                        proyecto,
                        codigodepo=None if suc_sel == TODAS_S else suc_sel,
                        fecha_stock=fecha_stock,
                        dias_ventana=dias_ventana,
                        cobertura_min=banda[0],
                        cobertura_max=banda[1],
                        incluir_logistica=incluir_log,
                    )
                    st.session_state["df_salud"] = df_salud
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_salud" in st.session_state:
            df_full = st.session_state["df_salud"]

            st.markdown("---")
            # --- Filtros -----------------------------------------------------
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                f_estado = st.multiselect(
                    "Estado", list(ESTADOS),
                    format_func=lambda e: ETIQUETA_ESTADO[e], key="salud_f_estado",
                )
            with fc2:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                    key="salud_f_gsr",
                )
            with fc3:
                f_texto = st.text_input("Buscar SKU / descripción", key="salud_f_txt")

            df = df_full
            if f_estado:
                df = df[df["estado"].isin(f_estado)]
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            # --- KPIs ----------------------------------------------------------
            r = resumen_salud(df)
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Quiebres", f"{r['por_estado']['quiebre']:,}",
                      delta=f"pierden {formatear_pesos(r['venta_perdida_diaria'])}/día",
                      delta_color="off")
            k2.metric("Críticos", f"{r['por_estado']['critico']:,}")
            k3.metric("Stock muerto", formatear_pesos(r["stock_muerto_valor"]),
                      delta=f"{r['por_estado']['muerto']:,} SKU-sucursal", delta_color="off")
            k4.metric("Sobrestock", formatear_pesos(r["sobrestock_valor"]),
                      delta=f"{r['por_estado']['sobrestock']:,} SKU-sucursal", delta_color="off")
            k5.metric("Cobertura mediana",
                      f"{r['cobertura_mediana']:.0f} días" if r["cobertura_mediana"] else "—")
            st.caption(
                f"Stock al **{df_full.attrs.get('fecha_stock')}** · demanda de los últimos "
                f"**{df_full.attrs.get('dias_ventana')} días** · "
                f"capital total en stock: **{formatear_pesos(r['stock_valor_total'])}**"
            )

            # --- Tabla -----------------------------------------------------------
            st.markdown("#### Detalle")
            cols = ["codigo", "descripcion", "rubro", "codigodepo", "sucursal",
                    "estado", "stock", "venta_diaria", "cobertura_dias",
                    "stock_valorizado", "venta_perdida_diaria"]
            df_display = df[cols].copy()
            df_display["estado"] = df_display["estado"].map(ETIQUETA_ESTADO)
            df_display["stock"] = df_display["stock"].apply(formatear_unidades)
            df_display["venta_diaria"] = df_display["venta_diaria"].apply(
                lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            df_display["cobertura_dias"] = df_display["cobertura_dias"].apply(
                lambda v: f"{v:,.0f} d" if pd.notna(v) else "∞")
            df_display["stock_valorizado"] = df_display["stock_valorizado"].apply(formatear_pesos)
            df_display["venta_perdida_diaria"] = df_display["venta_perdida_diaria"].apply(formatear_pesos)
            df_display.columns = ["Código", "Descripción", "Rubro", "Cód.", "Sucursal",
                                  "Estado", "Stock", "Venta/día", "Cobertura",
                                  "Stock $", "Vta perdida $/día"]
            st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
            if len(df) > 2000:
                st.caption(f"Mostrando 2.000 de {len(df):,} filas — descargá el listado completo.")
            botones_descarga(df[cols], "salud_stock", "salud")

            # --- Gráficos -----------------------------------------------------------
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("#### SKU-sucursal por estado")
                conteo = df["estado"].value_counts().reindex(list(ESTADOS)).fillna(0)
                fig = go.Figure(go.Bar(
                    x=[ETIQUETA_ESTADO[e] for e in conteo.index], y=conteo.values,
                    marker_color=[COLOR_ESTADO[e] for e in conteo.index],
                ))
                fig.update_layout(height=340, xaxis=dict(gridcolor="#1e2130"),
                                  yaxis=dict(gridcolor="#1e2130"), **layout_oscuro)
                st.plotly_chart(fig, width="stretch")
            with g2:
                st.markdown("#### Capital en stock por estado")
                valor = df.groupby("estado")["stock_valorizado"].sum().reindex(list(ESTADOS)).fillna(0)
                fig = go.Figure(go.Bar(
                    x=[ETIQUETA_ESTADO[e] for e in valor.index], y=valor.values,
                    marker_color=[COLOR_ESTADO[e] for e in valor.index],
                ))
                fig.update_layout(height=340, xaxis=dict(gridcolor="#1e2130"),
                                  yaxis=dict(gridcolor="#1e2130"), **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            df_q = df[df["estado"] == "quiebre"]
            if not df_q.empty:
                st.markdown("#### Top 15 quiebres por venta perdida")
                df_topq = df_q.nlargest(15, "venta_perdida_diaria").copy()
                df_topq["label"] = (df_topq["codigo"] + " (" + df_topq["codigodepo"] + ") - "
                                    + df_topq["descripcion"].fillna("").str.slice(0, 28))
                fig = go.Figure(go.Bar(
                    x=df_topq["venta_perdida_diaria"], y=df_topq["label"],
                    orientation="h", marker_color="#ff6b6b",
                ))
                fig.update_layout(height=440,
                                  xaxis=dict(title="$ perdidos por día", gridcolor="#1e2130"),
                                  yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                  **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            df_m = df[df["estado"] == "muerto"]
            if not df_m.empty and df_m["gran_super_rubro"].notna().any():
                st.markdown("#### Stock muerto por gran super rubro")
                df_gm = (df_m.groupby("gran_super_rubro")["stock_valorizado"].sum()
                         .sort_values(ascending=False).head(10))
                fig = go.Figure(go.Bar(
                    x=df_gm.values, y=df_gm.index, orientation="h", marker_color="#8892b0",
                ))
                fig.update_layout(height=400,
                                  xaxis=dict(title="$ inmovilizados", gridcolor="#1e2130"),
                                  yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                  **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            # --- Reporte imprimible --------------------------------------------
            st.markdown("---")
            meta_s = {
                "proyecto": proyecto,
                "sucursal": ("Todas las sucursales" if suc_sel == TODAS_S
                             else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                "dias_ventana": df_full.attrs.get("dias_ventana", ""),
                "banda": f"{banda[0]}–{banda[1]} días",
            }
            html_s = generar_reporte_salud(proyecto, meta_s, df, r, ETIQUETA_ESTADO)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_s.encode("utf-8"),
                f"salud_stock_{meta_s['fecha_stock']}.html",
                "text/html", key="dl_rep_salud",
            )
