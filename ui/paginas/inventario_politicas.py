"""ui/paginas/inventario_politicas.py — Página Min / Opt / Max"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Políticas de stock — Mín / Ópt / Máx")

    ETIQUETA_POL = {
        "reponer":     "🔴 Reponer",
        "ok":          "🟢 OK",
        "exceso":      "🔵 Exceso",
        "sin_demanda": "⚫ Sin demanda",
    }

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)

    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
    else:
        TODAS_P = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c0, c1, c2 = st.columns([2, 1, 1])
        with c0:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_P] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_P
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="pol_suc",
            )
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="pol_fs")
        with c2:
            dias_demanda = st.selectbox(
                "Ventana de demanda", [56, 90, 180], index=1,
                format_func=lambda d: f"{d} días", key="pol_vent",
            )

        with st.expander("Parámetros del modelo"):
            p1, p2 = st.columns(2)
            with p1:
                lead_time = st.number_input(
                    "Lead time (días)", 1, 120, 7, key="pol_lead",
                    help="Días desde que se dispara el pedido hasta que llega.",
                )
            with p2:
                ciclo_default = st.number_input(
                    "Ciclo default (días)", 7, 365, 30, key="pol_ciclo",
                    help="Ciclo de reposición cuando no se puede estimar de los datos.",
                )
            st.caption(
                "El **ciclo de reposición** se estima por SKU × sucursal como la "
                "mediana de días entre llegadas (Remitido entrante), con fallback "
                "SKU global → rubro → default. Nivel de servicio por clase ABC: "
                "A 95% · B 90% · C 80%."
            )

        if st.button("Calcular políticas"):
            with st.spinner("Calculando..."):
                try:
                    df_pol = calcular_politicas(
                        proyecto,
                        codigodepo=None if suc_sel == TODAS_P else suc_sel,
                        fecha_stock=fecha_stock,
                        dias_demanda=dias_demanda,
                        lead_time_dias=lead_time,
                        ciclo_default=ciclo_default,
                    )
                    st.session_state["df_pol"] = df_pol
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_pol" in st.session_state:
            df_full = st.session_state["df_pol"]

            st.markdown("---")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                f_estado = st.multiselect(
                    "Estado", list(ESTADOS_POLITICA),
                    format_func=lambda e: ETIQUETA_POL[e], key="pol_f_estado",
                )
            with fc2:
                f_abc = st.multiselect("Clase ABC", ["A", "B", "C"], key="pol_f_abc")
            with fc3:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                    key="pol_f_gsr",
                )
            with fc4:
                f_texto = st.text_input("Buscar SKU / descripción", key="pol_f_txt")

            df = df_full
            if f_estado:
                df = df[df["estado"].isin(f_estado)]
            if f_abc:
                df = df[df["clase_abc"].isin(f_abc)]
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            r = resumen_politicas(df)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("A reponer", f"{r['por_estado']['reponer']:,}",
                      delta=f"compra sugerida {formatear_pesos(r['compra_sugerida'])}",
                      delta_color="off")
            k2.metric("En exceso", f"{r['por_estado']['exceso']:,}",
                      delta=f"{formatear_pesos(r['exceso_valorizado'])} sobre el máx",
                      delta_color="off")
            k3.metric("OK", f"{r['por_estado']['ok']:,}")
            k4.metric("Ciclo de reposición mediano",
                      f"{r['ciclo_mediano']:.0f} días" if r["ciclo_mediano"] else "—")
            st.caption(
                f"Stock al **{df_full.attrs.get('fecha_stock')}** · demanda de "
                f"**{df_full.attrs.get('dias_demanda')} días** · lead time "
                f"**{df_full.attrs.get('lead_time_dias'):.0f} días** · compra sugerida "
                "redondeada a bultos (uxb) cuando el bulto cabe en el óptimo."
            )

            # --- Tabla ------------------------------------------------------
            st.markdown("#### Detalle")
            cols = ["codigo", "descripcion", "codigodepo", "sucursal",
                    "clase_abc", "clase_xyz", "estado",
                    "stock", "demanda_diaria", "ciclo_dias", "ciclo_origen",
                    "minimo", "optimo", "maximo",
                    "compra_sugerida", "compra_valorizada"]
            df_display = df[cols].copy()
            df_display["estado"] = df_display["estado"].map(ETIQUETA_POL)
            df_display["demanda_diaria"] = df_display["demanda_diaria"].apply(
                lambda v: f"{v:.2f}")
            for c in ["stock", "ciclo_dias", "minimo", "optimo", "maximo",
                      "compra_sugerida"]:
                df_display[c] = df_display[c].apply(formatear_unidades)
            df_display["compra_valorizada"] = df_display["compra_valorizada"].apply(formatear_pesos)
            df_display.columns = ["Código", "Descripción", "Cód.", "Sucursal",
                                  "ABC", "XYZ", "Estado", "Stock", "Vta/día",
                                  "Ciclo d", "Origen ciclo", "Mín", "Ópt", "Máx",
                                  "Compra u.", "Compra $"]
            st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
            if len(df) > 2000:
                st.caption(f"Mostrando 2.000 de {len(df):,} filas — descargá el listado completo.")
            botones_descarga(df[cols], "politicas_stock", "pol")

            # --- Gráficos ---------------------------------------------------
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("#### Matriz ABC × XYZ (SKU-sucursal)")
                mat = df.pivot_table(index="clase_abc", columns="clase_xyz",
                                     values="codigo", aggfunc="count").fillna(0)
                mat = mat.reindex(index=["A", "B", "C"], columns=["X", "Y", "Z"]).fillna(0)
                fig = go.Figure(go.Heatmap(
                    z=mat.values, x=mat.columns, y=mat.index,
                    colorscale=[[0, "#1e2130"], [1, "#ff6b6b"]],
                    text=mat.values.astype(int), texttemplate="%{text:,}",
                    showscale=False,
                ))
                fig.update_layout(height=340, **layout_oscuro)
                st.plotly_chart(fig, width="stretch")
            with g2:
                df_rep = df[df["estado"] == "reponer"]
                if not df_rep.empty:
                    st.markdown("#### Top 15 compras sugeridas")
                    df_topc = df_rep.nlargest(15, "compra_valorizada").copy()
                    df_topc["label"] = (df_topc["codigo"] + " (" + df_topc["codigodepo"]
                                        + ") - " + df_topc["descripcion"].fillna("").str.slice(0, 25))
                    fig = go.Figure(go.Bar(
                        x=df_topc["compra_valorizada"], y=df_topc["label"],
                        orientation="h", marker_color="#ffa94d",
                    ))
                    fig.update_layout(height=340,
                                      xaxis=dict(title="Compra sugerida $", gridcolor="#1e2130"),
                                      yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                      **layout_oscuro)
                    st.plotly_chart(fig, width="stretch")

            # --- Reporte imprimible --------------------------------------------
            st.markdown("---")
            meta_p = {
                "proyecto": proyecto,
                "sucursal": ("Todas las sucursales" if suc_sel == TODAS_P
                             else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                "dias_demanda": df_full.attrs.get("dias_demanda", ""),
                "lead_time": f"{df_full.attrs.get('lead_time_dias', 0):.0f}",
            }
            html_p = generar_reporte_politicas(proyecto, meta_p, df, r)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_p.encode("utf-8"),
                f"politicas_stock_{meta_p['fecha_stock']}.html",
                "text/html", key="dl_rep_pol",
            )
