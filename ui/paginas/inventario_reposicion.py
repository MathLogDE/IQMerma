"""ui/paginas/inventario_reposicion.py — Perfil de reposición por SKU"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

LIMITE_SKUS = 25_000
TOP_RANKING = 30


def _estado(r) -> str:
    if r["sin_remitos_en_periodo"]:
        return "Sin remitos"
    return "Cruzó" if r["quebro_barrera"] else "Censurado"


def render(proyecto):
    st.title("Perfil de reposición")
    st.caption(
        "Por cada SKU de un rubro/marca en una sucursal: cada cuánto llega "
        "un remito, con qué cantidad, cuánto se vende en promedio, cuántos "
        "días pasa en quiebre o por debajo de una barrera elegida, y cuánto "
        "tarda en cruzar esa barrera contando desde el último remito "
        "recibido — para priorizar qué reponer primero dentro de una marca "
        "o rubro."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados todavía.")
        return

    fmin, fmax = rango_disponible(proyecto)
    suc_largo, _ = etiquetas_sucursal(sucursales)

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        suc_sel = st.selectbox(
            "Sucursal", sucursales["codigodepo"].tolist(),
            format_func=etiqueta_suc, key="rep_suc")
    with c2:
        f_desde = st.date_input("Desde", value=max(fmin, fmax.replace(month=1, day=1)),
                                min_value=fmin, max_value=fmax, key="rep_desde")
    with c3:
        f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                max_value=fmax, key="rep_hasta")
    with c4:
        barrera = st.number_input(
            "Barrera (unidades)", min_value=0.0, value=0.0, step=1.0,
            key="rep_barrera",
            help="Además del quiebre real (stock = 0), un nivel de alerta "
                 "temprana propio: cuántos días pasa cada SKU por debajo de "
                 "este número, y cuánto tarda en cruzarlo desde su último "
                 "remito.")

    st.markdown("##### Qué mirar")
    cat = catalogo_cacheado(proyecto)
    recorte, hay_filtro = filtros_catalogo(cat, "rep", con_activo=True,
                                          con_gsr=not modo_solo_lectura())

    if not hay_filtro:
        st.info("Elegí al menos un rubro, marca o palabra para acotar el análisis.")
        return
    if recorte.empty:
        st.warning("Ningún SKU del catálogo coincide con esos filtros.")
        return
    if len(recorte) > LIMITE_SKUS:
        st.warning(
            f"El filtro alcanza {len(recorte):,} SKUs. Acotalo un poco más "
            f"(máximo {LIMITE_SKUS:,}) para que el análisis sea rápido y legible.")
        return

    st.caption(f"**{len(recorte):,} SKUs** en el recorte.")

    if st.button("Calcular perfil de reposición"):
        with st.spinner("Cruzando remitos, ventas y quiebres..."):
            try:
                p = perfil_reposicion(
                    proyecto, suc_sel, str(f_desde), str(f_hasta),
                    codigos=recorte["codigo"].tolist(), umbral_barrera=barrera)
                st.session_state["rep_perfil"] = p
            except Exception as e:
                st.error(f"Error: {e}")

    if "rep_perfil" not in st.session_state:
        return

    perfil = st.session_state["rep_perfil"]
    if perfil.empty:
        st.info("Sin movimientos en el rango para esa sucursal y ese recorte.")
        return

    st.markdown("---")

    resumen = resumen_reposicion(perfil)

    # --- KPIs ------------------------------------------------------------------
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("SKUs analizados", f"{resumen['skus']:,}")
    k2.metric("Con remitos en el período", f"{resumen['con_remitos']:,}")
    k3.metric("Cadencia promedio",
              f"{resumen['cadencia_prom']:.0f} d" if resumen["cadencia_prom"] is not None else "—")
    k4.metric("Días en quiebre (prom.)",
              f"{resumen['dias_quiebre_prom']:.0f}" if resumen["dias_quiebre_prom"] is not None else "—")
    k5.metric("Cruzaron la barrera", f"{resumen['quebraron_barrera']:,}",
              delta=f"{resumen['censurados_barrera']:,} censurados", delta_color="off")
    st.caption(
        f"Anclado al stock del **{perfil.attrs['fecha_stock']}** · "
        f"{perfil.attrs['desde']} → {perfil.attrs['hasta']} · sucursal "
        f"**{etiqueta_suc(suc_sel)}** · barrera **{barrera:,.0f} u.**"
    )

    # --- Filtros sobre el resultado ----------------------------------------------
    f1, f2 = st.columns([3, 1])
    with f1:
        res, _ = filtros_resultado(perfil, "rep_f")
    with f2:
        solo_urgentes = st.checkbox("Solo los que cruzaron la barrera", value=False,
                                    key="rep_f_urgentes")
    if solo_urgentes:
        res = res[res["quebro_barrera"]]

    # --- Ranking de urgencia ------------------------------------------------------
    st.markdown("#### Ranking de urgencia")
    st.caption(
        "Días entre el último remito de cada SKU y el momento en que su "
        "stock cruzó la barrera — los que no cruzaron en el período no "
        "aparecen acá (quedaron censurados: no se sabe cuánto tardarían)."
    )
    urgentes = res[res["quebro_barrera"]].nsmallest(TOP_RANKING, "dias_hasta_quiebre_barrera")
    if urgentes.empty:
        st.info("Ningún SKU del recorte filtrado cruzó la barrera en el período.")
    else:
        graf = urgentes.iloc[::-1]
        etiquetas = graf["codigo"] + " — " + graf["descripcion"].fillna("").str.slice(0, 35)
        fig = go.Figure()
        fig.add_bar(y=etiquetas, x=graf["dias_hasta_quiebre_barrera"], orientation="h",
                    marker_color="#ff6b6b",
                    hovertemplate="%{y}<br>%{x:.0f} días<extra></extra>")
        fig.update_layout(height=max(340, 24 * len(graf)),
                          xaxis=dict(title="Días hasta cruzar la barrera", gridcolor=color_grilla()),
                          yaxis=dict(gridcolor=color_grilla()), **layout_grafico())
        st.plotly_chart(fig, width="stretch")

    # --- Cadencia vs. ventas -------------------------------------------------------
    st.markdown("#### Cadencia de reposición vs. ventas promedio")
    st.caption(
        "Productos de venta alta y reposición poco frecuente (arriba a la "
        "izquierda) son los que más conviene revisar primero."
    )
    con_cadencia = res[res["cadencia_dias"].notna()]
    if con_cadencia.empty:
        st.info(
            "Ningún SKU del recorte filtrado tiene más de un remito en el "
            "período (hace falta al menos dos para calcular una cadencia)."
        )
    else:
        fig2 = go.Figure()
        fig2.add_scatter(
            x=con_cadencia["cadencia_dias"], y=con_cadencia["ventas_promedio"],
            mode="markers",
            marker=dict(size=8, color=con_cadencia["dias_quiebre"],
                       colorscale="Reds", showscale=True,
                       colorbar=dict(title="Días quiebre")),
            customdata=con_cadencia[["codigo", "descripcion"]],
            hovertemplate=("%{customdata[0]} — %{customdata[1]}<br>"
                           "Cadencia: %{x:.0f} d<br>Ventas prom.: %{y:.1f} u/mes"
                           "<extra></extra>"))
        fig2.update_layout(
            height=380,
            xaxis=dict(title="Cadencia (días entre remitos)", gridcolor=color_grilla()),
            yaxis=dict(title="Ventas promedio (u./mes)", gridcolor=color_grilla()),
            **layout_grafico())
        st.plotly_chart(fig2, width="stretch")

    # --- Tabla ---------------------------------------------------------------------
    st.markdown("#### Detalle por SKU")
    st.caption(
        f"**Ventas prom. (c/stock)** solo cuenta los días con stock "
        f"disponible — no diluye la demanda por los días en quiebre, donde "
        f"no había nada para vender. **Stock sucursal** y **Stock CD** son "
        f"del último snapshot ({perfil.attrs['fecha_stock']}), no "
        f"reconstruidos — sirven para ver qué hay disponible en los "
        f"centros de distribución para pedir de inmediato, sin esperar al "
        f"próximo remito."
    )
    disp = res.copy()
    disp["estado"] = disp.apply(_estado, axis=1)
    cols_stock_cd = [f"stock_cd_{d}" for d in DEPOSITOS_DISTRIBUCION]
    cols = ["codigo", "descripcion", "rubro", "marca", "uxb", "cadencia_dias",
            "remitos_periodo", "cantidad_promedio_remito", "unidades_remitidas_periodo",
            "ventas_totales_periodo", "ventas_promedio", "ventas_promedio_ajustado",
            "dias_quiebre", "dias_sin_quiebre", "dias_bajo_barrera",
            "dias_desde_quiebre_real", "dias_hasta_quiebre_barrera", "estado",
            "dias_desde_ultimo_ingreso", "stock_sucursal", "dias_remanentes",
            *cols_stock_cd]
    tabla = disp[cols].copy()
    for c in ["uxb", "cadencia_dias", "remitos_periodo", "cantidad_promedio_remito",
              "unidades_remitidas_periodo", "ventas_totales_periodo", "ventas_promedio",
              "ventas_promedio_ajustado", "dias_quiebre", "dias_sin_quiebre",
              "dias_bajo_barrera", "dias_desde_quiebre_real", "dias_hasta_quiebre_barrera",
              "dias_desde_ultimo_ingreso", "stock_sucursal", "dias_remanentes",
              *cols_stock_cd]:
        tabla[c] = tabla[c].apply(formatear_unidades)
    tabla.columns = ["Código", "Descripción", "Rubro", "Marca", "UxB", "Cadencia (d)",
                     "Remitos", "Cant. prom./remito", "Unidades remitidas periodo",
                     "Ventas totales periodo", "Ventas prom./mes",
                     "Ventas prom. (c/stock)", "Días quiebre", "Días sin quiebre",
                     "Días bajo barrera", "Días en quiebre (racha actual)",
                     "Días hasta cruzar", "Estado", "Días desde último ingreso",
                     "Stock sucursal", "Días remanentes",
                     *[f"Stock CD {d}" for d in DEPOSITOS_DISTRIBUCION]]
    st.dataframe(tabla.head(1000), width="stretch", hide_index=True)
    if len(res) > 1000:
        st.caption(f"Mostrando 1.000 de {len(res):,} — descargá el listado completo.")

    cols_descarga = cols + ["gran_super_rubro", "fecha_ultimo_remito",
                            "dias_con_stock", "fecha_ultima_venta", "en_quiebre_real",
                            "quebro_barrera", "sin_remitos_en_periodo", "racha_quiebre",
                            "stock_min", "stock_prom", "stock_max", "stock_final",
                            "movimientos"]
    botones_descarga(disp[[c for c in cols_descarga if c in disp.columns]],
                     "perfil_reposicion", "rep")

    st.markdown("###### Exportación alternativa (con pedido sugerido)")
    st.caption(
        "Mismos SKUs, otro orden de columnas. **Pedido** = 2 meses de venta "
        "ajustada menos el stock de sucursal (negativo si ya sobra stock). "
        "**%_pedido** = Pedido sobre la venta ajustada. **dias_stock_promedio** "
        "es el mismo cálculo que Días remanentes, solo con otro nombre acá."
    )
    cols_export2 = ["codigo", "descripcion", "rubro", "gran_super_rubro", "marca", "uxb",
                    "cadencia_dias", "remitos_periodo", "cantidad_promedio_remito",
                    "unidades_remitidas_periodo", "ventas_totales_periodo", "ventas_promedio",
                    "ventas_promedio_ajustado", "dias_quiebre", "dias_sin_quiebre",
                    "dias_bajo_barrera", "stock_sucursal", "stock_cd_001",
                    "pedido_sugerido", "pct_pedido", "dias_remanentes",
                    "fecha_ultimo_remito", "fecha_ultima_venta"]
    export2 = (disp[[c for c in cols_export2 if c in disp.columns]]
               .rename(columns={"pedido_sugerido": "Pedido", "pct_pedido": "%_pedido",
                                "dias_remanentes": "dias_stock_promedio"}))
    botones_descarga(export2, "perfil_reposicion_pedido", "rep_pedido")

    # --- Reporte imprimible -----------------------------------------------------------
    meta_rep = {
        "proyecto": proyecto,
        "sucursal": etiqueta_suc(suc_sel),
        "desde": perfil.attrs["desde"], "hasta": perfil.attrs["hasta"],
        "barrera": perfil.attrs["barrera"], "fecha_stock": perfil.attrs["fecha_stock"],
        "recorte": f"{len(recorte):,} SKUs filtrados",
    }
    html_rep = generar_reporte_reposicion(proyecto, meta_rep, res)
    boton_reporte(html_rep, f"perfil_reposicion_{suc_sel}_{perfil.attrs['hasta']}.html",
                 "dl_rep_rep")
