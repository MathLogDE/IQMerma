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
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c0, c1, c2 = st.columns([2, 1, 1])
        with c0:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS
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
                        codigodepo=None if suc_sel == TODAS else suc_sel,
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
            fc1, fc2 = st.columns(2)
            with fc1:
                f_estado = st.multiselect(
                    "Estado", list(ESTADOS_POLITICA),
                    format_func=lambda e: ETIQUETA_POL[e], key="pol_f_estado",
                )
            with fc2:
                f_abc = st.multiselect("Clase ABC", ["A", "B", "C"], key="pol_f_abc")

            df = df_full
            if f_estado:
                df = df[df["estado"].isin(f_estado)]
            if f_abc:
                df = df[df["clase_abc"].isin(f_abc)]
            df, _ = filtros_resultado(df, "pol_f")

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
                fig.update_layout(height=340, **layout_grafico())
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
                                      xaxis=dict(title="Compra sugerida $", gridcolor=color_grilla()),
                                      yaxis=dict(autorange="reversed", gridcolor=color_grilla()),
                                      **layout_grafico())
                    st.plotly_chart(fig, width="stretch")

            # --- Clase ABC: ERP vs. calculada -----------------------------------
            st.markdown("---")
            st.markdown("#### Clase ABC: ERP vs. calculada")
            st.caption(
                "El ERP trae una clase ABC por SKU (`articulos.clase`). Acá se "
                "recalcula la misma regla 80/95 por participación en venta "
                "valorizada — sumando **todas las sucursales**, porque el tag "
                "del ERP es un valor único por SKU, no por sucursal — para "
                "poder comparar 1 a 1 y detectar SKUs mal clasificados en el "
                "ERP (o casos donde el cálculo propio difiere)."
            )
            depos_dist = st.multiselect(
                "Depósitos de distribución",
                sucursales["codigodepo"].tolist(),
                default=[d for d in DEPOSITOS_DISTRIBUCION
                         if d in set(sucursales["codigodepo"])],
                format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
                key="pol_abc_depos",
                help="De acá sale el 'Stock en distribución': cuánto de cada "
                     "SKU está en estos depósitos, listo para mandar a las "
                     "sucursales.",
            )
            if st.button("Comparar clases ABC", key="pol_abc_btn"):
                with st.spinner("Comparando..."):
                    try:
                        st.session_state["df_abc_cmp"] = comparar_clase_abc(
                            proyecto, depositos_distribucion=depos_dist)
                    except Exception as e:
                        st.error(f"Error: {e}")

            if "df_abc_cmp" in st.session_state:
                df_abc = st.session_state["df_abc_cmp"]
                con_dato = df_abc[df_abc["clase_erp"] != "(sin dato)"]
                if con_dato.empty:
                    st.info("Ningún SKU con ventas trae clase ABC informada por el ERP.")
                else:
                    coinciden = int(con_dato["coincide"].sum())
                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("SKUs con clase ERP", f"{len(con_dato):,}")
                    a2.metric("Coinciden", f"{coinciden:,}",
                              delta=f"{coinciden / len(con_dato) * 100:.0f}%",
                              delta_color="off")
                    a3.metric("Discrepan", f"{len(con_dato) - coinciden:,}")
                    a4.metric("Stock en distribución",
                              formatear_unidades(con_dato["stock_distribucion"].sum()),
                              help="Unidades en los depósitos de distribución elegidos "
                                   "arriba, al snapshot usado para valorizar — "
                                   "disponibles para distribución inmediata.")

                    solo_disc = st.checkbox(
                        "Mostrar solo discrepancias", value=True, key="pol_abc_solo_disc")
                    tabla = con_dato[~con_dato["coincide"]] if solo_disc else con_dato
                    if tabla.empty:
                        st.success("Sin discrepancias entre la clase del ERP y la calculada.")
                    else:
                        disp = tabla.sort_values("venta_valorizada", ascending=False).copy()
                        disp["venta_valorizada"] = disp["venta_valorizada"].apply(formatear_pesos)
                        disp["stock_distribucion"] = disp["stock_distribucion"].apply(
                            formatear_unidades)
                        disp = disp[["codigo", "descripcion", "clase_erp", "clase_calculada",
                                     "venta_valorizada", "stock_distribucion"]]
                        disp.columns = ["Código", "Descripción", "Clase ERP",
                                        "Clase calculada", "Venta valorizada",
                                        "Stock en distribución"]
                        st.dataframe(disp.head(500), width="stretch", hide_index=True)
                        if len(disp) > 500:
                            st.caption(f"Mostrando 500 de {len(disp):,} filas.")
                        botones_descarga(tabla, "clase_abc_comparacion", "pol_abc")

            # --- Reporte imprimible --------------------------------------------
            st.markdown("---")
            meta_p = {
                "proyecto": proyecto,
                "sucursal": ("Todas las sucursales" if suc_sel == TODAS
                             else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                "dias_demanda": df_full.attrs.get("dias_demanda", ""),
                "lead_time": f"{df_full.attrs.get('lead_time_dias', 0):.0f}",
            }
            html_p = generar_reporte_politicas(proyecto, meta_p, df, r)
            boton_reporte(html_p, f"politicas_stock_{meta_p['fecha_stock']}.html", "dl_rep_pol")
