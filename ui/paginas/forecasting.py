"""ui/paginas/forecasting.py — Página Forecast"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

# Agrupación del pronóstico para mostrar (no cambia el modelo, solo el
# bucketing de los meses proyectados).
AGRUPACIONES = {"mensual": "Mensual", "trimestral": "Trimestral", "semestral": "Semestral"}
UNIDAD_PERIODO = {"mensual": "meses", "trimestral": "trimestres", "semestral": "semestres"}
MESES_LARGO = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
               7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre",
               11: "Noviembre", 12: "Diciembre"}
MESES_A_PROYECTAR = 18  # cuántos meses hacia adelante se pueden elegir como objetivo
NOMBRES_MODELO = {
    "estacional_robusto": "Estacional robusto",
    "holt_winters": "Holt-Winters",
    "sarima": "SARIMA",
}


def _mes_largo(mes: str) -> str:
    anio, mes_num = mes.split("-")
    return f"{MESES_LARGO[int(mes_num)]} {anio}"


def _etiqueta_periodo(mes: str, agrupacion: str) -> str:
    ts = pd.Timestamp(mes)
    if agrupacion == "trimestral":
        return f"{ts.year}-T{(ts.month - 1) // 3 + 1}"
    if agrupacion == "semestral":
        return f"{ts.year}-S{(ts.month - 1) // 6 + 1}"
    return mes


def _agrupar_periodo(df: pd.DataFrame, agrupacion: str) -> pd.DataFrame:
    """
    Reagrupa un resultado mes a mes (de `forecast_ventas` o `serie_mensual_real`)
    en buckets trimestrales o semestrales, sumando unidades/valor/bandas.
    "mensual" no transforma nada. Funciona con o sin columna "tipo" (el
    overlay de `serie_mensual_real` no la tiene) para que las series que se
    grafican juntas queden sobre el mismo eje de períodos. La banda de error
    sumada es una aproximación (no un recálculo estadístico a nivel del
    período agregado).
    """
    if agrupacion == "mensual" or df.empty:
        return df
    out = df.copy()
    out["mes"] = out["mes"].apply(lambda m: _etiqueta_periodo(m, agrupacion))
    agg = {c: "sum" for c in ("unidades", "valor", "banda_inf", "banda_sup")
           if c in out.columns}
    claves = [c for c in ("grupo", "tipo") if c in out.columns] + ["mes"]
    out = out.groupby(claves, dropna=False, as_index=False).agg(agg)
    return out.sort_values(claves).reset_index(drop=True)


def render(proyecto):
    st.title("Forecast de ventas")
    st.caption(
        "Proyección mensual de **unidades vendidas** por nivel de agregación "
        "(a nivel SKU la demanda es errática y el forecast sería ruido). "
        "Compiten varios modelos por grupo (estacional robusto, Holt-Winters, "
        "SARIMA cuando hay suficiente historia) y gana el de menor error en "
        "backtest — nunca es una caja negra, abajo se ve qué modelo ganó y con "
        "qué error. Los grupos con historia corta (menos de 12 meses, pero al "
        "menos 3) no se descartan: piden prestado el patrón estacional a "
        "grupos parecidos (kNN). Los meses atípicos se detectan y no dominan "
        "ningún ajuste."
    )

    ETIQUETA_NIVEL = {
        "total": "Total", "gran_super_rubro": "Gran Super Rubro",
        "rubro": "Rubro", "marca": "Marca", "sucursal": "Sucursal",
    }
    ETIQUETA_METRICA = {
        "ventas": "Ventas (salida)",
        "transferencias": "Transferencias recibidas",
    }
    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay movimientos cargados.")
    else:
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        _, fmax = rango_disponible(proyecto)
        ultimo_periodo = pd.Period(fmax, freq="M")
        opciones_mes = list(pd.period_range(
            ultimo_periodo + 1, periods=MESES_A_PROYECTAR, freq="M"))

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            metrica = st.selectbox(
                "Métrica a proyectar", list(METRICAS),
                format_func=lambda m: ETIQUETA_METRICA[m], key="fc_metrica",
                help="Proyectar transferencias sirve para planificar el "
                     "abastecimiento; compararlas contra ventas muestra si "
                     "una caída de venta es de demanda o de abastecimiento.",
            )
        with c2:
            nivel = st.selectbox(
                "Nivel de agregación", list(NIVELES),
                format_func=lambda n: ETIQUETA_NIVEL[n], key="fc_nivel",
            )
        with c3:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas" if c == TODAS
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="fc_suc", disabled=(nivel == "sucursal"),
            )
        with c4:
            mes_objetivo = st.selectbox(
                "Mes a proyectar", opciones_mes,
                format_func=lambda p: f"{MESES_LARGO[p.month]} {p.year}", key="fc_mes_obj",
                help="El pronóstico llega hasta este mes; más abajo se compara "
                     "contra el mismo mes de años anteriores.",
            )
        with c5:
            agrupacion = st.selectbox(
                "Agrupar por", list(AGRUPACIONES), key="fc_agrup",
                format_func=lambda a: AGRUPACIONES[a],
                help="Solo cambia cómo se muestra el pronóstico (suma de "
                     "meses); el modelo sigue ajustando mes a mes.",
            )

        superponer = st.checkbox(
            f"Superponer la serie real de "
            f"{'transferencias' if metrica == 'ventas' else 'ventas'}",
            value=True, key="fc_overlay",
            help="Si las transferencias caen antes que las ventas, la caída "
                 "es de abastecimiento, no de demanda.",
        )

        if st.button("Proyectar"):
            with st.spinner("Proyectando..."):
                try:
                    depo = (None if (suc_sel == TODAS or nivel == "sucursal")
                            else suc_sel)
                    mes_objetivo_str = str(mes_objetivo)
                    # +1 de margen: el modelo puede descartar el último mes real
                    # si está incompleto, corriendo el arranque del pronóstico
                    # un mes — de más se recorta después por etiqueta, no por
                    # posición, así que el mes objetivo siempre queda incluido.
                    horizonte_calc = (mes_objetivo - ultimo_periodo).n + 1
                    df_fc = forecast_ventas_cacheado(
                        proyecto, codigodepo=depo,
                        nivel=nivel, horizonte=horizonte_calc, backtest=3,
                        metrica=metrica,
                    )
                    if not df_fc.empty:
                        df_fc = df_fc[(df_fc["tipo"] == "real")
                                      | (df_fc["mes"] <= mes_objetivo_str)]
                    st.session_state["df_fc"] = df_fc
                    st.session_state["fc_mes_objetivo"] = mes_objetivo_str
                    st.session_state["fc_mes_objetivo_label"] = (
                        f"{MESES_LARGO[mes_objetivo.month]} {mes_objetivo.year}")
                    otra = "transferencias" if metrica == "ventas" else "ventas"
                    st.session_state["df_fc_otra"] = serie_mensual_real(
                        proyecto, codigodepo=depo, nivel=nivel, metrica=otra,
                    )
                    st.session_state["fc_otra_nombre"] = ETIQUETA_METRICA[otra]
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_fc" in st.session_state:
            df_fc = st.session_state["df_fc"]
            if df_fc.empty:
                st.info("Sin datos suficientes para proyectar.")
            else:
                st.markdown("---")
                mapes = df_fc.attrs.get("mape", {})
                atipicos = df_fc.attrs.get("meses_atipicos", {})
                excluidos = df_fc.attrs.get("grupos_excluidos", [])
                modelos_elegidos = df_fc.attrs.get("modelo_elegido", {})
                mape_por_modelo = df_fc.attrs.get("mape_por_modelo", {})

                grupos = sorted(df_fc["grupo"].unique().tolist())
                grupo_sel = (st.selectbox("Grupo", grupos, key="fc_grupo")
                             if len(grupos) > 1 else grupos[0])

                mes_objetivo_str = st.session_state.get("fc_mes_objetivo", "")
                mes_objetivo_label = st.session_state.get("fc_mes_objetivo_label", mes_objetivo_str)

                g_mensual = df_fc[df_fc["grupo"] == grupo_sel]
                fc_mensual = g_mensual[g_mensual["tipo"] == "forecast"].sort_values("mes")

                g = _agrupar_periodo(g_mensual, agrupacion)
                reales = g[g["tipo"] == "real"]
                fc = g[g["tipo"] == "forecast"]
                if agrupacion != "mensual":
                    st.caption(
                        f"Vista {AGRUPACIONES[agrupacion].lower()}: suma de los meses "
                        "de cada período. La banda de error es la suma de las bandas "
                        "mensuales (aproximada, no un recálculo del período agregado)."
                    )

                k1, k2, k3 = st.columns(3)
                objetivo = fc_mensual[fc_mensual["mes"] == mes_objetivo_str]
                if not objetivo.empty:
                    o = objetivo.iloc[0]
                    k1.metric(f"Pronóstico {mes_objetivo_label}",
                              f"{o['unidades']:,.0f} u.",
                              delta=formatear_pesos(o["valor"]), delta_color="off")
                else:
                    k1.metric(f"Pronóstico {mes_objetivo_label}", "—",
                              help="Este grupo no tiene proyección para ese mes "
                                   "(historia insuficiente, ver el aviso al pie).")
                if not fc.empty:
                    k2.metric(f"Acumulado hasta {mes_objetivo_label} "
                              f"({len(fc)} {UNIDAD_PERIODO[agrupacion]})",
                              f"{fc['unidades'].sum():,.0f} u.",
                              delta=formatear_pesos(fc["valor"].sum()), delta_color="off")
                mape_g = mapes.get(str(grupo_sel))
                k3.metric("Error esperado (MAPE backtest)",
                          f"{mape_g:.0f}%" if mape_g is not None else "—",
                          help="Promedio del error porcentual al predecir los "
                               "últimos 3 meses reales con el modelo elegido.")

                # --- modelo elegido para este grupo (competencia por MAPE) --
                modelo_g = modelos_elegidos.get(str(grupo_sel))
                if modelo_g:
                    detalle_g = mape_por_modelo.get(str(grupo_sel))
                    if detalle_g:
                        otros = ", ".join(
                            f"{NOMBRES_MODELO.get(m, m)}: {v:.0f}%"
                            for m, v in sorted(detalle_g.items(), key=lambda kv: kv[1]))
                        st.caption(
                            f"**Modelo elegido:** {NOMBRES_MODELO.get(modelo_g, modelo_g)} "
                            f"— compitió contra {otros} (backtest de 3 meses)."
                        )
                    else:
                        st.caption(f"**Modelo:** {NOMBRES_MODELO.get(modelo_g, modelo_g)}")

                # --- comparación interanual del mismo mes calendario --------
                if mes_objetivo_str:
                    mes_num = int(mes_objetivo_str[5:7])
                    anio_obj = int(mes_objetivo_str[:4])
                    hist_mismo_mes = g_mensual[
                        (g_mensual["tipo"] == "real")
                        & (g_mensual["mes"].str.slice(5, 7) == f"{mes_num:02d}")
                        & (g_mensual["mes"].str.slice(0, 4).astype(int) < anio_obj)
                    ].sort_values("mes", ascending=False)
                    st.markdown(f"###### {MESES_LARGO[mes_num]} en años anteriores")
                    if hist_mismo_mes.empty:
                        st.caption(
                            f"Sin datos de {MESES_LARGO[mes_num]} de años anteriores "
                            "todavía — a medida que se cargue histórico va a aparecer acá."
                        )
                    else:
                        cols_hist = st.columns(len(hist_mismo_mes))
                        for col, (_, row) in zip(cols_hist, hist_mismo_mes.iterrows()):
                            col.metric(_mes_largo(row["mes"]),
                                       f"{row['unidades']:,.0f} u.",
                                       delta=formatear_pesos(row["valor"]),
                                       delta_color="off")

                at_g = atipicos.get(str(grupo_sel), [])
                if at_g:
                    st.warning(
                        f"Meses atípicos detectados (no dominan el ajuste, "
                        f"conviene investigarlos en Control de merma / con el ERP): "
                        f"{', '.join(at_g)}"
                    )

                # --- gráfico ------------------------------------------------
                fig = go.Figure()
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_sup"],
                    mode="lines", line=dict(width=0), showlegend=False,
                )
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_inf"],
                    mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(255,169,77,0.15)",
                )
                fig.add_scatter(
                    name="Real", x=reales["mes"], y=reales["unidades"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=6),
                )
                df_at = reales[reales["mes"].isin(at_g)]
                if not df_at.empty:
                    fig.add_scatter(
                        name="Atípico", x=df_at["mes"], y=df_at["unidades"],
                        mode="markers",
                        marker=dict(size=11, color="#ff6b6b", symbol="x"),
                    )
                if superponer and "df_fc_otra" in st.session_state:
                    df_o = st.session_state["df_fc_otra"]
                    if not df_o.empty:
                        df_og = _agrupar_periodo(
                            df_o[df_o["grupo"] == grupo_sel], agrupacion
                        ).sort_values("mes")
                        if not df_og.empty:
                            fig.add_scatter(
                                name=st.session_state.get("fc_otra_nombre", "Otra métrica"),
                                x=df_og["mes"], y=df_og["unidades"],
                                mode="lines",
                                line=dict(color="#748ffc", width=1.5, dash="dot"),
                            )
                fig.add_scatter(
                    name="Forecast", x=fc["mes"], y=fc["unidades"],
                    mode="lines+markers",
                    line=dict(color="#ffa94d", width=2, dash="dash"),
                    marker=dict(size=7),
                )
                fig.update_layout(
                    height=440,
                    xaxis=dict(gridcolor=color_grilla()),
                    yaxis=dict(title="Unidades / mes", gridcolor=color_grilla()),
                    **layout_grafico(),
                )
                st.plotly_chart(fig, width="stretch")

                # --- tabla de proyección (todos los grupos) -------------------
                st.markdown("#### Proyección por grupo")
                df_fc_fore = _agrupar_periodo(df_fc[df_fc["tipo"] == "forecast"], agrupacion)
                df_t = df_fc_fore.pivot_table(
                    index="grupo", columns="mes", values="unidades", aggfunc="sum",
                ).round(0)
                st.dataframe(
                    df_t.style.format("{:,.0f}"), width="stretch",
                )
                botones_descarga(df_fc_fore, "forecast_ventas", "fc")

                meta_f = {
                    "proyecto": proyecto,
                    "metrica": ETIQUETA_METRICA.get(
                        df_fc.attrs.get("metrica", "ventas"), "Ventas"),
                    "nivel": ETIQUETA_NIVEL.get(nivel, nivel),
                    "sucursal": ("Todas" if (suc_sel == TODAS or nivel == "sucursal")
                                 else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                    "horizonte": len(fc_mensual),
                }
                html_f = generar_reporte_forecast(
                    proyecto, meta_f, df_fc, grupo_detalle=grupo_sel)
                boton_reporte(html_f, f"forecast_{mes_objetivo_str}.html", "dl_rep_fc")

                if excluidos:
                    st.caption(
                        f"{len(excluidos)} grupos sin proyección por historia "
                        f"insuficiente (< {MIN_MESES_KNN} meses, ni siquiera para "
                        f"prestar patrón de otro grupo): {', '.join(excluidos[:8])}"
                        + ("…" if len(excluidos) > 8 else "")
                    )

        # --- Detalle por SKU: estimación + stock (exhibir vs pedir) ---------
        mes_desde_sku = ultimo_periodo + 1
        mes_desde_sku_str, mes_hasta_sku_str = str(mes_desde_sku), str(mes_objetivo)
        periodo_sku_label = (f"{MESES_LARGO[mes_desde_sku.month]} {mes_desde_sku.year}"
                             if mes_desde_sku == mes_objetivo else
                             f"{MESES_LARGO[mes_desde_sku.month]} {mes_desde_sku.year} a "
                             f"{MESES_LARGO[mes_objetivo.month]} {mes_objetivo.year}")

        st.markdown("---")
        st.markdown("#### 📦 Detalle por SKU: estimación + stock")
        st.caption(
            f"Estima **{periodo_sku_label}** (el mismo rango elegido arriba en "
            "\"Mes a proyectar\") combinando dos señales simples: la tendencia "
            "reciente (últimos 90 días) extrapolada al período, y el promedio "
            "de lo vendido en ese mismo rango de meses en años anteriores (si "
            "ya hay alguno completo cargado). A nivel SKU puntual la demanda "
            "es errática — por eso esto es una **estimación** transparente, no "
            "el modelo de estacionalidad de arriba. Se cruza con el stock "
            "actual (sucursal(es) elegidas para exhibir, depósitos 001/002 "
            "para pedir) y la venta de cada año calendario disponible. La "
            "columna **Clasificación** resume la acción sugerida: *Exhibir* "
            "(hay stock en sucursal y se vende), *Pedir* (no hay en sucursal "
            "pero sí en el CD), *Recomendar compra* (no hay en ningún lado) o "
            "*Sin rotación* (hay stock en sucursal pero no se vende)."
        )

        cat_sku = catalogo_cacheado(proyecto)
        recorte_sku, hay_filtro_sku = filtros_catalogo(cat_sku, "fc_sku", con_activo=True)

        default_suc = ([suc_sel] if (nivel != "sucursal" and suc_sel != TODAS) else [])
        sucs_exhibir = st.multiselect(
            "Sucursales a exhibir (vacío = todas, sin depósitos de logística)",
            sucursales["codigodepo"].tolist(), default=default_suc,
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
            key="fc_sku_sucs",
        )

        if st.button("Calcular estimación y stock", key="fc_sku_btn"):
            with st.spinner("Calculando demanda estimada y stock..."):
                try:
                    st.session_state["df_sku"] = estimar_demanda_stock_sku(
                        proyecto, mes_desde=mes_desde_sku_str, mes_hasta=mes_hasta_sku_str,
                        codigos=recorte_sku["codigo"].tolist() if hay_filtro_sku else None,
                        sucursales_exhibir=sucs_exhibir or None,
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_sku" in st.session_state:
            df_sku = st.session_state["df_sku"]
            if df_sku.empty:
                st.info("Sin ventas ni stock para ese recorte.")
            else:
                m1, m2, m3 = st.columns(3)
                m1.metric("Demanda estimada (período)",
                          formatear_pesos(df_sku["valor_estimado"].sum()))
                m2.metric("Stock a exhibir", formatear_unidades(df_sku["stock_exhibir"].sum()))
                m3.metric("Stock a pedir (001/002)",
                          formatear_unidades(df_sku["stock_distribucion"].sum()))

                conteo_clasif = df_sku["clasificacion"].value_counts()
                st.caption(
                    " · ".join(f"**{c}**: {conteo_clasif.get(c, 0):,}"
                              for c in ["Exhibir", "Pedir", "Recomendar compra", "Sin rotación"])
                )
                anios_hist = df_sku.attrs.get("anios_historia", [])
                st.caption(
                    f"Demanda estimada = promedio de tendencia reciente + "
                    f"{periodo_sku_label} de " + (
                        ", ".join(str(a) for a in anios_hist) if anios_hist
                        else "años anteriores (sin ninguno completo cargado todavía — "
                             "se usa solo la tendencia reciente)")
                )

                venta_cols = sorted(c for c in df_sku.columns if c.startswith("venta_"))
                etiquetas_venta = {c: f"Venta {c.split('_')[1]}" for c in venta_cols}

                vista = df_sku.drop(columns=["demanda_diaria"]).copy()
                vista["demanda_tendencia"] = vista["demanda_tendencia"].apply(formatear_unidades)
                vista["demanda_historica_prom"] = vista["demanda_historica_prom"].apply(
                    lambda v: formatear_unidades(v) if pd.notna(v) else "—")
                vista["demanda_estimada"] = vista["demanda_estimada"].apply(formatear_unidades)
                vista["valor_estimado"] = vista["valor_estimado"].apply(formatear_pesos)
                vista["stock_exhibir"] = vista["stock_exhibir"].apply(formatear_unidades)
                vista["stock_distribucion"] = vista["stock_distribucion"].apply(formatear_unidades)
                vista["cobertura_cd_dias"] = vista["cobertura_cd_dias"].apply(
                    lambda v: f"{v:,.0f} d" if pd.notna(v) else "—")
                for c in venta_cols:
                    vista[c] = vista[c].apply(formatear_unidades)
                vista = vista.rename(columns={
                    "codigo": "Código", "descripcion": "Descripción", "rubro": "Rubro",
                    "marca": "Marca", "clasificacion": "Clasificación",
                    "demanda_tendencia": "Demanda (tendencia)",
                    "demanda_historica_prom": "Demanda (años ant.)",
                    "demanda_estimada": "Demanda estimada", "valor_estimado": "Valor estimado",
                    "stock_exhibir": "Stock exhibir", "stock_distribucion": "Stock a pedir (CD)",
                    "cobertura_cd_dias": "Cobertura CD", **etiquetas_venta,
                })
                orden_cols = (["Código", "Descripción", "Rubro", "Marca", "Clasificación",
                               "Demanda (tendencia)", "Demanda (años ant.)", "Demanda estimada",
                               "Valor estimado", "Stock exhibir",
                               "Stock a pedir (CD)", "Cobertura CD"]
                              + [etiquetas_venta[c] for c in venta_cols])
                vista = vista[orden_cols]
                st.dataframe(vista.head(500), width="stretch", hide_index=True, height=420)
                if len(df_sku) > 500:
                    st.caption(
                        f"Mostrando 500 de {len(df_sku):,} SKUs — descargá el "
                        "listado completo.")
                botones_descarga(df_sku, "detalle_sku_estimacion_stock", "fc_sku")
