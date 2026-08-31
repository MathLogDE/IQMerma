"""ui/paginas/merma_analisis.py — Página Análisis (Detalle / Comparativa / Rubros)"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Análisis de merma")

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
        return

    suc_largo, _ = etiquetas_sucursal(sucursales)
    suc_sel = st.multiselect(
        "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
        format_func=lambda c: suc_largo.get(c, str(c)), key="analisis_sucs",
    )

    modo_periodo = st.radio(
        "Modo de período", ["Rango de fechas", "Corte dinámico por comprobante"],
        horizontal=True, key="analisis_modo_periodo",
        help="El corte dinámico calcula, para cada SKU, su propia fecha de "
             "inicio: la del N-ésimo comprobante de un tipo elegido (INV, "
             "NCB, RI, TR...) hacia atrás desde 'Hasta', sin contarlo.",
    )

    if modo_periodo == "Rango de fechas":
        ctrl = controles_periodo(proyecto, "analisis", modos=list(MODOS_VALORIZACION_MERMA))

        if ctrl and st.button("Calcular merma"):
            with st.spinner("Calculando..."):
                try:
                    depo = suc_sel or None
                    df = calcular_merma(proyecto, depo, **ctrl)
                    st.session_state["df_merma"] = df
                    st.session_state["cats_merma"] = df.attrs.get("categorias", [])
                    st.session_state["fval_merma"] = df.attrs.get("fecha_valorizacion")
                    st.session_state["skus_inv_merma"] = df.attrs.get("skus_inv")
                    st.session_state["pct_skus_merma"] = df.attrs.get("pct_skus_contados")
                    st.session_state["ctrl_merma"] = ctrl
                    st.session_state["suc_merma"] = suc_sel
                    st.session_state["suc_merma_label"] = (
                        etiqueta_seleccion_sucursales(suc_sel, suc_largo)
                    )
                    st.session_state["modo_periodo_calc"] = "fijo"
                    st.session_state["dinamico_meta"] = None
                except Exception as e:
                    st.error(f"Error: {e}")
    else:
        tipos_df = tipos_movimiento(proyecto)
        etiq_tipo = tipos_df.set_index("tipo")["categoria"]
        fmin, fmax = rango_disponible(proyecto)
        snapshots = listar_fechas_valorizacion(proyecto)

        dc1, dc2, dc3 = st.columns(3)
        with dc1:
            tipo_din = st.selectbox(
                "Tipo de comprobante", tipos_df["tipo"].tolist(),
                format_func=lambda t: f"{t} — {etiq_tipo.get(t, '')}",
                key="analisis_din_tipo")
        with dc2:
            cantidad_din = st.number_input(
                "Cantidad hacia atrás", min_value=1, step=1, value=1,
                key="analisis_din_cant",
                help="1 = el comprobante más reciente de ese tipo antes de 'Hasta'.")
        with dc3:
            fecha_hasta_din = st.date_input(
                "Hasta", value=fmax, min_value=fmin, max_value=fmax,
                key="analisis_din_hasta")

        dc4, dc5 = st.columns(2)
        with dc4:
            modo_din = st.selectbox(
                "Valorización", list(MODOS_VALORIZACION_MERMA),
                format_func=lambda x: ETIQUETAS_MODO_VALORIZACION.get(x, x),
                key="analisis_din_modo")
        with dc5:
            if snapshots:
                fval_din = st.selectbox(
                    "Valorizar al (snapshot)", snapshots, key="analisis_din_fval",
                    help="Fecha del stock con la que se valoriza. Default: el más reciente.")
            else:
                fval_din = None
                st.warning("Sin snapshots de stock — no se puede valorizar.")

        if st.button("Calcular merma", key="analisis_din_btn"):
            with st.spinner("Calculando..."):
                try:
                    depo = suc_sel or None
                    df = calcular_merma_dinamica(
                        proyecto, tipo_din, int(cantidad_din), str(fecha_hasta_din),
                        codigodepo=depo, modo_valorizacion=modo_din,
                        fecha_valorizacion=fval_din)
                    st.session_state["df_merma"] = df
                    st.session_state["cats_merma"] = df.attrs.get("categorias", [])
                    st.session_state["fval_merma"] = df.attrs.get("fecha_valorizacion")
                    st.session_state["skus_inv_merma"] = df.attrs.get("skus_inv")
                    st.session_state["pct_skus_merma"] = df.attrs.get("pct_skus_contados")
                    st.session_state["ctrl_merma"] = None
                    st.session_state["suc_merma"] = suc_sel
                    st.session_state["suc_merma_label"] = (
                        etiqueta_seleccion_sucursales(suc_sel, suc_largo)
                    )
                    st.session_state["modo_periodo_calc"] = "dinamico"
                    st.session_state["dinamico_meta"] = {
                        "tipo": tipo_din, "cantidad": int(cantidad_din),
                        "fecha_hasta": str(fecha_hasta_din),
                        "skus_excluidos": df.attrs.get("skus_excluidos", 0),
                        "pares_totales": df.attrs.get("pares_totales", 0),
                        "pares_excluidos": df.attrs.get("pares_excluidos", 0),
                        "modo_valorizacion": modo_din,
                    }
                except Exception as e:
                    st.error(f"Error: {e}")

    if "df_merma" not in st.session_state:
        return

    df_full = st.session_state["df_merma"]
    cats = st.session_state.get("cats_merma", [])
    fval = st.session_state.get("fval_merma")
    skus_inv = st.session_state.get("skus_inv_merma")
    pct_skus_contados = st.session_state.get("pct_skus_merma")
    ctrl_calc = st.session_state.get("ctrl_merma")
    modo_periodo_calc = st.session_state.get("modo_periodo_calc", "fijo")
    dinamico_meta = st.session_state.get("dinamico_meta")

    st.markdown("---")

    # --- Filtros sobre el resultado (sin recalcular) ------------------------
    st.markdown("#### Filtros")
    df, _ = filtros_resultado(df_full, "analisis_f")

    fecha_corte_moda = None
    if modo_periodo_calc == "dinamico" and dinamico_meta and not df.empty:
        suc_moda = st.session_state.get("suc_merma") or None
        fecha_corte_moda = fecha_corte_moda_cacheada(
            proyecto, dinamico_meta["tipo"], dinamico_meta["cantidad"],
            dinamico_meta["fecha_hasta"],
            tuple(sorted(suc_moda)) if suc_moda else None,
            tuple(sorted(df["codigo"])),
        )

    # --- KPIs ----------------------------------------------------------------
    merma_total   = df["merma_total_valorizada"].sum()
    merma_total_u = df["merma_total_unidades"].sum()
    venta_total   = df["venta_neta"].sum()
    pct_total = (merma_total / venta_total * 100) if venta_total > 0 else None
    skus_con_merma = int((df["merma_total_valorizada"] > 0).sum())

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("SKUs analizados", f"{len(df):,}")
    col2.metric("SKUs con merma", f"{skus_con_merma:,}")
    col3.metric("Merma total", formatear_pesos(merma_total),
                delta=f"{merma_total_u:,.0f} unidades", delta_color="off")
    col4.metric("Venta total período", formatear_pesos(venta_total))
    col5.metric("% Merma s/ventas", formatear_pct(pct_total))
    col6.metric("% SKUs contados", formatear_pct(pct_skus_contados),
                delta=(f"{skus_inv:,} de {len(df_full):,} SKUs"
                       if skus_inv is not None else None),
                delta_color="off",
                help="SKUs con conteo de inventario físico (INV) en el "
                     "período, sobre el total de SKUs con movimientos. No "
                     "se recalcula con los filtros de abajo.")
    if fval:
        st.caption(f"Valorizado con snapshot de stock del **{fval}**")
    if modo_periodo_calc == "dinamico" and dinamico_meta:
        excl = dinamico_meta.get("skus_excluidos", 0)
        pares_excl = dinamico_meta.get("pares_excluidos", 0)
        pares_tot = dinamico_meta.get("pares_totales", 0)
        moda_txt = (f" Fecha de corte más frecuente: **{fecha_corte_moda}**."
                   if fecha_corte_moda else "")
        st.caption(
            f"Corte dinámico: **{dinamico_meta['cantidad']}º comprobante {dinamico_meta['tipo']}** "
            f"hacia atrás, hasta **{dinamico_meta['fecha_hasta']}** — el corte se calcula "
            "aislado por SKU **y por sucursal** (cada sucursal retrocede sobre su propio "
            "historial)." + moda_txt + " "
            + (f"⚠️ {excl:,} SKUs excluidos: ninguna sucursal alcanzó "
               f"{dinamico_meta['cantidad']} comprobantes tipo {dinamico_meta['tipo']}. "
               if excl else "")
            + (f"ℹ️ {pares_excl:,} de {pares_tot:,} combinaciones SKU+sucursal no "
               "alcanzaron la condición (esos SKUs igual aparecen si otra sucursal sí la "
               "alcanzó, con datos parciales)."
               if pares_excl else "")
        )

    merma_cats = [c for c in categorias_merma(proyecto) if c in df.columns]

    tab_det, tab_suc, tab_rub = st.tabs(
        ["📋 Detalle por SKU", "🏢 Comparativa por sucursal", "📊 Pareto por rubro"])

    # ======================================================================
    # Detalle por SKU + dashboard
    # ======================================================================
    with tab_det:
        vista = st.radio("Mostrar", ["Valorizado", "Unidades", "Ambos"],
                         horizontal=True, key="analisis_vista")

        cols_base = ["codigo", "descripcion", "rubro"]
        headers   = ["Código", "Descripción", "Rubro"]
        if modo_periodo_calc == "dinamico":
            df["corte_desde_fmt"] = df.apply(
                lambda r: str(r["fecha_desde_min"]) if r["fecha_desde_min"] == r["fecha_desde_max"]
                else f"{r['fecha_desde_min']} – {r['fecha_desde_max']}", axis=1)
            cols_base += ["corte_desde_fmt", "n_sucursales_incluidas", "n_sucursales_excluidas"]
            headers   += ["Corte desde", "Sucursales OK", "Sucursales excl."]
        cols_mostrar, formato = [], {}
        for c in cats:
            if vista in ("Valorizado", "Ambos"):
                cols_mostrar.append(c); headers.append(f"{c} $")
                formato[c] = formatear_pesos
            if vista in ("Unidades", "Ambos"):
                cu = f"{c} (u)"
                cols_mostrar.append(cu); headers.append(f"{c} u.")
                formato[cu] = formatear_unidades
        for c, h, f in [
            ("merma_total_valorizada", "Merma Total $", formatear_pesos),
            ("merma_total_unidades",   "Merma Total u.", formatear_unidades),
            ("venta_neta",             "Venta $",        formatear_pesos),
            ("pct_merma_sobre_ventas", "% Merma",        formatear_pct),
        ]:
            cols_mostrar.append(c); headers.append(h); formato[c] = f

        df_display = df[cols_base + cols_mostrar].copy()
        for c, f in formato.items():
            df_display[c] = df_display[c].apply(f)
        df_display.columns = headers
        st.dataframe(df_display, width="stretch", hide_index=True)
        botones_descarga(df[cols_base + cols_mostrar], "detalle_merma_sku", "det")

        if merma_cats and merma_total > 0:
            g1, g2 = st.columns([1, 2])
            with g1:
                st.markdown("#### Composición de la merma")
                comp = {c: merma_por_categoria(df, c).sum() for c in merma_cats}
                # aporte con signo: un faltante suma, un ajuste positivo resta
                fig = go.Figure(go.Bar(
                    x=list(comp.values()), y=list(comp.keys()), orientation="h",
                    marker_color=["#ff6b6b" if v >= 0 else "#64ffda" for v in comp.values()],
                    text=[formatear_pesos(v) for v in comp.values()],
                    textposition="auto"))
                fig.update_layout(
                    height=380, showlegend=False,
                    xaxis=dict(title="Aporte a la merma $", gridcolor=color_grilla()),
                    yaxis=dict(autorange="reversed"), **layout_grafico())
                st.plotly_chart(fig, width="stretch")
            with g2:
                st.markdown("#### Top 15 SKUs por merma")
                df_top = df.nlargest(15, "merma_total_valorizada").copy()
                df_top["label"] = df_top["codigo"] + " - " + df_top["descripcion"].fillna("").str.slice(0, 30)
                fig = go.Figure()
                for i, cat in enumerate(merma_cats):
                    fig.add_bar(name=cat, x=df_top["label"],
                                y=merma_por_categoria(df_top, cat),
                                marker_color=PALETA[i % len(PALETA)])
                fig.update_layout(barmode="stack", height=380,
                                  xaxis=dict(tickangle=-45, gridcolor=color_grilla()),
                                  yaxis=dict(gridcolor=color_grilla()),
                                  margin=dict(b=120), **layout_grafico())
                st.plotly_chart(fig, width="stretch")

            nivel_dash = ("gran_super_rubro" if df["gran_super_rubro"].notna().any()
                          else "rubro")
            st.markdown(f"#### Merma vs venta por {'gran super rubro' if nivel_dash == 'gran_super_rubro' else 'rubro'}")
            df_gr = (df.groupby(nivel_dash, dropna=False)
                     .agg(merma=("merma_total_valorizada", "sum"),
                          venta=("venta_neta", "sum"))
                     .reset_index().sort_values("merma", ascending=False).head(12))
            df_gr[nivel_dash] = df_gr[nivel_dash].fillna("Sin clasificar")
            df_gr["pct"] = (df_gr["merma"] / df_gr["venta"] * 100).where(df_gr["venta"] > 0)
            fig = go.Figure()
            fig.add_bar(name="Merma $", x=df_gr[nivel_dash], y=df_gr["merma"],
                        marker_color="#ff6b6b", yaxis="y1")
            fig.add_scatter(name="% Merma s/venta", x=df_gr[nivel_dash], y=df_gr["pct"],
                            mode="lines+markers", line=dict(color="#64ffda", width=2),
                            marker=dict(size=7), yaxis="y2")
            fig.update_layout(height=420,
                              xaxis=dict(tickangle=-30, gridcolor=color_grilla()),
                              yaxis=dict(title="Merma $", gridcolor=color_grilla()),
                              yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                          gridcolor=color_grilla(), ticksuffix="%"),
                              margin=dict(b=120), **layout_grafico())
            st.plotly_chart(fig, width="stretch")

        st.markdown("---")
        st.markdown("#### Ver movimientos de un SKU")
        if df.empty:
            st.info("Sin SKUs con los filtros aplicados.")
        else:
            opciones_sku = df["codigo"].tolist()
            etiqueta_sku = dict(zip(df["codigo"], df["descripcion"].fillna("")))
            sku_det = st.selectbox(
                "SKU", opciones_sku,
                format_func=lambda c: f"{c} — {str(etiqueta_sku.get(c, ''))[:60]}",
                key="analisis_det_sku")

            if modo_periodo_calc == "dinamico" and dinamico_meta:
                cortes_sku = fechas_corte_comprobante(
                    proyecto, dinamico_meta["tipo"], dinamico_meta["cantidad"],
                    dinamico_meta["fecha_hasta"],
                    codigodepo=st.session_state.get("suc_merma") or None,
                    codigos=[sku_det])
                st.caption(
                    f"Corte por sucursal para **{sku_det}** — tipo "
                    f"**{dinamico_meta['tipo']}** x**{dinamico_meta['cantidad']}**, "
                    f"cada sucursal retrocede sobre su propio historial:")

                disp_cortes = cortes_sku.copy()
                disp_cortes["Sucursal"] = (
                    disp_cortes["codigodepo"].map(suc_largo).fillna(disp_cortes["codigodepo"]))
                disp_cortes["Incluida"] = disp_cortes["suficientes"].map(
                    {True: "Sí", False: "No (comprobantes insuficientes)"})
                disp_cortes = disp_cortes[
                    ["Sucursal", "fecha_corte", "n_disponibles", "Incluida"]]
                disp_cortes.columns = ["Sucursal", "Corte (excluido)",
                                       "Comprobantes disp.", "Incluida"]
                st.dataframe(disp_cortes, width="stretch", hide_index=True)

                partes = []
                for _, fila in cortes_sku[cortes_sku["suficientes"]].iterrows():
                    f_desde_suc = str(
                        (pd.to_datetime(fila["fecha_corte"]) + pd.Timedelta(days=1)).date())
                    partes.append(movimientos_detalle(
                        proyecto, sku_det, f_desde_suc, dinamico_meta["fecha_hasta"],
                        codigodepo=fila["codigodepo"]))
                mov_det = (pd.concat(partes, ignore_index=True) if partes
                          else pd.DataFrame(columns=["fecha", "tipomov", "tipo", "numero",
                                                     "codigodepo", "diferencia", "ingreso",
                                                     "egreso", "usuario"]))
                if mov_det.empty:
                    st.info("Sin movimientos en las ventanas de las sucursales incluidas.")
                else:
                    disp_mov = mov_det.copy()
                    disp_mov.columns = ["Fecha", "Tipo mov.", "Tipo", "Nro. comprobante",
                                        "Sucursal", "Diferencia", "Ingreso", "Egreso", "Usuario"]
                    st.dataframe(disp_mov, width="stretch", hide_index=True, height=280)
                    botones_descarga(mov_det, f"movimientos_{sku_det}", "det_mov")
            elif modo_periodo_calc == "fijo":
                f_desde_det = ctrl_calc["fecha_desde"] if ctrl_calc else None
                f_hasta_det = ctrl_calc["fecha_hasta"] if ctrl_calc else None
                if f_desde_det and f_hasta_det:
                    mov_det = movimientos_detalle(
                        proyecto, sku_det, f_desde_det, f_hasta_det,
                        codigodepo=st.session_state.get("suc_merma") or None)
                    st.caption(f"Movimientos entre **{f_desde_det}** y **{f_hasta_det}**.")
                    if mov_det.empty:
                        st.info("Sin movimientos en esa ventana.")
                    else:
                        disp_mov = mov_det.copy()
                        disp_mov.columns = ["Fecha", "Tipo mov.", "Tipo", "Nro. comprobante",
                                            "Sucursal", "Diferencia", "Ingreso", "Egreso", "Usuario"]
                        st.dataframe(disp_mov, width="stretch", hide_index=True, height=280)
                        botones_descarga(mov_det, f"movimientos_{sku_det}", "det_mov")

    # ======================================================================
    # Comparativa por sucursal
    # ======================================================================
    df_suc = None
    with tab_suc:
        suc_merma_calc = st.session_state.get("suc_merma", [])
        if len(suc_merma_calc) == 1:
            st.info("Para comparar entre sucursales, elegí **más de una** "
                    "(o dejá vacío para todas) arriba y volvé a calcular.")
        elif modo_periodo_calc == "dinamico" and dinamico_meta:
            filtrado = len(df) != len(df_full)
            codigos_t = tuple(sorted(df["codigo"])) if filtrado else None
            depo_t = tuple(sorted(suc_merma_calc)) if suc_merma_calc else None
            with st.spinner("Armando comparativa..."):
                df_suc = comparativa_dinamica_cacheada(
                    proyecto, dinamico_meta["tipo"], dinamico_meta["cantidad"],
                    dinamico_meta["fecha_hasta"],
                    dinamico_meta.get("modo_valorizacion", "costo"), fval,
                    codigos_t, depo_t)
            st.caption(
                "Cada sucursal usa la misma ventana dinámica que el detalle "
                "por SKU (corte propio por SKU, respetando la sucursal)."
                + (" La comparativa respeta los filtros aplicados arriba." if filtrado else "")
            )
            sin_log = st.checkbox(
                "Excluir depósitos logísticos (CD)", value=True, key="comp_sinlog",
                help="Los CD no venden: distorsionan el % de merma. "
                     "Marcalos en Ingesta → Depósitos.")
            if sin_log and "es_logistica" in df_suc.columns:
                df_suc = df_suc[~df_suc["es_logistica"].fillna(False)]
            render_comparativa(df_suc, merma_cats, "comp")
        elif modo_periodo_calc == "fijo" and ctrl_calc:
            filtrado = len(df) != len(df_full)
            codigos_t = tuple(sorted(df["codigo"])) if filtrado else None
            depo_t = tuple(sorted(suc_merma_calc)) if suc_merma_calc else None
            with st.spinner("Armando comparativa..."):
                df_suc = comparativa_cacheada(
                    proyecto, ctrl_calc["fecha_desde"], ctrl_calc["fecha_hasta"],
                    ctrl_calc["modo_valorizacion"], ctrl_calc["fecha_valorizacion"],
                    codigos_t, depo_t)
            if filtrado:
                st.caption("La comparativa respeta los filtros aplicados arriba.")
            sin_log = st.checkbox(
                "Excluir depósitos logísticos (CD)", value=True, key="comp_sinlog",
                help="Los CD no venden: distorsionan el % de merma. "
                     "Marcalos en Ingesta → Depósitos.")
            if sin_log and "es_logistica" in df_suc.columns:
                df_suc = df_suc[~df_suc["es_logistica"].fillna(False)]
            render_comparativa(df_suc, merma_cats, "comp")

    # ======================================================================
    # Pareto por rubro
    # ======================================================================
    with tab_rub:
        nivel = st.selectbox(
            "Nivel de agrupación", ["rubro", "super_rubro", "gran_super_rubro"],
            format_func=lambda x: {"rubro": "Rubro", "super_rubro": "Super Rubro",
                                   "gran_super_rubro": "Gran Super Rubro"}[x],
            key="rubro_nivel")
        df_rubro = (df.groupby(nivel, dropna=False)
                    .agg(merma_total=("merma_total_valorizada", "sum"),
                         venta_total=("venta_neta", "sum"),
                         skus=("codigo", "count"))
                    .reset_index().sort_values("merma_total", ascending=False))
        df_rubro["pct_merma"] = (df_rubro["merma_total"] / df_rubro["venta_total"] * 100
                                 ).where(df_rubro["venta_total"] > 0)
        tot = df_rubro["merma_total"].sum()
        df_rubro["pct_acumulado"] = (df_rubro["merma_total"].cumsum() / tot * 100
                                     ) if tot > 0 else 0.0

        fig = go.Figure()
        fig.add_bar(x=df_rubro[nivel].fillna("Sin rubro"), y=df_rubro["merma_total"],
                    name="Merma $", marker_color="#ff6b6b", yaxis="y1")
        fig.add_scatter(x=df_rubro[nivel].fillna("Sin rubro"), y=df_rubro["pct_acumulado"],
                        name="% Acumulado", mode="lines+markers",
                        line=dict(color="#64ffda", width=2), marker=dict(size=6), yaxis="y2")
        fig.update_layout(height=500,
                          xaxis=dict(tickangle=-45, gridcolor=color_grilla()),
                          yaxis=dict(title="Merma $", gridcolor=color_grilla()),
                          yaxis2=dict(title="% Acumulado", overlaying="y", side="right",
                                      range=[0, 110], gridcolor=color_grilla(), ticksuffix="%"),
                          margin=dict(b=160), **layout_grafico())
        st.plotly_chart(fig, width="stretch")

        df_disp = df_rubro.copy()
        df_disp["merma_total"] = df_disp["merma_total"].apply(formatear_pesos)
        df_disp["venta_total"] = df_disp["venta_total"].apply(formatear_pesos)
        df_disp["pct_merma"]   = df_disp["pct_merma"].apply(formatear_pct)
        df_disp["pct_acumulado"] = df_disp["pct_acumulado"].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "—")
        df_disp.columns = [nivel.replace("_", " ").title(), "Merma $", "Venta $",
                           "SKUs", "% Merma", "% Acum."]
        st.dataframe(df_disp, width="stretch", hide_index=True)

    # --- Reporte imprimible --------------------------------------------------
    st.markdown("---")
    st.markdown("### Reporte imprimible")
    filtros_txt = []
    if st.session_state.get("analisis_f_gran_super_rubro"):
        filtros_txt.append("GSR: " + ", ".join(st.session_state["analisis_f_gran_super_rubro"]))
    if st.session_state.get("analisis_f_rubro"):
        filtros_txt.append("Rubro: " + ", ".join(st.session_state["analisis_f_rubro"]))
    if st.session_state.get("analisis_f_marca"):
        filtros_txt.append("Marca: " + ", ".join(st.session_state["analisis_f_marca"]))
    if st.session_state.get("analisis_f_txt"):
        filtros_txt.append(f"Búsqueda: '{st.session_state['analisis_f_txt']}'")
    if modo_periodo_calc == "dinamico" and dinamico_meta:
        fecha_desde_meta = (f"Corte dinámico: {dinamico_meta['cantidad']}º "
                            f"comprobante {dinamico_meta['tipo']}")
        fecha_hasta_meta = dinamico_meta["fecha_hasta"]
    else:
        fecha_desde_meta = ctrl_calc["fecha_desde"] if ctrl_calc else ""
        fecha_hasta_meta = ctrl_calc["fecha_hasta"] if ctrl_calc else ""
    if modo_periodo_calc == "dinamico" and dinamico_meta:
        modo_meta = dinamico_meta.get("modo_valorizacion", "costo")
    else:
        modo_meta = ctrl_calc["modo_valorizacion"] if ctrl_calc else "costo"

    rc1, rc2 = st.columns(2)
    with rc1:
        nivel_reporte = st.selectbox(
            "Nivel de agrupación (imprimible)", ["rubro", "super_rubro", "gran_super_rubro"],
            index=["rubro", "super_rubro", "gran_super_rubro"].index(
                st.session_state.get("rubro_nivel", "rubro")),
            format_func=lambda x: {"rubro": "Rubro", "super_rubro": "Super Rubro",
                                   "gran_super_rubro": "Gran Super Rubro"}[x],
            key="reporte_nivel",
            help="Nivel de la sección 'Merma por X' del imprimible. Por defecto, el "
                 "mismo que la pestaña Pareto por rubro.")
    with rc2:
        vista_reporte = st.radio(
            "Vista (imprimible)", ["Valorizado", "Unidades", "Ambos"],
            index=["Valorizado", "Unidades", "Ambos"].index(
                st.session_state.get("analisis_vista", "Ambos")),
            horizontal=True, key="reporte_vista",
            help="Qué columnas mostrar en las tablas del imprimible. Por defecto, la "
                 "misma vista que la pestaña Detalle por SKU.")

    meta = {
        "proyecto":           proyecto,
        "sucursal":           st.session_state.get("suc_merma_label", ""),
        "fecha_desde":        fecha_desde_meta,
        "fecha_hasta":        fecha_hasta_meta,
        "modo":               modo_meta,
        "fecha_valorizacion": fval or "—",
        "filtros":            " · ".join(filtros_txt),
        "fecha_corte_moda":   fecha_corte_moda,
    }
    html_reporte = generar_reporte(df, meta, merma_cats, df_sucursales=df_suc,
                                   skus_inv=skus_inv, skus_total=len(df_full),
                                   pct_skus_contados=pct_skus_contados,
                                   nivel=nivel_reporte, vista=vista_reporte.lower())
    if modo_periodo_calc == "dinamico" and dinamico_meta:
        nombre_reporte = (f"reporte_merma_dinamico_{dinamico_meta['tipo']}x"
                          f"{dinamico_meta['cantidad']}_{dinamico_meta['fecha_hasta']}.html")
    else:
        nombre_reporte = f"reporte_merma_{meta['fecha_desde']}_{meta['fecha_hasta']}.html"
    boton_reporte(html_reporte, nombre_reporte, "dl_reporte")
