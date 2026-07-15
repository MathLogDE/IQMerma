"""ui/paginas/merma_analisis.py — Página Análisis"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Análisis de merma")

    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
    else:
        TODAS = "__todas__"
        suc_opts = [TODAS] + sucursales["codigodepo"].tolist()
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        suc_sel = st.selectbox(
            "Sucursal", suc_opts,
            format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS
                                   else f"{c} — {suc_label.get(c, '')}".strip(" —")),
        )

        ctrl = controles_periodo(proyecto, "analisis")

        if ctrl and st.button("Calcular merma"):
            with st.spinner("Calculando..."):
                try:
                    depo = None if suc_sel == TODAS else suc_sel
                    df = calcular_merma(proyecto, depo, **ctrl)
                    st.session_state["df_merma"] = df
                    st.session_state["cats_merma"] = df.attrs.get("categorias", [])
                    st.session_state["fval_merma"] = df.attrs.get("fecha_valorizacion")
                    st.session_state["ctrl_merma"] = ctrl
                    st.session_state["suc_merma"] = suc_sel
                    st.session_state["suc_merma_label"] = (
                        "Todas las sucursales" if suc_sel == TODAS
                        else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_merma" in st.session_state:
            df_full = st.session_state["df_merma"]
            cats = st.session_state.get("cats_merma", [])
            fval = st.session_state.get("fval_merma")

            st.markdown("---")

            # --- Filtros sobre el resultado (sin recalcular) -----------------
            st.markdown("#### Filtros")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                )
            with fc2:
                f_rubro = st.multiselect(
                    "Rubro",
                    sorted(df_full["rubro"].dropna().unique().tolist()),
                )
            with fc3:
                f_marca = st.multiselect(
                    "Marca",
                    sorted(df_full["marca"].dropna().unique().tolist()),
                )
            with fc4:
                f_texto = st.text_input("Buscar SKU / descripción")

            df = df_full
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_rubro:
                df = df[df["rubro"].isin(f_rubro)]
            if f_marca:
                df = df[df["marca"].isin(f_marca)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            # --- KPIs ---------------------------------------------------------
            merma_total   = df["merma_total_valorizada"].sum()
            merma_total_u = df["merma_total_unidades"].sum()
            venta_total   = df["venta_neta"].sum()
            pct_total = (merma_total / venta_total * 100) if venta_total > 0 else None
            skus_con_merma = int((df["merma_total_valorizada"] > 0).sum())

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("SKUs analizados", f"{len(df):,}")
            col2.metric("SKUs con merma", f"{skus_con_merma:,}")
            col3.metric("Merma total", formatear_pesos(merma_total),
                        delta=f"{merma_total_u:,.0f} unidades", delta_color="off")
            col4.metric("Venta total período", formatear_pesos(venta_total))
            col5.metric("% Merma s/ventas", formatear_pct(pct_total))
            if fval:
                st.caption(f"Valorizado con snapshot de stock del **{fval}**")

            # --- Tabla detalle -------------------------------------------------
            st.markdown("#### Detalle por SKU")
            vista = st.radio(
                "Mostrar", ["Valorizado", "Unidades", "Ambos"],
                horizontal=True, key="analisis_vista",
            )

            cols_base = ["codigo", "descripcion", "rubro"]
            headers   = ["Código", "Descripción", "Rubro"]
            cols_mostrar, formato = [], {}
            for c in cats:
                if vista in ("Valorizado", "Ambos"):
                    cols_mostrar.append(c); headers.append(f"{c} $")
                    formato[c] = formatear_pesos
                if vista in ("Unidades", "Ambos"):
                    cu = f"{c} (u)"
                    cols_mostrar.append(cu); headers.append(f"{c} u.")
                    formato[cu] = formatear_unidades
            cols_fin = [
                ("merma_total_valorizada", "Merma Total $", formatear_pesos),
                ("merma_total_unidades",   "Merma Total u.", formatear_unidades),
                ("venta_neta",             "Venta $",        formatear_pesos),
                ("pct_merma_sobre_ventas", "% Merma",        formatear_pct),
            ]
            for c, h, f in cols_fin:
                cols_mostrar.append(c); headers.append(h); formato[c] = f

            df_display = df[cols_base + cols_mostrar].copy()
            for c, f in formato.items():
                df_display[c] = df_display[c].apply(f)
            df_display.columns = headers
            st.dataframe(df_display, width="stretch", hide_index=True)
            botones_descarga(df[cols_base + cols_mostrar], "detalle_merma_sku", "det")

            # --- Dashboard ------------------------------------------------------
            merma_cats = [c for c in categorias_merma(proyecto) if c in df.columns]
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )

            if merma_cats and merma_total > 0:
                g1, g2 = st.columns([1, 2])

                # Composición de la merma por categoría
                with g1:
                    st.markdown("#### Composición de la merma")
                    comp = {c: merma_por_categoria(df, c).sum() for c in merma_cats}
                    comp = {k: v for k, v in comp.items() if v > 0}
                    fig = go.Figure(go.Pie(
                        labels=list(comp.keys()), values=list(comp.values()),
                        hole=0.55,
                        marker=dict(colors=[PALETA[i % len(PALETA)] for i in range(len(comp))]),
                        textinfo="label+percent",
                    ))
                    fig.update_layout(height=380, showlegend=False, **layout_oscuro)
                    st.plotly_chart(fig, width="stretch")

                # Top 15 SKUs por merma
                with g2:
                    st.markdown("#### Top 15 SKUs por merma")
                    df_top = df.nlargest(15, "merma_total_valorizada").copy()
                    df_top["label"] = df_top["codigo"] + " - " + df_top["descripcion"].fillna("").str.slice(0, 30)
                    fig = go.Figure()
                    for i, cat in enumerate(merma_cats):
                        fig.add_bar(
                            name=cat, x=df_top["label"],
                            y=merma_por_categoria(df_top, cat),
                            marker_color=PALETA[i % len(PALETA)],
                        )
                    fig.update_layout(
                        barmode="stack", height=380,
                        xaxis=dict(tickangle=-45, gridcolor="#1e2130"),
                        yaxis=dict(gridcolor="#1e2130"),
                        margin=dict(b=120), **layout_oscuro,
                    )
                    st.plotly_chart(fig, width="stretch")

                # Merma vs venta por gran super rubro (o rubro si no hay jerarquía)
                nivel_dash = ("gran_super_rubro"
                              if df["gran_super_rubro"].notna().any() else "rubro")
                st.markdown(f"#### Merma vs venta por {'gran super rubro' if nivel_dash == 'gran_super_rubro' else 'rubro'}")
                df_gr = (
                    df.groupby(nivel_dash, dropna=False)
                    .agg(merma=("merma_total_valorizada", "sum"),
                         venta=("venta_neta", "sum"))
                    .reset_index()
                    .sort_values("merma", ascending=False)
                    .head(12)
                )
                df_gr[nivel_dash] = df_gr[nivel_dash].fillna("Sin clasificar")
                df_gr["pct"] = (df_gr["merma"] / df_gr["venta"] * 100).where(df_gr["venta"] > 0)
                fig = go.Figure()
                fig.add_bar(
                    name="Merma $", x=df_gr[nivel_dash], y=df_gr["merma"],
                    marker_color="#ff6b6b", yaxis="y1",
                )
                fig.add_scatter(
                    name="% Merma s/venta", x=df_gr[nivel_dash], y=df_gr["pct"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=7), yaxis="y2",
                )
                fig.update_layout(
                    height=420,
                    xaxis=dict(tickangle=-30, gridcolor="#1e2130"),
                    yaxis=dict(title="Merma $", gridcolor="#1e2130"),
                    yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                gridcolor="#1e2130", ticksuffix="%"),
                    margin=dict(b=120), **layout_oscuro,
                )
                st.plotly_chart(fig, width="stretch")

            # --- Comparativa por sucursal (solo con "Todas") --------------------
            ctrl_calc = st.session_state.get("ctrl_merma")
            df_suc = None
            if st.session_state.get("suc_merma") == TODAS and ctrl_calc:
                st.markdown("---")
                st.markdown("### Comparativa por sucursal")
                filtrado = len(df) != len(df_full)
                codigos_t = tuple(sorted(df["codigo"])) if filtrado else None
                with st.spinner("Armando comparativa..."):
                    df_suc = comparativa_cacheada(
                        proyecto,
                        ctrl_calc["fecha_desde"], ctrl_calc["fecha_hasta"],
                        ctrl_calc["modo_valorizacion"], ctrl_calc["fecha_valorizacion"],
                        codigos_t,
                    )
                if filtrado:
                    st.caption("La comparativa respeta los filtros aplicados arriba.")
                sin_log = st.checkbox(
                    "Excluir depósitos logísticos (CD)", value=True, key="comp_sinlog",
                    help="Los CD no venden: distorsionan el % de merma. "
                         "Marcalos en Ingesta → Depósitos.",
                )
                if sin_log and "es_logistica" in df_suc.columns:
                    df_suc = df_suc[~df_suc["es_logistica"].fillna(False)]
                render_comparativa(df_suc, merma_cats, "comp")

            # --- Reporte imprimible ----------------------------------------------
            st.markdown("---")
            st.markdown("### Reporte imprimible")
            filtros_txt = []
            if f_gsr:
                filtros_txt.append("GSR: " + ", ".join(f_gsr))
            if f_rubro:
                filtros_txt.append("Rubro: " + ", ".join(f_rubro))
            if f_marca:
                filtros_txt.append("Marca: " + ", ".join(f_marca))
            if f_texto:
                filtros_txt.append(f"Búsqueda: '{f_texto}'")
            meta = {
                "proyecto":           proyecto,
                "sucursal":           st.session_state.get("suc_merma_label", ""),
                "fecha_desde":        ctrl_calc["fecha_desde"] if ctrl_calc else "",
                "fecha_hasta":        ctrl_calc["fecha_hasta"] if ctrl_calc else "",
                "modo":               ctrl_calc["modo_valorizacion"] if ctrl_calc else "costo",
                "fecha_valorizacion": fval or "—",
                "filtros":            " · ".join(filtros_txt),
            }
            html_reporte = generar_reporte(df, meta, merma_cats, df_sucursales=df_suc)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)",
                html_reporte.encode("utf-8"),
                f"reporte_merma_{meta['fecha_desde']}_{meta['fecha_hasta']}.html",
                "text/html", key="dl_reporte",
            )
            st.caption("Abrilo en el navegador e imprimí con Ctrl+P (o guardá como PDF).")
