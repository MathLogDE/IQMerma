"""ui/paginas/comercial_comprobantes.py — Comprobantes y canasta"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

# Un color por año, del más viejo (apagado) al más nuevo (destacado) — mismo
# criterio que ui/paginas/inventario_interanual.py.
COLORES_ANIO = ["#5c6b8a", "#748ffc", "#a9e34b", "#ffa94d", "#64ffda", "#f783ac"]


def _color(i, total):
    return COLORES_ANIO[(len(COLORES_ANIO) - total + i) % len(COLORES_ANIO)]


def _agregar_ponderado(resumen: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """
    Agrupa resumen_comprobantes por group_cols, ponderando referencias/
    unidades promedio por cantidad_comprobantes — promediar promedios sin
    pesar subestimaría los grupos con más volumen.
    """
    r = resumen.copy()
    r["referencias_x_cant"] = r["referencias_prom"] * r["cantidad_comprobantes"]
    r["unidades_x_cant"] = r["unidades_prom"] * r["cantidad_comprobantes"]
    agg = r.groupby(group_cols).agg(
        cantidad_comprobantes=("cantidad_comprobantes", "sum"),
        referencias_x_cant=("referencias_x_cant", "sum"),
        unidades_x_cant=("unidades_x_cant", "sum"),
        valor_total=("valor_total", "sum"),
    ).reset_index()
    agg["referencias_prom"] = agg["referencias_x_cant"] / agg["cantidad_comprobantes"]
    agg["unidades_prom"] = agg["unidades_x_cant"] / agg["cantidad_comprobantes"]
    agg["valor_prom"] = agg["valor_total"] / agg["cantidad_comprobantes"]
    return agg.sort_values("cantidad_comprobantes", ascending=False).drop(
        columns=["referencias_x_cant", "unidades_x_cant"])


def _agregar_por_sucursal(resumen: pd.DataFrame) -> pd.DataFrame:
    return _agregar_ponderado(resumen, ["codigodepo"])


def _agregar_por_tipo(resumen: pd.DataFrame) -> pd.DataFrame:
    return _agregar_ponderado(resumen, ["tipo", "categoria"])


def render(proyecto):
    st.title("Comprobantes y canasta")
    st.caption(
        "Comparativa entre sucursales a nivel de **comprobante** (no de "
        "movimiento): cuántas facturas / notas / remitos emite cada boca, de "
        "qué tipo, y cuánto factura en promedio cada uno. Más abajo, el "
        "perfil de cada producto dentro del comprobante — en qué cantidad se "
        "suele vender, y qué otros productos suele acompañar (canasta)."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados.")
        return
    suc_largo, suc_corto = etiquetas_sucursal(sucursales)

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    def corto_suc(c):
        return suc_corto.get(c, str(c))

    controles = controles_periodo(proyecto, "cp")
    if controles is None:
        return

    tipos_cat = tipos_disponibles(proyecto)
    tipos_map = dict(zip(tipos_cat["tipo"], tipos_cat["categoria"]))

    with st.expander("Filtrar por sucursal / rubro / marca / SKU / tipo de comprobante"):
        sucs = st.multiselect("Sucursales (vacío = todas)",
                              sucursales["codigodepo"].tolist(),
                              format_func=etiqueta_suc, key="cp_sucs")
        tipos_sel = st.multiselect(
            "Tipos de comprobante (vacío = todos)", tipos_cat["tipo"].tolist(),
            format_func=lambda t: f"{t} — {tipos_map.get(t, '')}", key="cp_tipos")
        cat = catalogo_cacheado(proyecto)
        recorte, hay_filtro = filtros_catalogo(cat, "cp_cat", con_activo=True)
        if hay_filtro:
            st.caption(f"{len(recorte):,} SKUs en el recorte.")
    codigos = recorte["codigo"].tolist() if hay_filtro else None
    tipos = tipos_sel or None

    if st.button("Calcular comprobantes"):
        with st.spinner("Reconstruyendo comprobantes..."):
            try:
                st.session_state["cp_resumen"] = resumen_comprobantes(
                    proyecto, controles["fecha_desde"], controles["fecha_hasta"],
                    sucursales=sucs or None, codigos=codigos, tipos=tipos,
                    modo_valorizacion=controles["modo_valorizacion"],
                    fecha_valorizacion=controles["fecha_valorizacion"])
                st.session_state["cp_args"] = dict(controles)
                # Los cálculos posteriores (perfil, afinidad) son "on demand" y
                # quedan viejos si se cambia el recorte de arriba.
                st.session_state.pop("cp_perfil", None)
                st.session_state.pop("cp_afinidad", None)
            except Exception as e:
                st.error(f"Error: {e}")

    if "cp_resumen" not in st.session_state:
        return

    resumen = st.session_state["cp_resumen"]
    args = st.session_state["cp_args"]
    if resumen.empty:
        st.info("Sin comprobantes para este recorte de fechas/filtros.")
        return

    st.markdown("---")

    # --- KPIs ------------------------------------------------------------
    total_comp = int(resumen["cantidad_comprobantes"].sum())
    total_valor = float(resumen["valor_total"].sum())
    ticket_prom = total_valor / total_comp if total_comp else 0.0
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Comprobantes", formatear_unidades(total_comp))
    k2.metric("Ticket promedio", formatear_pesos(ticket_prom))
    k3.metric("Sucursales", int(resumen["codigodepo"].nunique()))
    k4.metric("Tipos de comprobante", int(resumen["tipo"].nunique()))
    st.caption(
        f"Período {args['fecha_desde']} a {args['fecha_hasta']} · precios "
        f"constantes a {resumen.attrs['modo_valorizacion']} del snapshot "
        f"{resumen.attrs['fecha_valorizacion']}.")

    # --- Comparativa por sucursal ------------------------------------------
    st.markdown("#### Comparativa por sucursal")
    agg_suc = _agregar_por_sucursal(resumen)
    agg_suc["etiqueta"] = agg_suc["codigodepo"].map(corto_suc)

    fig = go.Figure()
    fig.add_bar(name="Comprobantes", x=agg_suc["etiqueta"],
                y=agg_suc["cantidad_comprobantes"], marker_color="#748ffc")
    fig.add_scatter(name="Ticket promedio", x=agg_suc["etiqueta"],
                    y=agg_suc["valor_prom"], mode="lines+markers",
                    line=dict(color="#64ffda", width=2), marker=dict(size=7),
                    yaxis="y2")
    fig.update_layout(
        height=430, xaxis=dict(tickangle=-30, gridcolor=color_grilla()),
        yaxis=dict(title="Cantidad de comprobantes", gridcolor=color_grilla()),
        yaxis2=dict(title="Ticket promedio $", overlaying="y", side="right",
                    gridcolor=color_grilla(), showgrid=False),
        **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    disp_suc = agg_suc.copy()
    disp_suc["cantidad_comprobantes"] = disp_suc["cantidad_comprobantes"].apply(formatear_unidades)
    disp_suc["referencias_prom"] = disp_suc["referencias_prom"].round(1)
    disp_suc["unidades_prom"] = disp_suc["unidades_prom"].round(1)
    disp_suc["valor_prom"] = disp_suc["valor_prom"].apply(formatear_pesos)
    disp_suc["valor_total"] = disp_suc["valor_total"].apply(formatear_pesos)
    disp_suc = disp_suc[["codigodepo", "etiqueta", "cantidad_comprobantes",
                         "referencias_prom", "unidades_prom", "valor_prom", "valor_total"]]
    disp_suc.columns = ["Código", "Sucursal", "Comprobantes", "Referencias prom.",
                        "Unidades prom.", "Ticket promedio", "Total $"]
    st.dataframe(disp_suc, width="stretch", hide_index=True)
    botones_descarga(agg_suc, "comprobantes_por_sucursal", "cp_suc")

    # --- Por tipo de comprobante ---------------------------------------------
    st.markdown("#### Por tipo de comprobante")
    agg_tipo = _agregar_por_tipo(resumen)
    fig = go.Figure()
    fig.add_bar(x=agg_tipo["tipo"], y=agg_tipo["cantidad_comprobantes"],
                marker_color=[PALETA[i % len(PALETA)] for i in range(len(agg_tipo))])
    fig.update_layout(
        height=340, showlegend=False,
        xaxis=dict(title="Tipo", gridcolor=color_grilla()),
        yaxis=dict(title="Cantidad de comprobantes", gridcolor=color_grilla()),
        **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    disp_tipo = agg_tipo.copy()
    disp_tipo["cantidad_comprobantes"] = disp_tipo["cantidad_comprobantes"].apply(formatear_unidades)
    disp_tipo["referencias_prom"] = disp_tipo["referencias_prom"].round(1)
    disp_tipo["unidades_prom"] = disp_tipo["unidades_prom"].round(1)
    disp_tipo["valor_total"] = disp_tipo["valor_total"].apply(formatear_pesos)
    disp_tipo["valor_prom"] = disp_tipo["valor_prom"].apply(formatear_pesos)
    disp_tipo = disp_tipo[["tipo", "categoria", "cantidad_comprobantes", "referencias_prom",
                           "unidades_prom", "valor_prom", "valor_total"]]
    disp_tipo.columns = ["Tipo", "Categoría", "Comprobantes", "Referencias prom.",
                         "Unidades prom.", "Ticket promedio", "Total $"]
    st.dataframe(disp_tipo, width="stretch", hide_index=True)

    # --- Evolución mensual / interanual --------------------------------------
    st.markdown("---")
    st.markdown("#### Evolución mensual / interanual")
    anios_disp = anios_disponibles(proyecto)
    anios_sel = []
    if not anios_disp:
        st.info("No hay años con movimientos cargados.")
    else:
        e1, e2, e3 = st.columns([2, 1, 1])
        with e1:
            anios_sel = st.multiselect(
                "Años", anios_disp,
                default=anios_disp[:2] if len(anios_disp) >= 2 else anios_disp,
                key="cp_anios")
        with e2:
            metrica_ev = st.selectbox(
                "Métrica", list(METRICAS_COMPROBANTE), key="cp_metrica",
                format_func=lambda m: METRICAS_COMPROBANTE[m]["etiqueta"])
        with e3:
            abrir_suc = st.checkbox("Abrir por sucursal", value=False, key="cp_abrir_suc")

        if not anios_sel:
            st.warning("Elegí al menos un año.")
        else:
            sucs_ev = sucs or sucursales["codigodepo"].tolist()
            with st.spinner("Calculando evolución..."):
                if abrir_suc:
                    serie_ev = evolucion_comprobantes_sucursales(
                        proyecto, anios_sel, sucs_ev, metrica=metrica_ev,
                        codigos=codigos, tipos=tipos,
                        modo_valorizacion=args["modo_valorizacion"],
                        fecha_valorizacion=args["fecha_valorizacion"])
                else:
                    serie_ev = evolucion_comprobantes(
                        proyecto, anios_sel, metrica=metrica_ev,
                        sucursales=sucs or None, codigos=codigos, tipos=tipos,
                        modo_valorizacion=args["modo_valorizacion"],
                        fecha_valorizacion=args["fecha_valorizacion"])

            es_pesos = METRICAS_COMPROBANTE[metrica_ev]["valorizada"]
            fmt = formatear_pesos if es_pesos else formatear_unidades
            anios_ord = sorted(anios_sel)

            fig = go.Figure()
            if abrir_suc:
                for i, dep in enumerate(sucs_ev):
                    for j, anio in enumerate(anios_ord):
                        d = serie_ev[(serie_ev["codigodepo"] == dep) & (serie_ev["anio"] == anio)]
                        if d.empty:
                            continue
                        fig.add_scatter(
                            x=d["mes_nombre"], y=d["valor"], mode="lines+markers",
                            name=f"{corto_suc(dep)} · {anio}",
                            line=dict(color=COLORES_ANIO[i % len(COLORES_ANIO)], width=2,
                                      dash="solid" if j == len(anios_ord) - 1 else "dot"),
                            marker=dict(size=6))
            else:
                for i, anio in enumerate(anios_ord):
                    d = serie_ev[serie_ev["anio"] == anio]
                    fig.add_scatter(
                        x=d["mes_nombre"], y=d["valor"], mode="lines+markers",
                        name=str(anio), line=dict(color=_color(i, len(anios_ord)), width=2),
                        marker=dict(size=7))
            fig.update_layout(
                height=380, xaxis=dict(gridcolor=color_grilla()),
                yaxis=dict(title=METRICAS_COMPROBANTE[metrica_ev]["etiqueta"],
                          gridcolor=color_grilla()),
                **layout_grafico())
            st.plotly_chart(fig, width="stretch")

            tabla_ev = serie_ev.copy()
            tabla_ev["valor"] = tabla_ev["valor"].apply(fmt)
            st.dataframe(tabla_ev, width="stretch", hide_index=True, height=340)
            botones_descarga(serie_ev, f"evolucion_comprobantes_{metrica_ev}", "cp_ev")

    # --- Perfil de producto en el comprobante -------------------------------
    st.markdown("---")
    st.markdown("#### Perfil de producto en el comprobante")
    st.caption(
        "En cuántos comprobantes de venta aparece cada producto y cuántas "
        "unidades trae en promedio cada vez — sirve para detectar patrones "
        "como 'se vende de a 6 unidades'."
    )

    if st.button("Calcular perfil de productos", key="cp_perfil_btn"):
        with st.spinner("Calculando perfil por SKU..."):
            try:
                st.session_state["cp_perfil"] = perfil_sku_en_comprobante(
                    proyecto, args["fecha_desde"], args["fecha_hasta"],
                    sucursales=sucs or None, codigos=codigos, tipos=tipos)
            except Exception as e:
                st.error(f"Error: {e}")

    if "cp_perfil" in st.session_state:
        perfil = st.session_state["cp_perfil"]
        if perfil.empty:
            st.info("Sin comprobantes de venta para este recorte.")
        else:
            perfil_f, _ = filtros_resultado(
                perfil, "cp_perfil_f", dimensiones=("gran_super_rubro", "rubro", "marca"))
            disp = perfil_f.head(500).copy()
            disp["unidades_totales"] = disp["unidades_totales"].apply(formatear_unidades)
            disp["unidades_prom_por_aparicion"] = disp["unidades_prom_por_aparicion"].round(2)
            disp["apariciones"] = disp["apariciones"].apply(formatear_unidades)
            disp.columns = ["Código", "Descripción", "Rubro", "Marca", "Gran Super Rubro",
                            "Apariciones", "Unidades totales", "Unid. prom. x aparición"]
            st.dataframe(disp, width="stretch", hide_index=True, height=420)
            if len(perfil_f) > 500:
                st.caption(f"Mostrando 500 de {len(perfil_f):,} — descargá el listado completo.")
            botones_descarga(perfil_f, "perfil_sku_comprobante", "cp_perfil")

    # --- Afinidad entre productos (canasta) ----------------------------------
    st.markdown("---")
    st.markdown("#### Afinidad entre productos (canasta)")
    st.caption(
        "Qué productos se compran juntos en el mismo comprobante. Un recorte "
        "de fechas o de catálogo (rubro/marca) más angosto acelera bastante "
        "el cálculo."
    )
    a1, a2 = st.columns(2)
    with a1:
        min_apariciones = st.number_input(
            "Mínimo de apariciones para entrar al cálculo", min_value=2, value=20,
            step=1, key="cp_min_apar",
            help="Los SKUs con menos apariciones se descartan antes del cruce "
                 "para que no explote combinatoriamente.")
    with a2:
        top_n_afinidad = st.number_input(
            "Top N pares", min_value=10, max_value=500, value=50, step=10,
            key="cp_topn_afin")

    if st.button("Calcular afinidad", key="cp_afin_btn"):
        with st.spinner("Buscando pares de productos..."):
            try:
                st.session_state["cp_afinidad"] = afinidad_skus(
                    proyecto, args["fecha_desde"], args["fecha_hasta"],
                    sucursales=sucs or None, codigos=codigos, tipos=tipos,
                    min_apariciones=int(min_apariciones), top_n=int(top_n_afinidad))
            except Exception as e:
                st.error(f"Error: {e}")

    if "cp_afinidad" in st.session_state:
        afinidad = st.session_state["cp_afinidad"]
        if afinidad.empty:
            st.info("Sin pares de productos para este recorte / mínimo de apariciones.")
        else:
            a = afinidad.attrs
            st.caption(
                f"{a['skus_evaluados']:,} SKUs entraron al cálculo "
                f"({a['skus_descartados']:,} descartados por debajo del "
                f"mínimo de apariciones), sobre {a['total_comprobantes']:,} "
                "comprobantes de venta.")

            top_graf = afinidad.head(20).iloc[::-1]
            etiquetas = [
                f"{str(r['descripcion_a'] or r['codigo_a'])[:20]} + "
                f"{str(r['descripcion_b'] or r['codigo_b'])[:20]}"
                for _, r in top_graf.iterrows()
            ]
            fig = go.Figure()
            fig.add_bar(y=etiquetas, x=top_graf["comprobantes_conjuntos"],
                       orientation="h", marker_color="#748ffc")
            fig.update_layout(
                height=max(340, 22 * len(top_graf)),
                xaxis=dict(title="Comprobantes conjuntos", gridcolor=color_grilla()),
                yaxis=dict(gridcolor=color_grilla()), **layout_grafico())
            st.plotly_chart(fig, width="stretch")

            disp = afinidad.copy()
            disp["par"] = (
                disp["descripcion_a"].fillna(disp["codigo_a"]).astype(str).str.slice(0, 35)
                + " + " + disp["descripcion_b"].fillna(disp["codigo_b"]).astype(str).str.slice(0, 35))
            disp["comprobantes_conjuntos"] = disp["comprobantes_conjuntos"].apply(formatear_unidades)
            disp["soporte"] = disp["soporte"].apply(lambda v: f"{v:.2f}%")
            disp["confianza_a_b"] = disp["confianza_a_b"].apply(lambda v: f"{v:.1f}%")
            disp["confianza_b_a"] = disp["confianza_b_a"].apply(lambda v: f"{v:.1f}%")
            disp["lift"] = disp["lift"].round(2)
            disp = disp[["par", "comprobantes_conjuntos", "soporte",
                         "confianza_a_b", "confianza_b_a", "lift"]]
            disp.columns = ["Par de productos", "Comprobantes", "Soporte",
                            "Conf. A→B", "Conf. B→A", "Lift"]
            st.dataframe(disp, width="stretch", hide_index=True, height=420)
            botones_descarga(afinidad, "afinidad_skus", "cp_afin")

    # --- Reporte imprimible ---------------------------------------------------
    st.markdown("---")
    st.markdown("#### Reporte imprimible")
    r1, r2, r3 = st.columns([1, 1, 2])
    with r1:
        top_n_rep = st.number_input("Filas por ranking", min_value=5, max_value=100,
                                    value=25, step=5, key="cp_topn_rep")
    with r2:
        meses_rep = st.multiselect(
            "Meses (vacío = todos)", list(MESES), format_func=lambda m: MESES[m],
            key="cp_rep_meses")
    with r3:
        incluir_ev = st.checkbox("Incluir evolución mensual", value=True, key="cp_rep_ev")
        incluir_afin = st.checkbox("Incluir afinidad de productos",
                                   value=("cp_afinidad" in st.session_state), key="cp_rep_afin")

    meta_r = {
        "proyecto": proyecto,
        "desde": args["fecha_desde"], "hasta": args["fecha_hasta"],
        "valorizacion": f"{args['modo_valorizacion']} — snapshot {resumen.attrs['fecha_valorizacion']}",
        "sucursales_filtro": ", ".join(etiqueta_suc(s) for s in (sucs or [])),
        "tipos_filtro": ", ".join(tipos or []),
        "meses_filtro": ", ".join(MESES[m] for m in sorted(meses_rep)),
        "recorte": f"{len(recorte):,} SKUs filtrados" if hay_filtro else "",
        "nombres_sucursal": {c: v.split(" — ", 1)[-1] for c, v in suc_largo.items()},
    }
    evolucion_rep = None
    evolucion_suc_rep = None
    if incluir_ev and anios_disp:
        abrir_suc_rep = len(sucs or []) >= 2
        ev_multi = evolucion_comprobantes_multi(
            proyecto, anios_sel or anios_disp[:1], sucursales=sucs or None,
            meses=meses_rep or None, codigos=codigos, tipos=tipos,
            modo_valorizacion=args["modo_valorizacion"],
            fecha_valorizacion=args["fecha_valorizacion"],
            abrir_sucursales=abrir_suc_rep)
        evolucion_rep = ev_multi["combinado"]
        evolucion_suc_rep = ev_multi["por_sucursal"]
    afinidad_rep = (st.session_state.get("cp_afinidad") if incluir_afin else None)

    html = generar_reporte_comprobantes(
        proyecto, meta_r, resumen, evolucion=evolucion_rep,
        evolucion_por_sucursal=evolucion_suc_rep, afinidad=afinidad_rep,
        top_n=int(top_n_rep))
    boton_reporte(html, f"comprobantes_{args['fecha_desde']}_{args['fecha_hasta']}.html",
                 "dl_rep_cp")
