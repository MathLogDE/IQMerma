"""ui/paginas/merma_transformaciones.py — Informes de transformaciones (TR)"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Transformaciones")
    st.caption(
        "Movimientos tipo **TR** (producción simple): dan de baja un SKU y "
        "dan de alta el SKU de liquidación que lo reemplaza. Ya suman a la "
        "merma en la página Análisis (categoría 'Transformación') — acá se "
        "ven en detalle, por sucursal y por SKU."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
        return
    suc_largo, _ = etiquetas_sucursal(sucursales)
    suc_sel = st.multiselect(
        "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
        format_func=lambda c: suc_largo.get(c, str(c)), key="tr_sucs",
    )

    controles = controles_periodo(proyecto, "tr")
    if controles is None:
        return

    with st.expander("Filtrar por rubro / marca / gran super rubro"):
        cat = catalogo_cacheado(proyecto)
        recorte, hay_filtro = filtros_catalogo(cat, "tr_cat", con_activo=True)
        if hay_filtro:
            st.caption(f"{len(recorte):,} SKUs en el recorte.")
    codigos = recorte["codigo"].tolist() if hay_filtro else None

    if st.button("Calcular transformaciones"):
        with st.spinner("Buscando movimientos TR..."):
            try:
                df = detalle_transformaciones(
                    proyecto, controles["fecha_desde"], controles["fecha_hasta"],
                    sucursales=suc_sel or None, codigos=codigos,
                    modo_valorizacion=controles["modo_valorizacion"],
                    fecha_valorizacion=controles["fecha_valorizacion"])
                st.session_state["tr_detalle"] = df
                st.session_state["tr_fval_calc"] = df.attrs.get("fecha_valorizacion")
                st.session_state["tr_args"] = dict(controles)
                st.session_state["tr_suc_sel"] = suc_sel
                st.session_state["tr_suc_label"] = etiqueta_seleccion_sucursales(suc_sel, suc_largo)
                st.session_state["tr_recorte_label"] = (
                    f"{len(recorte):,} SKUs filtrados" if hay_filtro else "")
            except Exception as e:
                st.error(f"Error: {e}")

    if "tr_detalle" not in st.session_state:
        return

    detalle = st.session_state["tr_detalle"]
    args = st.session_state["tr_args"]
    fval = st.session_state.get("tr_fval_calc")

    st.markdown("---")

    if detalle.empty:
        st.info("Sin movimientos de transformación (TR) para este recorte.")
        return

    vista = st.radio("Vista", ["Valorizado", "Unidades"], horizontal=True, key="tr_vista")
    fmt = formatear_pesos if vista == "Valorizado" else formatear_unidades
    col = "valor" if vista == "Valorizado" else "unidades"

    # --- Filtros sobre el resultado (sin recalcular) -------------------------
    st.markdown("#### Filtros")
    detalle, _ = filtros_resultado(detalle, "tr_f", dimensiones=("gran_super_rubro", "marca"))

    baja = detalle[detalle["sentido"] == "Baja"]
    alta = detalle[detalle["sentido"] == "Alta"]

    # --- KPIs ------------------------------------------------------------
    n_comprobantes = detalle.drop_duplicates(["numero", "codigodepo"]).shape[0]
    u_baja, v_baja = baja["unidades"].sum(), baja["valor"].sum()
    u_alta, v_alta = alta["unidades"].sum(), alta["valor"].sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Transformaciones", f"{n_comprobantes:,}")
    k2.metric("Sucursales", int(detalle["codigodepo"].nunique()))
    k3.metric("Dado de baja", fmt(v_baja if vista == "Valorizado" else u_baja))
    k4.metric("Alta (liquidación)", fmt(v_alta if vista == "Valorizado" else u_alta))
    k5.metric("Neto", fmt((v_alta - v_baja) if vista == "Valorizado" else (u_alta - u_baja)))
    if fval:
        st.caption(f"Valorizado con snapshot de stock del **{fval}**")

    tab_pares, tab_sku, tab_suc = st.tabs(
        ["🔀 Pares (baja → alta)", "📋 Detalle por SKU", "🏢 Comparativa por sucursal"])

    # ======================================================================
    # Pares (baja -> alta) por comprobante
    # ======================================================================
    with tab_pares:
        pares = pares_transformacion(detalle)
        if pares.empty:
            st.info("Sin transformaciones con los filtros aplicados.")
        else:
            disp = pares.copy()
            disp["fecha"] = disp["fecha"].dt.date.astype(str)
            disp["Sucursal"] = disp["codigodepo"].map(suc_largo).fillna(disp["codigodepo"])
            if vista == "Valorizado":
                disp["valor_baja"] = disp["valor_baja"].apply(formatear_pesos)
                disp["valor_alta"] = disp["valor_alta"].apply(formatear_pesos)
                disp["neto_valor"] = disp["neto_valor"].apply(formatear_pesos)
                cols_mostrar = ["numero", "Sucursal", "fecha", "skus_baja", "skus_alta",
                                "valor_baja", "valor_alta", "neto_valor"]
                headers = ["Comprobante", "Sucursal", "Fecha", "SKU(s) baja", "SKU(s) alta",
                          "Valor baja", "Valor alta", "Neto"]
            else:
                disp["unidades_baja"] = disp["unidades_baja"].apply(formatear_unidades)
                disp["unidades_alta"] = disp["unidades_alta"].apply(formatear_unidades)
                disp["neto_unidades"] = disp["neto_unidades"].apply(formatear_unidades)
                cols_mostrar = ["numero", "Sucursal", "fecha", "skus_baja", "skus_alta",
                                "unidades_baja", "unidades_alta", "neto_unidades"]
                headers = ["Comprobante", "Sucursal", "Fecha", "SKU(s) baja", "SKU(s) alta",
                          "Unidades baja", "Unidades alta", "Neto"]
            disp_show = disp[cols_mostrar].copy()
            disp_show.columns = headers
            st.dataframe(disp_show, width="stretch", hide_index=True, height=420)
            botones_descarga(pares, "pares_transformacion", "tr_pares")

    # ======================================================================
    # Detalle por SKU
    # ======================================================================
    with tab_sku:
        piv = (detalle.groupby(["codigo", "descripcion", "rubro", "marca", "gran_super_rubro",
                                "sentido"])
               .agg(unidades=("unidades", "sum"), valor=("valor", "sum"))
               .unstack("sentido", fill_value=0.0))
        piv.columns = [f"{c[0]}_{c[1].lower()}" for c in piv.columns]
        for c in ("unidades_baja", "valor_baja", "unidades_alta", "valor_alta"):
            if c not in piv.columns:
                piv[c] = 0.0
        piv = piv.reset_index()
        piv["neto_unidades"] = piv["unidades_alta"] - piv["unidades_baja"]
        piv["neto_valor"] = piv["valor_alta"] - piv["valor_baja"]
        piv = piv.sort_values("valor_baja" if vista == "Valorizado" else "unidades_baja",
                              ascending=False)

        disp = piv.copy()
        if vista == "Valorizado":
            cols_mostrar = ["codigo", "descripcion", "rubro", "marca", "valor_baja",
                            "valor_alta", "neto_valor"]
            headers = ["Código", "Descripción", "Rubro", "Marca", "Baja $", "Alta $", "Neto $"]
        else:
            cols_mostrar = ["codigo", "descripcion", "rubro", "marca", "unidades_baja",
                            "unidades_alta", "neto_unidades"]
            headers = ["Código", "Descripción", "Rubro", "Marca", "Baja u.", "Alta u.", "Neto u."]
        for c in cols_mostrar:
            if c.startswith(("valor_", "neto_valor")):
                disp[c] = disp[c].apply(formatear_pesos)
            elif c.startswith(("unidades_", "neto_unidades")):
                disp[c] = disp[c].apply(formatear_unidades)
        disp_show = disp[cols_mostrar].copy()
        disp_show.columns = headers
        st.dataframe(disp_show, width="stretch", hide_index=True, height=420)
        botones_descarga(piv, "transformaciones_por_sku", "tr_sku")

        if not piv.empty:
            top_baja_g = piv.nlargest(15, "valor_baja" if vista == "Valorizado" else "unidades_baja")
            fig = go.Figure()
            fig.add_bar(name="Dado de baja", y=top_baja_g["codigo"],
                       x=top_baja_g[col + "_baja"], orientation="h", marker_color="#ff6b6b")
            fig.add_bar(name="Alta (liquidación)", y=top_baja_g["codigo"],
                       x=top_baja_g[col + "_alta"], orientation="h", marker_color="#64ffda")
            fig.update_layout(
                barmode="group", height=max(340, 24 * len(top_baja_g)),
                title="Top 15 SKUs con más transformaciones",
                xaxis=dict(title=vista, gridcolor=color_grilla()),
                yaxis=dict(autorange="reversed", gridcolor=color_grilla()),
                **layout_grafico())
            st.plotly_chart(fig, width="stretch")

    # ======================================================================
    # Comparativa por sucursal
    # ======================================================================
    with tab_suc:
        agg_suc = (detalle.groupby(["codigodepo", "sentido"])
                  .agg(unidades=("unidades", "sum"), valor=("valor", "sum"))
                  .unstack("sentido", fill_value=0.0))
        agg_suc.columns = [f"{c[0]}_{c[1].lower()}" for c in agg_suc.columns]
        for c in ("unidades_baja", "valor_baja", "unidades_alta", "valor_alta"):
            if c not in agg_suc.columns:
                agg_suc[c] = 0.0
        agg_suc = agg_suc.reset_index()
        agg_suc["etiqueta"] = agg_suc["codigodepo"].map(suc_largo).fillna(agg_suc["codigodepo"])
        agg_suc = agg_suc.sort_values(col + "_baja", ascending=False)

        fig = go.Figure()
        fig.add_bar(name="Dado de baja", x=agg_suc["etiqueta"], y=agg_suc[col + "_baja"],
                   marker_color="#ff6b6b")
        fig.add_bar(name="Alta (liquidación)", x=agg_suc["etiqueta"], y=agg_suc[col + "_alta"],
                   marker_color="#64ffda")
        fig.update_layout(
            barmode="group", height=420,
            xaxis=dict(tickangle=-30, gridcolor=color_grilla()),
            yaxis=dict(title=vista, gridcolor=color_grilla()),
            **layout_grafico())
        st.plotly_chart(fig, width="stretch")

        disp = agg_suc.copy()
        disp["valor_baja"] = disp["valor_baja"].apply(formatear_pesos)
        disp["valor_alta"] = disp["valor_alta"].apply(formatear_pesos)
        disp["unidades_baja"] = disp["unidades_baja"].apply(formatear_unidades)
        disp["unidades_alta"] = disp["unidades_alta"].apply(formatear_unidades)
        disp = disp[["codigodepo", "etiqueta", "unidades_baja", "valor_baja",
                    "unidades_alta", "valor_alta"]]
        disp.columns = ["Código", "Sucursal", "Baja u.", "Baja $", "Alta u.", "Alta $"]
        st.dataframe(disp, width="stretch", hide_index=True)

    # --- Reporte imprimible --------------------------------------------------
    st.markdown("---")
    st.markdown("### Reporte imprimible")
    meta = {
        "proyecto":           proyecto,
        "desde":              args["fecha_desde"],
        "hasta":              args["fecha_hasta"],
        "valorizacion":       f"{args['modo_valorizacion']} — snapshot {fval or '—'}",
        "sucursales_filtro":  st.session_state.get("tr_suc_label", ""),
        "recorte":            st.session_state.get("tr_recorte_label", ""),
        "nombres_sucursal":   {c: v.split(" — ", 1)[-1] for c, v in suc_largo.items()},
    }
    pares_reporte = pares_transformacion(detalle)
    html_reporte = generar_reporte_transformaciones(proyecto, meta, detalle, pares_reporte)
    boton_reporte(html_reporte, f"transformaciones_{args['fecha_desde']}_{args['fecha_hasta']}.html",
                 "dl_reporte_tr")
