"""ui/paginas/inventario_series.py — Serie de stock diaria y quiebres"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

# La paleta corta se repite enseguida al superponer muchos SKUs
COLORES_SKU = px.colors.qualitative.Light24


def _rachas_quiebre(d: pd.DataFrame) -> list[tuple]:
    """Tramos consecutivos con stock <= 0 (para sombrear el gráfico)."""
    q = d["stock"] <= 0
    if not q.any():
        return []
    grupo = (q != q.shift()).cumsum()
    tramos = []
    for _, g in d[q].groupby(grupo[q]):
        tramos.append((g["fecha"].iloc[0], g["fecha"].iloc[-1]))
    return tramos


def _marcas(fig, serie, codigos, tipos, mostrar_nombre=True) -> None:
    """Dibuja una X sobre la curva cada día con remito / ajuste."""
    marcas = marcadores_movimiento(serie, codigos, tipos)
    for tipo, cfg in TIPOS_MARCA.items():
        m = marcas[marcas["tipomov"] == tipo]
        if m.empty:
            continue
        fig.add_scatter(
            x=m["fecha"], y=m["stock"], mode="markers",
            name=cfg["etiqueta"], showlegend=mostrar_nombre,
            marker=dict(symbol=cfg["simbolo"], size=10, color=cfg["color"],
                        line=dict(width=1.5, color=cfg["color"])),
            customdata=m[["codigo", "delta"]],
            hovertemplate=(cfg["etiqueta"] + " %{customdata[0]}<br>%{x|%d/%m/%Y}"
                           "<br>%{customdata[1]:+,.0f} u → stock %{y:,.0f}"
                           "<extra></extra>"),
        )


def render(proyecto):
    st.title("Serie de stock y quiebres")
    st.caption(
        "Reconstruye el stock **día a día** partiendo del snapshot conocido y "
        "caminando los movimientos: las ventas restan, los remitos suman, los "
        "ajustes suman o restan según su signo. No es exacto (depende de que "
        "los movimientos estén completos), pero permite ver **períodos de "
        "quiebre** y tramos de stock muy bajo o muy alto."
    )

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)
    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
        return

    fmin, fmax = rango_disponible(proyecto)
    suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        suc_sel = st.selectbox(
            "Sucursal", sucursales["codigodepo"].tolist(),
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
            key="ser_suc")
    with c2:
        f_desde = st.date_input("Desde", value=max(fmin, fmax.replace(month=1, day=1)),
                                min_value=fmin, max_value=fmax, key="ser_desde")
    with c3:
        f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                max_value=fmax, key="ser_hasta")
    with c4:
        f_snap = st.selectbox(
            "Anclar al stock del", snapshots, key="ser_snap",
            help="Snapshot desde el que se camina hacia atrás/adelante.")

    if st.button("Reconstruir serie"):
        with st.spinner("Reconstruyendo stock día a día..."):
            try:
                s = serie_stock(proyecto, suc_sel, str(f_desde), str(f_hasta),
                                fecha_stock=f_snap)
                st.session_state["serie_stock"] = s
                st.session_state["serie_resumen"] = resumen_quiebres(s)
            except Exception as e:
                st.error(f"Error: {e}")

    if "serie_stock" not in st.session_state:
        return

    serie = st.session_state["serie_stock"]
    res_full = st.session_state["serie_resumen"]
    if serie.empty:
        st.info("Sin movimientos en el rango para esa sucursal.")
        return

    st.markdown("---")

    # --- Filtros -------------------------------------------------------------
    fc1, fc2 = st.columns([3, 1])
    with fc1:
        res, _ = filtros_resultado(res_full, "ser_f")
    with fc2:
        solo_q = st.checkbox("Solo SKUs con quiebre", value=True, key="ser_f_q")

    if solo_q:
        res = res[res["dias_quiebre"] > 0]

    # --- KPIs ----------------------------------------------------------------
    dias_rango = (pd.to_datetime(serie.attrs["hasta"])
                  - pd.to_datetime(serie.attrs["desde"])).days + 1
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("SKUs analizados", f"{len(res):,}")
    k2.metric("Con quiebre", f"{(res['dias_quiebre'] > 0).sum():,}")
    k3.metric("Días en quiebre (prom.)",
              f"{res['dias_quiebre'].mean():.0f}" if len(res) else "—",
              delta=f"de {dias_rango} días", delta_color="off")
    k4.metric("Racha más larga",
              f"{res['racha_quiebre'].max():,.0f} d" if len(res) else "—")
    incoh = (res_full["stock_min"] < 0).sum()
    st.caption(
        f"Anclado al stock del **{serie.attrs['fecha_stock']}** · "
        f"{serie.attrs['desde']} → {serie.attrs['hasta']}. "
        + (f"⚠️ {incoh:,} SKUs reconstruyen con stock negativo "
           "(movimientos y snapshot no reconcilian; tomar esos con pinzas)."
           if incoh else "")
    )

    # --- Quiebres por día (agregado) -----------------------------------------
    st.markdown("#### SKUs en quiebre por día")
    serie_f = serie[serie["codigo"].isin(set(res["codigo"]))]
    q = quiebres_por_dia(serie_f)
    fig = go.Figure()
    fig.add_scatter(x=q["fecha"], y=q["skus_en_quiebre"], mode="lines",
                    line=dict(color="#ff6b6b", width=2), fill="tozeroy",
                    fillcolor="rgba(255,107,107,0.15)", name="SKUs en quiebre")
    fig.update_layout(height=320, xaxis=dict(gridcolor=color_grilla()),
                      yaxis=dict(title="SKUs sin stock", gridcolor=color_grilla()),
                      **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    # --- Tabla resumen -------------------------------------------------------
    st.markdown("#### Resumen por SKU")
    cols = ["codigo", "descripcion", "rubro", "dias_quiebre", "pct_quiebre",
            "racha_quiebre", "stock_min", "stock_prom", "stock_max",
            "stock_final", "movimientos"]
    disp = res[cols].copy()
    for c in ["dias_quiebre", "racha_quiebre", "stock_min", "stock_max",
              "stock_final", "movimientos"]:
        disp[c] = disp[c].apply(formatear_unidades)
    disp["stock_prom"] = disp["stock_prom"].apply(
        lambda v: f"{v:,.1f}" if pd.notna(v) else "—")
    disp["pct_quiebre"] = disp["pct_quiebre"].apply(formatear_pct)
    disp.columns = ["Código", "Descripción", "Rubro", "Días quiebre", "% período",
                    "Racha máx", "Stock mín", "Stock prom", "Stock máx",
                    "Stock final", "Movs"]
    st.dataframe(disp.head(1000), width="stretch", hide_index=True)
    if len(res) > 1000:
        st.caption(f"Mostrando 1.000 de {len(res):,} — descargá el listado completo.")
    botones_descarga(res[cols], "serie_stock_quiebres", "ser")

    # --- Reporte imprimible ---------------------------------------------------
    meta_ser = {
        "proyecto": proyecto,
        "sucursal": f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —"),
        "desde": res_full.attrs["desde"],
        "hasta": res_full.attrs["hasta"],
        "fecha_stock": res_full.attrs["fecha_stock"],
    }
    html_ser = generar_reporte_series(proyecto, meta_ser, res)
    boton_reporte(html_ser, f"serie_stock_{suc_sel}_{meta_ser['fecha_stock']}.html",
                  "dl_rep_ser")

    # --- Resumen de movimientos por período -----------------------------------
    st.markdown("---")
    st.markdown("#### Resumen de movimientos por período")
    st.caption(
        "Ojo: esto es **flujo** (suma de movimientos), no el nivel de stock "
        "reconstruido arriba. Elegí un rango, una granularidad de tiempo, "
        "cómo agrupar (SKU / marca / rubro / gran super rubro) y por qué "
        "tipo de movimiento abrir — para ver de dónde viene el movimiento, "
        "no cuánto queda."
    )

    rm1, rm2, rm3 = st.columns(3)
    with rm1:
        suc_rm = st.multiselect(
            "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
            default=[suc_sel],
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
            key="ser_rm_suc")
    with rm2:
        f_rm_desde = st.date_input("Desde", value=fmin, min_value=fmin,
                                   max_value=fmax, key="ser_rm_desde")
    with rm3:
        f_rm_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                   max_value=fmax, key="ser_rm_hasta")

    rm4, rm5, rm6 = st.columns(3)
    with rm4:
        gran_sel = st.selectbox(
            "Granularidad", list(GRANULARIDADES),
            format_func=lambda g: GRANULARIDADES[g],
            index=list(GRANULARIDADES).index("mes"), key="ser_rm_gran")
    with rm5:
        dim_sel = st.selectbox(
            "Agrupar por", list(DIMENSIONES_RESUMEN),
            format_func=lambda d: DIMENSIONES_RESUMEN[d], key="ser_rm_dim")
    with rm6:
        clasif_sel = st.selectbox(
            "Clasificar por", list(CLASIFICACIONES),
            format_func=lambda c: CLASIFICACIONES[c], key="ser_rm_clasif")

    if st.button("Generar resumen", key="ser_rm_btn"):
        with st.spinner("Calculando..."):
            try:
                st.session_state["ser_rm_df"] = resumen_movimientos(
                    proyecto, str(f_rm_desde), str(f_rm_hasta),
                    codigodepo=suc_rm or None,
                    granularidad=gran_sel, dimension=dim_sel,
                    clasificar_por=clasif_sel,
                )
            except Exception as e:
                st.error(f"Error: {e}")

    if "ser_rm_df" in st.session_state:
        df_rm = st.session_state["ser_rm_df"]
        if df_rm.empty:
            st.info("Sin movimientos en el período con esos filtros.")
        else:
            gran = df_rm.attrs.get("granularidad", "mes")
            dim = df_rm.attrs.get("dimension", "codigo")

            disp = df_rm.copy()
            disp["Período"] = disp["periodo_ini"].apply(lambda p: etiqueta_periodo(p, gran))

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Unidades (neto)", formatear_unidades(disp["unidades"].sum()))
            k2.metric("Movimientos", f"{int(disp['movimientos'].sum()):,}")
            k3.metric("Períodos", f"{disp['Período'].nunique():,}")
            k4.metric(DIMENSIONES_RESUMEN[dim], f"{disp[dim].nunique():,}")

            # Gráfico: total por período, sumando toda apertura (dimensión y tipo)
            orden_periodos = (disp[["Período", "periodo_ini"]].drop_duplicates()
                              .sort_values("periodo_ini")["Período"].tolist())
            tot = disp.groupby("Período")["unidades"].sum().reindex(orden_periodos)
            fig = go.Figure(go.Bar(x=tot.index, y=tot.values, marker_color="#748ffc"))
            fig.update_layout(height=320, xaxis=dict(gridcolor=color_grilla()),
                              yaxis=dict(title="Unidades (neto)", gridcolor=color_grilla()),
                              **layout_grafico())
            st.plotly_chart(fig, width="stretch")

            # Tabla de detalle
            cols = ["periodo_ini", "Período", "abreviacion"]
            cols += ["codigo", "descripcion"] if dim == "codigo" else [dim]
            cols += [c for c in ("tipomov", "tipo") if c in disp.columns]
            cols += ["unidades", "ingreso", "egreso", "movimientos"]

            tabla = (disp.sort_values(["periodo_ini", "codigodepo", dim])[cols]
                     .drop(columns=["periodo_ini"]).copy())
            for c in ["unidades", "ingreso", "egreso", "movimientos"]:
                tabla[c] = tabla[c].apply(formatear_unidades)
            etiquetas_col = {
                "abreviacion": "Sucursal",
                "codigo": "Código", "descripcion": "Descripción", "marca": "Marca",
                "rubro": "Rubro", "gran_super_rubro": "Gran Super Rubro",
                "tipomov": "Tipo mov.", "tipo": "Tipo", "unidades": "Unidades",
                "ingreso": "Ingreso", "egreso": "Egreso", "movimientos": "Movs",
            }
            tabla.columns = [etiquetas_col.get(c, c) for c in tabla.columns]
            st.dataframe(tabla.head(2000), width="stretch", hide_index=True)
            if len(tabla) > 2000:
                st.caption(f"Mostrando 2.000 de {len(tabla):,} filas — descargá el listado completo.")
            botones_descarga(disp[[c for c in cols if c != "periodo_ini"]],
                             f"resumen_movimientos_{gran}_{dim}", "ser_rm")

    # --- Detalle de un SKU ---------------------------------------------------
    st.markdown("#### Evolución diaria de un SKU")
    if res.empty:
        st.info("Sin SKUs con los filtros aplicados.")
        return
    opciones = res["codigo"].tolist()[:500]
    etiqueta = dict(zip(res["codigo"], res["descripcion"].fillna("")))
    sc1, sc2 = st.columns([3, 2])
    with sc1:
        sku = st.selectbox(
            "SKU", opciones,
            format_func=lambda c: f"{c} — {str(etiqueta.get(c, ''))[:60]}",
            key="ser_sku")
    with sc2:
        tipos = st.multiselect(
            "Marcar movimientos", list(TIPOS_MARCA),
            default=list(TIPOS_MARCA), key="ser_tipos",
            format_func=lambda t: TIPOS_MARCA[t]["etiqueta"],
            help="Pone una marca sobre la curva el día que hubo remitos, "
                 "inventario físico, ajustes de merma o transformaciones.")

    d = serie_diaria(serie, sku)
    fig = go.Figure()
    fig.add_scatter(x=d["fecha"], y=d["stock"], mode="lines",
                    line=dict(color="#64ffda", width=2), name="Stock estimado")
    fig.add_hline(y=0, line=dict(color="#8892b0", width=1, dash="dot"))
    for ini, fin in _rachas_quiebre(d)[:60]:
        fig.add_vrect(x0=ini, x1=fin, fillcolor="#ff6b6b", opacity=0.18,
                      line_width=0)
    _marcas(fig, serie, sku, tipos)
    fig.update_layout(height=380, xaxis=dict(gridcolor=color_grilla()),
                      yaxis=dict(title="Unidades", gridcolor=color_grilla()),
                      **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    pie = "Las zonas rojas son los tramos sin stock (quiebre)."
    marcas_sku = marcadores_movimiento(serie, sku, tipos)
    # Cadencia de reposición: solo los remitos que suman stock (hay remitos de
    # salida, con diferencia negativa, que no son recepciones).
    rem = marcas_sku[(marcas_sku["tipomov"] == "REM") & (marcas_sku["delta"] > 0)]
    if len(rem) > 1:
        cada = (rem["fecha"].max() - rem["fecha"].min()).days / (len(rem) - 1)
        pie += (f" Hubo **{len(rem)} días con remito de entrada** — uno cada "
                f"{cada:.0f} días en promedio.")
    st.caption(pie)

    mov = serie[serie["codigo"] == sku][["fecha", "delta", "delta_rem",
                                         "delta_aju", "stock", "vigencia_dias"]].copy()
    mov["fecha"] = mov["fecha"].dt.date
    mov.columns = ["Fecha", "Movimiento", "Remitos", "Ajustes",
                   "Stock resultante", "Días vigente"]
    st.dataframe(mov, width="stretch", hide_index=True, height=260)

    # --- Comparar varios SKUs ------------------------------------------------
    st.markdown("---")
    st.markdown("#### Comparar varios SKUs")
    st.caption(
        "Filtrá por rubro, marca o palabra y elegí cuáles superponer en un "
        "mismo gráfico. Los filtros corren sobre **todos** los SKUs de la "
        "serie, independientes de los de arriba."
    )

    candidatos, hay_filtro = filtros_catalogo(res_full, "ser_cmp")

    if not hay_filtro:
        st.info("Elegí un rubro, una marca o escribí una palabra para buscar "
                "los SKUs a comparar.")
        return
    if candidatos.empty:
        st.warning("Ningún SKU de la serie coincide con esos filtros.")
        return

    etiq_cmp = dict(zip(candidatos["codigo"], candidatos["descripcion"].fillna("")))
    lista = candidatos["codigo"].tolist()
    sel = st.multiselect(
        f"SKUs a graficar ({len(lista):,} coinciden)", lista,
        default=lista[:5], key="ser_cmp_sel",
        format_func=lambda c: f"{c} — {str(etiq_cmp.get(c, ''))[:50]}")

    marcar_cmp = st.checkbox("Marcar remitos y ajustes", value=False,
                             key="ser_cmp_marcas")
    if not sel:
        st.info("Elegí al menos un SKU.")
        return

    largo = series_diarias(serie, sel)
    fig = go.Figure()
    for i, cod in enumerate(sel):
        dd = largo[largo["codigo"] == cod]
        fig.add_scatter(
            x=dd["fecha"], y=dd["stock"], mode="lines",
            line=dict(color=COLORES_SKU[i % len(COLORES_SKU)], width=2),
            name=f"{cod} — {str(etiq_cmp.get(cod, ''))[:30]}",
            hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f} u<extra>" + cod + "</extra>",
        )
    fig.add_hline(y=0, line=dict(color="#8892b0", width=1, dash="dot"))
    if marcar_cmp:
        _marcas(fig, serie, sel, list(TIPOS_MARCA))
    fig.update_layout(height=430, xaxis=dict(gridcolor=color_grilla()),
                      yaxis=dict(title="Unidades", gridcolor=color_grilla()),
                      **layout_grafico())
    st.plotly_chart(fig, width="stretch")
