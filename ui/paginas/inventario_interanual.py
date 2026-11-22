"""ui/paginas/inventario_interanual.py — Comparación entre años"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

# Un color por año, del más viejo (apagado) al más nuevo (destacado)
COLORES_ANIO = ["#5c6b8a", "#748ffc", "#a9e34b", "#ffa94d", "#64ffda", "#f783ac"]

# Un color por sucursal en el mes a mes comparado
COLORES_SUC = px.colors.qualitative.Light24

TOP_RANKING = 20


def _color(i, total):
    """El año más reciente siempre en el color más vivo."""
    return COLORES_ANIO[(len(COLORES_ANIO) - total + i) % len(COLORES_ANIO)]


def render(proyecto):
    st.title("Evolución interanual")
    st.caption(
        "Compara el mismo recorte de catálogo entre varios años. Se compara "
        "**flujo** (lo que se vendió o se perdió), no niveles de stock: los "
        "snapshots de stock son de una fecha puntual, así que no hay stock "
        "histórico. La valorización usa **un mismo snapshot de precios para "
        "todos los años**, así la comparación es a precios constantes y "
        "muestra volumen real, no inflación."
    )

    anios_disp = anios_disponibles(proyecto)
    if len(anios_disp) < 2:
        st.info(
            f"Se necesitan al menos dos años con movimientos para comparar. "
            f"Hoy hay {'solo ' + str(anios_disp[0]) if anios_disp else 'ninguno'}. "
            "A medida que se cargue histórico los años aparecen acá solos.")
        return

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)
    # largo ("008 — SAN JUAN") para selectores y tablas; corto ("San Juan")
    # para los gráficos, donde el código solo no dice nada.
    suc_largo, suc_corto = etiquetas_sucursal(sucursales)

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    def corto_suc(c):
        return suc_corto.get(c, str(c))

    # --- Años, métrica, dimensión -------------------------------------------
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        anios = st.multiselect(
            "Años a comparar", anios_disp, default=anios_disp[:2],
            key="ia_anios",
            help="Se ordenan solos. La variación compara el más nuevo contra "
                 "el más viejo de los elegidos.")
    with c2:
        metrica = st.selectbox("Métrica", list(METRICAS_INTERANUAL), key="ia_metrica",
                               format_func=lambda m: METRICAS_INTERANUAL[m]["etiqueta"])
    with c3:
        dimension = st.selectbox("Abrir por", list(DIMENSIONES), key="ia_dim",
                                 format_func=lambda d: DIMENSIONES[d])

    if len(anios) < 2:
        st.warning("Elegí al menos dos años.")
        return
    anios = sorted(anios)

    # --- Filtros ---------------------------------------------------------------
    # Antes de calcular meses completos: la cobertura tiene que mirar el mismo
    # recorte que se va a comparar, no el proyecto entero (una sucursal o SKU
    # con menos historia puede tener meses parciales que el total no muestra).
    with st.expander("Filtrar por sucursal / rubro / marca / SKU"):
        sucs = st.multiselect("Sucursales (vacío = todas)",
                              sucursales["codigodepo"].tolist(),
                              format_func=etiqueta_suc, key="ia_sucs")
        cat = catalogo_cacheado(proyecto)
        recorte, hay_filtro = filtros_catalogo(cat, "ia_cat", con_activo=True)
        if hay_filtro:
            st.caption(f"{len(recorte):,} SKUs en el recorte.")
    codigos_cob = recorte["codigo"].tolist() if hay_filtro else None

    # --- Meses: por defecto los completos en TODOS los años elegidos ---------
    cob = cobertura_meses(proyecto, sucursales=sucs or None, codigos=codigos_cob)
    del_periodo = cob[cob["anio"].isin(anios)]
    completos = sorted(
        m for m in range(1, 13)
        if (del_periodo[del_periodo["mes"] == m]["completo"].sum() == len(anios))
    )
    parciales = sorted(
        set(del_periodo["mes"]) - set(completos)) if not del_periodo.empty else []

    m1, m2 = st.columns([3, 1])
    with m1:
        # La key incluye los años: al cambiarlos, el default se recalcula.
        meses = st.multiselect(
            "Meses a comparar", list(MESES), default=completos or list(MESES),
            format_func=lambda m: MESES[m],
            key=f"ia_meses_{'_'.join(map(str, anios))}",
            help="Por defecto vienen sólo los meses completos en todos los "
                 "años elegidos, para que la comparación sea pareja.")
    with m2:
        dia_corte = st.number_input(
            "Cortar al día", min_value=0, max_value=31, value=0, step=1,
            key="ia_corte",
            help="0 = mes entero. Con un valor N, sólo el/los meses marcados "
                 "como incompletos se recortan al día N en todos los años — "
                 "sirve para comparar un mes que todavía va por la mitad "
                 "contra el mismo tramo del año anterior. Los meses completos "
                 "quedan enteros.")
    meses_dia_corte = sorted(set(parciales) & set(meses)) if dia_corte else None

    if not meses:
        st.warning("Elegí al menos un mes.")
        return

    if parciales:
        faltan = ", ".join(MESES[m] for m in parciales)
        incluidos = [m for m in meses if m in parciales]
        aviso = (f"Meses incompletos en alguno de los años elegidos: **{faltan}**. "
                 "No están cargados de punta a punta, así que compararlos "
                 "enteros subestima ese año.")
        if incluidos:
            st.warning(
                aviso + " Tenés seleccionado **"
                + ", ".join(MESES[m] for m in incluidos)
                + "** — usá *Cortar al día* para emparejar el tramo.")
        else:
            st.info(aviso + " Quedaron fuera de la selección.")

    # --- Valorización (solo para las métricas en $) --------------------------
    modo, f_val = "costo", None
    if METRICAS_INTERANUAL[metrica]["valorizada"]:
        v1, v2 = st.columns(2)
        with v1:
            modo = st.selectbox(
                "Valorización", ["costo", "lista_1"], key="ia_modo",
                format_func=lambda x: "A costo" if x == "costo"
                else "A precio de lista")
        with v2:
            f_val = st.selectbox("Precios del snapshot", snapshots,
                                 key="ia_fval") if snapshots else None
        if not snapshots:
            st.warning("Sin snapshots de stock — no se puede valorizar.")
            return

    if st.button("Comparar años"):
        codigos = codigos_cob
        args = dict(anios=anios, meses=meses, metrica=metrica, codigos=codigos,
                    sucursales=sucs or None, modo_valorizacion=modo,
                    fecha_valorizacion=f_val,
                    dia_corte=int(dia_corte) or None,
                    meses_dia_corte=meses_dia_corte)
        with st.spinner("Comparando años..."):
            try:
                st.session_state["ia_comp"] = comparar_anios(
                    proyecto, dimension=dimension, **args)
                st.session_state["ia_serie"] = serie_mensual_anios(proyecto, **args)
                # Para la sección de sucursales: el mismo recorte, abierto por boca.
                st.session_state["ia_suc"] = comparar_anios(
                    proyecto, dimension="codigodepo", **args)
                st.session_state["ia_args"] = args
            except Exception as e:
                st.error(f"Error: {e}")

    if "ia_comp" not in st.session_state:
        return

    comp = st.session_state["ia_comp"]
    serie = st.session_state["ia_serie"]
    if comp.empty:
        st.info("Sin movimientos para esa combinación de años, meses y filtros.")
        return

    st.markdown("---")
    a = comp.attrs
    cols_anio = [str(x) for x in a["anios"]]
    es_pesos = METRICAS_INTERANUAL[a["metrica"]]["valorizada"]
    fmt = formatear_pesos if es_pesos else formatear_unidades

    # --- KPIs: un total por año ----------------------------------------------
    ks = st.columns(len(cols_anio))
    previo = None
    for i, (col, anio) in enumerate(zip(ks, cols_anio)):
        total = a["total_por_anio"].get(int(anio), 0.0)
        delta = None
        if previo:
            delta = f"{(total - previo) / abs(previo) * 100:+.1f}% vs {cols_anio[i-1]}"
        col.metric(anio, fmt(total), delta=delta)
        previo = total

    pie = (f"{METRICAS_INTERANUAL[a['metrica']]['etiqueta']} · meses: "
           + ", ".join(MESES[m] for m in (a["meses"] or range(1, 13))))
    if a["dia_corte"]:
        if a.get("meses_dia_corte"):
            meses_c = ", ".join(MESES[m] for m in a["meses_dia_corte"])
            pie += f" · {meses_c} recortado al día {a['dia_corte']}"
        else:
            pie += f" · recortado al día {a['dia_corte']} de cada mes"
    if es_pesos:
        pie += (f" · precios constantes a {a['modo_valorizacion']} del snapshot "
                f"{a['fecha_valorizacion']}")
    st.caption(pie)

    vacios = [c for c in cols_anio if not a["total_por_anio"].get(int(c))]
    if vacios:
        st.warning(f"Sin datos para {', '.join(vacios)} con estos filtros: "
                   "las variaciones contra ese año no significan nada.")

    # --- Serie mensual: una línea por año ------------------------------------
    st.markdown("#### Mes a mes")
    serie = serie.sort_values("mes")
    fig = go.Figure()
    for i, anio in enumerate(a["anios"]):
        d = serie[serie["anio"] == anio]
        fig.add_scatter(
            x=d["mes_nombre"], y=d["valor"], mode="lines+markers", name=str(anio),
            line=dict(color=_color(i, len(a["anios"])), width=2),
            marker=dict(size=7),
            hovertemplate="%{x} " + str(anio) + "<br>%{y:,.0f}<extra></extra>")
    fig.update_layout(height=380, xaxis=dict(gridcolor=color_grilla()),
                      yaxis=dict(title=METRICAS_INTERANUAL[a["metrica"]]["etiqueta"],
                                 gridcolor=color_grilla()),
                      **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    # --- Ranking por dimensión ------------------------------------------------
    dim = a["dimension"]
    st.markdown(f"#### Por {DIMENSIONES[dim].lower()}")
    orden = st.radio(
        "Ordenar por", ["Mayor volumen", "Más creció", "Más cayó"],
        horizontal=True, key="ia_orden")
    ultimo = cols_anio[-1]
    if orden == "Mayor volumen":
        col_orden, ascendente = ultimo, False
    elif orden == "Más creció":
        col_orden, ascendente = "variacion_abs", False
    else:
        col_orden, ascendente = "variacion_abs", True
    comp_ordenado = comp.sort_values(col_orden, ascending=ascendente)
    top = comp_ordenado.head(TOP_RANKING)

    graf = top.iloc[::-1]
    etiquetas = (graf[dim].map(corto_suc) if dim == "codigodepo"
                 else graf["etiqueta"].astype(str).str.slice(0, 45))
    fig = go.Figure()
    for i, anio in enumerate(cols_anio):
        fig.add_bar(y=etiquetas, x=graf[anio], name=anio, orientation="h",
                    marker_color=_color(i, len(cols_anio)))
    fig.update_layout(height=max(340, 26 * len(graf)), barmode="group",
                      xaxis=dict(title=METRICAS_INTERANUAL[a["metrica"]]["etiqueta"],
                                 gridcolor=color_grilla()),
                      yaxis=dict(gridcolor=color_grilla()), **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    # --- Tabla -----------------------------------------------------------------
    # Mismo orden que el gráfico de arriba (según "Ordenar por"), no el orden
    # default de comparar_anios — si no, tabla y gráfico se contradicen.
    disp = comp_ordenado.copy()
    if dim == "codigodepo":
        disp["etiqueta"] = disp[dim].map(etiqueta_suc)
    vista = disp[["etiqueta", *cols_anio, "variacion_abs", "variacion_pct", "skus"]]
    vista = vista.copy()
    for c in [*cols_anio, "variacion_abs"]:
        vista[c] = vista[c].apply(fmt)
    vista["variacion_pct"] = vista["variacion_pct"].apply(
        lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
    vista["skus"] = vista["skus"].apply(formatear_unidades)
    vista.columns = [DIMENSIONES[dim], *cols_anio,
                     f"Δ {cols_anio[0]}→{cols_anio[-1]}", "Δ %", "SKUs"]
    st.dataframe(vista.head(1000), width="stretch", hide_index=True, height=420)
    if len(comp) > 1000:
        st.caption(f"Mostrando 1.000 de {len(comp):,} — descargá el listado completo.")

    descarga = disp[[dim, "etiqueta", *cols_anio, "variacion_abs",
                     "variacion_pct", "skus"]]
    botones_descarga(descarga, f"interanual_{a['metrica']}_por_{dim}", "ia")

    # --- Comparación entre sucursales ----------------------------------------
    st.markdown("---")
    st.markdown("#### Comparación entre sucursales")
    st.caption(
        "El mismo recorte, abierto por boca: sirve para ver si una caída es "
        "generalizada o se concentra en unas pocas sucursales."
    )

    comp_suc = st.session_state["ia_suc"]
    if comp_suc.empty:
        st.info("Sin datos por sucursal para este recorte.")
        return

    comp_suc = comp_suc.copy()
    comp_suc["etiqueta"] = comp_suc["codigodepo"].map(etiqueta_suc)

    # Variación por sucursal: dónde se ganó y dónde se perdió
    ordenado = comp_suc.sort_values("variacion_abs")
    graf = pd.concat([ordenado.head(10), ordenado.tail(10)]).drop_duplicates("codigodepo")
    fig = go.Figure()
    fig.add_bar(
        y=graf["codigodepo"].map(corto_suc), x=graf["variacion_abs"], orientation="h",
        marker_color=["#ff6b6b" if v < 0 else "#64ffda" for v in graf["variacion_abs"]],
        hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>", name="Variación")
    fig.add_vline(x=0, line=dict(color="#8892b0", width=1))
    fig.update_layout(
        height=max(320, 26 * len(graf)), showlegend=False,
        xaxis=dict(title=f"Δ {cols_anio[0]} → {cols_anio[-1]}", gridcolor=color_grilla()),
        yaxis=dict(gridcolor=color_grilla()), **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    # Mes a mes de las sucursales elegidas: color por sucursal, trazo por año
    top_suc = comp_suc.nlargest(3, cols_anio[-1])["codigodepo"].tolist()
    elegidas = st.multiselect(
        "Ver mes a mes", comp_suc["codigodepo"].tolist(), default=top_suc,
        format_func=etiqueta_suc, key="ia_suc_sel")

    if elegidas:
        args = st.session_state["ia_args"]
        with st.spinner("Calculando series por sucursal..."):
            s_suc = serie_mensual_sucursales(
                proyecto, sucursales=elegidas,
                **{k: v for k, v in args.items() if k != "sucursales"})
        s_suc = s_suc.sort_values("mes")
        # Un color por sucursal; el año más viejo punteado para distinguirlos.
        fig = go.Figure()
        for i, dep in enumerate(elegidas):
            for j, anio in enumerate(a["anios"]):
                d = s_suc[(s_suc["codigodepo"] == dep) & (s_suc["anio"] == anio)]
                fig.add_scatter(
                    x=d["mes_nombre"], y=d["valor"], mode="lines+markers",
                    name=f"{corto_suc(dep)} · {anio}",
                    line=dict(color=COLORES_SUC[i % len(COLORES_SUC)], width=2,
                              dash="solid" if j == len(a["anios"]) - 1 else "dot"),
                    marker=dict(size=6),
                    hovertemplate=f"{etiqueta_suc(dep)} {anio}"
                                  "<br>%{x}: %{y:,.0f}<extra></extra>")
        fig.update_layout(
            height=400, xaxis=dict(gridcolor=color_grilla()),
            yaxis=dict(title=METRICAS_INTERANUAL[a["metrica"]]["etiqueta"],
                       gridcolor=color_grilla()), **layout_grafico())
        st.plotly_chart(fig, width="stretch")
        st.caption("Línea llena = año más reciente; punteada = años anteriores.")

    vista_suc = comp_suc[["etiqueta", *cols_anio, "variacion_abs",
                          "variacion_pct", "skus"]].copy()
    for c in [*cols_anio, "variacion_abs"]:
        vista_suc[c] = vista_suc[c].apply(fmt)
    vista_suc["variacion_pct"] = vista_suc["variacion_pct"].apply(
        lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
    vista_suc["skus"] = vista_suc["skus"].apply(formatear_unidades)
    vista_suc.columns = ["Sucursal", *cols_anio,
                         f"Δ {cols_anio[0]}→{cols_anio[-1]}", "Δ %", "SKUs"]
    st.dataframe(vista_suc, width="stretch", hide_index=True)
    botones_descarga(
        comp_suc[["codigodepo", "etiqueta", *cols_anio, "variacion_abs",
                  "variacion_pct", "skus"]],
        f"interanual_{a['metrica']}_por_sucursal", "ia_suc")

    # --- Ventas vs Remitos ----------------------------------------------------
    st.markdown("---")
    st.markdown("#### Ventas vs Reposición")
    st.caption(
        "Barras = ventas · líneas = remitos recibidos. Sirve para ver cuánto "
        "de la variación de ventas viene acompañada de un cambio en la "
        "distribución. **Muestra si las dos series se mueven juntas, no prueba "
        "causalidad**: se puede vender menos porque llega menos mercadería, y "
        "también reponer menos porque se vende menos."
    )

    vr1, vr2 = st.columns([1, 2])
    with vr1:
        vr_unidad = st.radio("Medir en", ["Unidades", "Pesos"], horizontal=True,
                             key="ia_vr_unidad")
    with vr2:
        sin_cd = st.checkbox(
            "Excluir centros de distribución (001 / 002)", value=True,
            key="ia_vr_cd",
            help="El ingreso al CD desde el proveedor no es mercadería que le "
                 "llegue a una boca. Mezclarlo distorsiona la lectura.")

    args_vr = {k: v for k, v in st.session_state["ia_args"].items()
               if k not in ("metrica", "modo_valorizacion", "fecha_valorizacion")}
    with st.spinner("Cruzando ventas y remitos..."):
        vr = ventas_vs_remitos(
            proyecto, valorizado=(vr_unidad == "Pesos"),
            modo_valorizacion=modo, fecha_valorizacion=f_val or (
                snapshots[0] if snapshots else None),
            excluir_depositos=DEPOSITOS_DISTRIBUCION if sin_cd else None,
            **args_vr)

    if vr.empty:
        st.info("Sin datos de ventas ni remitos para este recorte.")
    else:
        fmt_vr = formatear_pesos if vr_unidad == "Pesos" else formatear_unidades
        fig = go.Figure()
        for i, anio in enumerate(vr.attrs["anios"]):
            d = vr[vr["anio"] == anio]
            fig.add_bar(x=d["mes_nombre"], y=d["ventas"], name=f"Ventas {anio}",
                        marker_color=_color(i, len(vr.attrs["anios"])),
                        opacity=0.85)
        for i, anio in enumerate(vr.attrs["anios"]):
            d = vr[vr["anio"] == anio]
            fig.add_scatter(
                x=d["mes_nombre"], y=d["remitos"], name=f"Remitos {anio}",
                mode="lines+markers", yaxis="y2",
                line=dict(color=_color(i, len(vr.attrs["anios"])), width=3,
                          dash="solid" if i == len(vr.attrs["anios"]) - 1 else "dot"),
                marker=dict(size=8, symbol="diamond"))
        fig.update_layout(
            height=430, barmode="group",
            xaxis=dict(gridcolor=color_grilla()),
            yaxis=dict(title=f"Ventas ({vr_unidad.lower()})", gridcolor=color_grilla()),
            yaxis2=dict(title=f"Remitos ({vr_unidad.lower()})", overlaying="y",
                        side="right", gridcolor=color_grilla(), showgrid=False),
            **layout_grafico())
        st.plotly_chart(fig, width="stretch")

        # --- Lectura del cruce ------------------------------------------------
        var = vr.attrs["variaciones"]
        v_ven, v_rem, brecha = var["ventas"], var["remitos"], vr.attrs["brecha"]
        b1, b2, b3 = st.columns(3)
        b1.metric(f"Ventas {cols_anio[-1]} vs {cols_anio[0]}",
                  f"{v_ven:+.1f}%" if v_ven is not None else "—")
        b2.metric(f"Remitos {cols_anio[-1]} vs {cols_anio[0]}",
                  f"{v_rem:+.1f}%" if v_rem is not None else "—")
        b3.metric("Brecha", f"{brecha:+.1f} pp" if brecha is not None else "—",
                  help="Variación de remitos menos la de ventas. Cerca de cero "
                       "= las dos series se movieron parejas.")

        if v_ven is not None and v_rem is not None:
            if abs(brecha) <= 5:
                lectura = (f"Ventas y reposición se movieron **casi parejas** "
                           f"({v_ven:+.1f}% vs {v_rem:+.1f}%): el cambio de "
                           "ventas es consistente con lo que llegó de mercadería.")
            elif v_ven < 0 and v_rem >= 0:
                lectura = (f"Las ventas cayeron {v_ven:+.1f}% **pero la "
                           f"reposición no** ({v_rem:+.1f}%): llegó tanta o más "
                           "mercadería que el año anterior, así que la caída no "
                           "se explica por falta de abastecimiento — hay que "
                           "buscarla en demanda, precio o mix.")
            elif v_ven < 0 and v_rem < v_ven:
                lectura = (f"Las ventas cayeron {v_ven:+.1f}% y la reposición "
                           f"cayó todavía más ({v_rem:+.1f}%): la falta de "
                           "mercadería es candidata a explicar buena parte de "
                           "la caída.")
            else:
                lectura = (f"Ventas {v_ven:+.1f}% y reposición {v_rem:+.1f}% "
                           f"({brecha:+.1f} pp de brecha).")
            st.info(lectura)

        cob = (vr.groupby("anio")["cobertura"].mean()
               .apply(lambda v: f"{v:,.2f}" if pd.notna(v) else "—"))
        st.caption(
            "Unidades recibidas por cada unidad vendida (promedio mensual): "
            + " · ".join(f"**{a}** {v}" for a, v in cob.items())
            + (". Sin los centros de distribución."
               if sin_cd else ". Incluye el ingreso a los centros de distribución."))

        tabla_vr = vr.copy()
        tabla_vr["anio"] = tabla_vr["anio"].astype(str)
        tabla_vr["ventas"] = tabla_vr["ventas"].apply(fmt_vr)
        tabla_vr["remitos"] = tabla_vr["remitos"].apply(fmt_vr)
        tabla_vr["cobertura"] = tabla_vr["cobertura"].apply(
            lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
        tabla_vr = tabla_vr[["anio", "mes_nombre", "ventas", "remitos", "cobertura"]]
        tabla_vr.columns = ["Año", "Mes", "Ventas", "Remitos", "Recibido / vendido"]
        with st.expander("Ver el detalle mes a mes"):
            st.dataframe(tabla_vr, width="stretch", hide_index=True)
        botones_descarga(vr, "interanual_ventas_vs_remitos", "ia_vr")

    # --- Reporte imprimible ---------------------------------------------------
    st.markdown("---")
    st.markdown("#### Reporte imprimible")
    r1, r2 = st.columns([1, 2])
    with r1:
        top_n = st.number_input("Filas por ranking", min_value=5, max_value=100,
                                value=25, step=5, key="ia_topn")
    with r2:
        incluir_suc = st.checkbox("Incluir la comparación entre sucursales",
                                  value=True, key="ia_rep_suc")
        incluir_vr = st.checkbox("Incluir ventas contra reposición",
                                 value=True, key="ia_rep_vr")

    meta_r = {
        "proyecto": proyecto,
        "sucursales_filtro": ", ".join(etiqueta_suc(s) for s in (sucs or [])),
        "recorte": f"{len(recorte):,} SKUs filtrados" if hay_filtro else "",
        "nombres_sucursal": {c: v.split(" — ", 1)[-1] for c, v in suc_largo.items()},
        "nombres_cortos": suc_corto,
    }
    html = generar_reporte_interanual(
        proyecto, meta_r, comp, serie,
        comp_sucursales=comp_suc.head(int(top_n)) if incluir_suc else None,
        vs_remitos=vr if (incluir_vr and not vr.empty) else None,
        top_n=int(top_n))
    boton_reporte(html, f"interanual_{a['metrica']}_{'_'.join(cols_anio)}.html", "dl_rep_ia")
