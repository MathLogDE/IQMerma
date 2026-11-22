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
DESCRIPCION_MODELO = {
    "estacional_robusto": {
        "calculo": "Estacionalidad multiplicativa por mes calendario + tendencia "
                   "robusta (mediana de pendientes, Theil-Sen), amortiguada para que "
                   "una racha reciente no se extrapole al infinito. Es el piso "
                   "determinístico: siempre disponible desde 12 meses de historia.",
        "fortalezas": "Nunca falla, es robusto a valores atípicos y fácil de auditar "
                      "a mano (no es una caja negra).",
        "cuidados": "No captura relaciones más complejas que estación + tendencia; "
                   "la tendencia amortiguada puede subestimar una racha sostenida real.",
    },
    "holt_winters": {
        "calculo": "Suavizado exponencial (Holt-Winters) con tendencia amortiguada; "
                   "estacionalidad multiplicativa solo si hay 24+ meses de historia "
                   "(si no, ajusta sin estacionalidad).",
        "fortalezas": "Se adapta rápido a cambios de nivel recientes, sin necesitar "
                      "tanta historia como SARIMA.",
        "cuidados": "Con menos de 24 meses corre sin estacionalidad — puede no "
                   "anticipar bien un pico de temporada.",
    },
    "sarima": {
        "calculo": "SARIMAX estacional (1,1,1)x(1,1,1,12) — modela la "
                   "autocorrelación de la serie de forma explícita. Solo compite "
                   "con 30+ meses de historia.",
        "fortalezas": "El más preciso cuando el patrón estacional+tendencia es "
                      "consistente y hay historia suficiente.",
        "cuidados": "Más lento de ajustar; puede no converger en series muy "
                   "ruidosas o al borde del mínimo de historia.",
    },
    "kNN": {
        "calculo": "El grupo no tiene 12 meses propios: se le presta el patrón "
                   "estacional y la tendencia relativa de los grupos más parecidos "
                   "(similitud coseno del índice estacional), escalado al nivel "
                   "propio del grupo.",
        "fortalezas": "Da una estimación razonable cuando no hay historia propia "
                      "suficiente para ajustar cualquiera de los otros modelos.",
        "cuidados": "El patrón viene de OTROS grupos, no del propio — la banda de "
                   "error queda deliberadamente más ancha.",
    },
}


def _descripcion_modelo(modelo: str) -> dict:
    return DESCRIPCION_MODELO.get(modelo, DESCRIPCION_MODELO["kNN"] if modelo.startswith("kNN")
                                  else {})


SEGMENTOS_INTERMITENTE = ("intermitente",)
SEGMENTOS_POOLED_UI = ("continuo", "erratico", "lumpy")
DESCRIPCION_METODO_SKU = {
    "Automático (recomendado por la Fase 5)": {
        "calculo": "Por fila: LightGBM pooled si el segmento es continuo/errático/"
                   "lumpy, Baseline si es intermitente — la combinación que ganó "
                   "en cada segmento en la evaluación comparativa (Fase 5).",
        "fortalezas": "No hay que elegir a mano por segmento; ya incorpora lo "
                      "medido en el backtest.",
        "cuidados": "Es una mezcla, no un solo método — al comparar dos SKUs de "
                   "distinto segmento en realidad estás comparando dos métodos "
                   "distintos.",
    },
    "Baseline (tendencia+histórico)": {
        "calculo": "Promedio de la tendencia reciente (últimos 90 días "
                   "extrapolados) y el promedio histórico del mismo rango de "
                   "meses en años anteriores.",
        "fortalezas": "Simple y transparente — y en SKUs intermitentes midió "
                      "igual o mejor que Croston/TSB en la Fase 5.",
        "cuidados": "En continuo/errático/lumpy midió peor que LightGBM pooled "
                   "(WMAPE 112% vs. 72%).",
    },
    "Croston/TSB": {
        "calculo": "Compite Croston clásico vs. TSB por error de backtest — "
                   "pensados para demanda intermitente (vende salteado).",
        "fortalezas": "TSB corrige el sesgo de Croston cuando un SKU deja de "
                      "venderse del todo.",
        "cuidados": "Medido en la Fase 5: acá el Baseline le ganó (WMAPE 161% "
                   "vs. 175%) — no forzarlo todavía sin revisar caso por caso.",
    },
    "LightGBM pooled": {
        "calculo": "Un solo modelo (LightGBM, objetivo tweedie) entrenado con "
                   "todos los SKUs continuo/errático/lumpy juntos — lags, medias "
                   "móviles, rubro/marca/precio como features.",
        "fortalezas": "Midió mejor que el Baseline en la Fase 5 (WMAPE 72% vs. "
                      "112%) — el candidato más sólido a reemplazar el baseline "
                      "en este segmento.",
        "cuidados": "72% de WMAPE sigue siendo alto en términos absolutos — "
                   "errático/lumpy es difícil para cualquier método.",
    },
}


def _demanda_por_metodo(row, metodo: str) -> float:
    seg, base = row["segmento"], row["demanda_estimada"]
    if metodo == "Baseline (tendencia+histórico)":
        return base
    if metodo == "Croston/TSB":
        return (row["demanda_croston_tsb"]
                if seg in SEGMENTOS_INTERMITENTE and pd.notna(row["demanda_croston_tsb"])
                else base)
    if metodo == "LightGBM pooled":
        return (row["demanda_lgbm_pooled"]
                if seg in SEGMENTOS_POOLED_UI and pd.notna(row["demanda_lgbm_pooled"])
                else base)
    # "Automático": el ganador de la Fase 5 por segmento.
    if seg in SEGMENTOS_POOLED_UI and pd.notna(row["demanda_lgbm_pooled"]):
        return row["demanda_lgbm_pooled"]
    return base


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
    # "modelo" tiene que entrar en la clave si existe: si no, la agrupación
    # trimestral/semestral sumaría el forecast de modelos distintos entre sí.
    claves = [c for c in ("grupo", "tipo", "modelo") if c in out.columns] + ["mes"]
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

                # df_fc trae TODOS los candidatos ajustados (para el desplegable
                # "Ver otro modelo" de más abajo); las vistas agregadas (tabla de
                # todos los grupos, reporte HTML) siempre usan el ganador de cada
                # grupo — mezclar modelos distintos por grupo ahí sería confuso.
                _modelo_ganador_por_fila = df_fc["grupo"].astype(str).map(modelos_elegidos)
                df_fc_ganador = df_fc[(df_fc["tipo"] == "real")
                                      | (df_fc["modelo"] == _modelo_ganador_por_fila)].copy()
                df_fc_ganador.attrs = dict(df_fc.attrs)  # el filtrado no propaga .attrs solo

                grupos = sorted(df_fc["grupo"].unique().tolist())
                grupo_sel = (st.selectbox("Grupo", grupos, key="fc_grupo")
                             if len(grupos) > 1 else grupos[0])

                mes_objetivo_str = st.session_state.get("fc_mes_objetivo", "")
                mes_objetivo_label = st.session_state.get("fc_mes_objetivo_label", mes_objetivo_str)

                g_mensual_todos = df_fc[df_fc["grupo"] == grupo_sel]
                modelo_ganador_g = modelos_elegidos.get(str(grupo_sel))
                detalle_g = mape_por_modelo.get(str(grupo_sel), {})
                modelos_disponibles_g = sorted(
                    g_mensual_todos.loc[g_mensual_todos["tipo"] == "forecast", "modelo"]
                    .dropna().unique().tolist(),
                    key=lambda m: (m != modelo_ganador_g, m),  # el ganador va primero
                )
                if modelos_disponibles_g:
                    modelo_ver_sel = st.selectbox(
                        "Ver otro modelo", modelos_disponibles_g, index=0,
                        format_func=lambda m: (f"{NOMBRES_MODELO.get(m, m)} (elegido)"
                                               if m == modelo_ganador_g
                                               else NOMBRES_MODELO.get(m, m)),
                        key="fc_modelo_ver",
                    )
                    desc_g = _descripcion_modelo(modelo_ver_sel)
                    if desc_g:
                        st.caption(
                            f"**Cómo se calcula:** {desc_g['calculo']}  \n"
                            f"**Fortalezas:** {desc_g['fortalezas']}  \n"
                            f"**Cuidados:** {desc_g['cuidados']}"
                        )
                else:
                    modelo_ver_sel = modelo_ganador_g

                g_mensual = g_mensual_todos[
                    (g_mensual_todos["tipo"] == "real")
                    | (g_mensual_todos["modelo"] == modelo_ver_sel)]
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
                mape_ver = detalle_g.get(
                    modelo_ver_sel,
                    mapes.get(str(grupo_sel)) if modelo_ver_sel == modelo_ganador_g else None)
                k3.metric("Error esperado (MAPE backtest)",
                          f"{mape_ver:.0f}%" if mape_ver is not None else "—",
                          help="Del modelo que estás viendo arriba (\"Ver otro modelo\"), "
                               "no siempre el ganador. Promedio del error porcentual al "
                               "predecir los últimos 3 meses reales.")

                # --- competencia de modelos para este grupo (backtest) ------
                if modelo_ganador_g:
                    if detalle_g:
                        otros = ", ".join(
                            f"{NOMBRES_MODELO.get(m, m)}: {v:.0f}%"
                            for m, v in sorted(detalle_g.items(), key=lambda kv: kv[1])
                            if m != modelo_ganador_g)
                        st.caption(
                            f"**Modelo elegido (ganador del backtest):** "
                            f"{NOMBRES_MODELO.get(modelo_ganador_g, modelo_ganador_g)} "
                            f"— compitió contra {otros} (backtest de 3 meses)."
                        )
                    else:
                        st.caption(
                            f"**Modelo:** {NOMBRES_MODELO.get(modelo_ganador_g, modelo_ganador_g)}")

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
                    name=f"Forecast ({NOMBRES_MODELO.get(modelo_ver_sel, modelo_ver_sel)})",
                    x=fc["mes"], y=fc["unidades"],
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

                # --- tabla de proyección (todos los grupos, modelo ganador) ----
                st.markdown("#### Proyección por grupo")
                st.caption(
                    "Siempre con el modelo ganador de cada grupo — el "
                    "desplegable \"Ver otro modelo\" de arriba solo afecta el "
                    "detalle del grupo seleccionado."
                )
                df_fc_fore = _agrupar_periodo(
                    df_fc_ganador[df_fc_ganador["tipo"] == "forecast"], agrupacion)
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
                    proyecto, meta_f, df_fc_ganador, grupo_detalle=grupo_sel)
                boton_reporte(html_f, f"forecast_{mes_objetivo_str}.html", "dl_rep_fc")

                if excluidos:
                    st.caption(
                        f"{len(excluidos)} grupos sin proyección por historia "
                        f"insuficiente (< {MIN_MESES_KNN} meses, ni siquiera para "
                        f"prestar patrón de otro grupo): {', '.join(excluidos[:8])}"
                        + ("…" if len(excluidos) > 8 else "")
                    )

        # --- Forecast semanal con feriados/eventos AR (Prophet) -------------
        st.markdown("---")
        st.markdown("#### 📅 Forecast semanal con feriados/eventos AR (Prophet)")
        st.caption(
            "Proyección **semanal** — separada de la mensual de arriba — con "
            "un calendario de feriados oficiales argentinos + Día de la Madre "
            "y Día del Niño como regresor explícito: a nivel mensual un "
            "feriado puntual queda diluido en todo el mes, acá no. "
            "Disponible en Total, Gran Super Rubro y Sucursal (Rubro y Marca "
            "quedan afuera: cada ajuste de Prophet es mucho más caro que los "
            "modelos mensuales). Los grupos con un patrón \"todo o nada\" "
            "(casi sin venta casi todo el año, pico enorme y puntual — un "
            "caso límite para cualquier modelo estacional) se excluyen si el "
            "backtest da un error demasiado alto, en vez de mostrar un "
            "forecast roto."
        )
        c1, c2, c3 = st.columns(3)
        nivel_sem = c1.selectbox(
            "Nivel", NIVELES_SEMANAL, format_func=lambda n: ETIQUETA_NIVEL.get(n, n),
            key="fc_sem_nivel",
        )
        horizonte_sem = c2.number_input(
            "Semanas a proyectar", min_value=4, max_value=52, value=12, step=1,
            key="fc_sem_horizonte",
        )
        metrica_sem = c3.selectbox(
            "Métrica", list(METRICAS), format_func=lambda m: ETIQUETA_METRICA[m],
            key="fc_sem_metrica",
        )

        if st.button("Proyectar semanal", key="fc_sem_btn"):
            with st.spinner("Ajustando Prophet por grupo (backtest + final, "
                            "puede tardar hasta ~1 min)..."):
                try:
                    st.session_state["df_fc_sem"] = forecast_ventas_semanal_cacheado(
                        proyecto, nivel=nivel_sem, horizonte=int(horizonte_sem),
                        metrica=metrica_sem,
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_fc_sem" in st.session_state:
            df_sem = st.session_state["df_fc_sem"]
            if df_sem.empty:
                st.info("Sin datos suficientes para proyectar a nivel semanal.")
            else:
                grupos_sem = sorted(df_sem["grupo"].unique().tolist())
                grupo_sem_sel = (st.selectbox("Grupo", grupos_sem, key="fc_sem_grupo")
                                 if len(grupos_sem) > 1 else grupos_sem[0])
                g_sem = df_sem[df_sem["grupo"] == grupo_sem_sel]
                reales_sem = g_sem[g_sem["tipo"] == "real"].sort_values("semana")
                fc_sem = g_sem[g_sem["tipo"] == "forecast"].sort_values("semana")

                w_g = df_sem.attrs.get("wmape", {}).get(str(grupo_sem_sel))
                if w_g is not None:
                    st.caption(
                        f"**WMAPE de backtest:** {w_g:.0f}% — comparando contra las "
                        "últimas semanas reales, sin que el modelo las haya visto."
                    )

                fig_sem = go.Figure()
                fig_sem.add_scatter(
                    name="Banda 95%", x=fc_sem["semana"], y=fc_sem["banda_sup"],
                    mode="lines", line=dict(width=0), showlegend=False,
                )
                fig_sem.add_scatter(
                    name="Banda 95%", x=fc_sem["semana"], y=fc_sem["banda_inf"],
                    mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(255,169,77,0.15)",
                )
                fig_sem.add_scatter(
                    name="Real", x=reales_sem["semana"], y=reales_sem["unidades"],
                    mode="lines", line=dict(color="#64ffda", width=2),
                )
                fig_sem.add_scatter(
                    name="Forecast", x=fc_sem["semana"], y=fc_sem["unidades"],
                    mode="lines+markers", line=dict(color="#ffa94d", width=2, dash="dash"),
                    marker=dict(size=6),
                )
                fig_sem.update_layout(
                    height=420,
                    xaxis=dict(gridcolor=color_grilla()),
                    yaxis=dict(title="Unidades / semana", gridcolor=color_grilla()),
                    **layout_grafico(),
                )
                st.plotly_chart(fig_sem, width="stretch")
                botones_descarga(df_sem, "forecast_semanal", "fc_sem")

                excluidos_sem = df_sem.attrs.get("grupos_excluidos", [])
                if excluidos_sem:
                    st.caption(
                        f"{len(excluidos_sem)} grupos sin proyección semanal: "
                        + "; ".join(excluidos_sem[:6])
                        + ("…" if len(excluidos_sem) > 6 else "")
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

        with st.expander("Segmentación de demanda por SKU (ADI/CV²)"):
            st.caption(
                "Clasifica cada SKU por patrón de demanda: **continuo** (vende "
                "seguido, cantidades parecidas), **intermitente** (vende "
                "salteado pero en cantidades parecidas), **errático** (vende "
                "seguido pero en cantidades muy variables), **lumpy** (salteado "
                "y variable — el caso más difícil) y **sin historia suficiente** "
                "(< 6 meses con venta, no se puede clasificar con confianza). "
                "Es la base para elegir, más adelante, qué método de forecast "
                "usar en cada segmento."
            )
            df_seg = clasificar_demanda_sku_cacheada(proyecto)
            conteo_seg = (df_seg[df_seg["segmento"] != "sin_ventas"]["segmento"]
                          .value_counts().reindex(
                              ["continuo", "intermitente", "erratico", "lumpy",
                               "sin_historia_suficiente"]).fillna(0).astype(int))
            st.bar_chart(conteo_seg)

        with st.expander("Evaluación comparativa: ¿el método nuevo mejora al baseline?"):
            st.caption(
                "Croston/TSB y LightGBM pooled se muestran hoy como **segunda "
                "opinión** al lado de la estimación de tendencia — ninguno la "
                "reemplaza todavía. Esto compara, con el mismo backtest (mismos "
                "meses de holdout, mismo WMAPE) para los dos lados, si conviene "
                "hacerlo en algún segmento. El baseline se reproduce a nivel "
                "mensual (tendencia reciente + histórico del mismo mes en años "
                "anteriores) porque la función real usa una ventana diaria y no "
                "admite \"rebobinar\" el reloj — es una aproximación directa, no "
                "una llamada a la función que ves arriba."
            )
            if st.button("Evaluar modelos (~1-2 min)", key="fc_eval_btn"):
                with st.spinner("Corriendo el mismo backtest para baseline y "
                                "método nuevo en cada segmento..."):
                    try:
                        st.session_state["df_eval"] = evaluar_modelos_cacheado(proyecto)
                    except Exception as e:
                        st.error(f"Error: {e}")

            if "df_eval" in st.session_state:
                df_eval = st.session_state["df_eval"]
                if df_eval.empty:
                    st.info("Sin SKUs suficientes en estos segmentos para evaluar.")
                else:
                    for segmento_ev, g_ev in df_eval.groupby("segmento"):
                        g_ev = g_ev.sort_values("wmape")
                        mejor = g_ev.iloc[0]
                        gana_nuevo = bool(mejor["es_nuevo"])
                        veredicto = (
                            f"✅ el método nuevo ({mejor['metodo']}) tiene menor "
                            f"WMAPE — candidato a reemplazar el baseline acá"
                            if gana_nuevo else
                            f"➖ el baseline sigue con menor WMAPE — no reemplazar "
                            f"todavía en este segmento"
                        )
                        st.markdown(f"**{segmento_ev}** ({int(mejor['n_skus']):,} SKUs) — {veredicto}")
                        metodos_seg = g_ev["metodo"].tolist()
                        vista_ev = g_ev[["metodo", "wmape"]].rename(
                            columns={"metodo": "Método", "wmape": "WMAPE backtest"})
                        vista_ev["WMAPE backtest"] = vista_ev["WMAPE backtest"].apply(
                            lambda v: f"{v:.0f}%" if pd.notna(v) else "—")
                        st.dataframe(vista_ev, hide_index=True, width="stretch")

                        curva_seg = df_eval.attrs.get("curva_mensual", {}).get(segmento_ev)
                        if curva_seg is not None and not curva_seg.empty:
                            metodo_ver_eval = st.selectbox(
                                "Ver curva de", metodos_seg,
                                index=metodos_seg.index(mejor["metodo"]),
                                key=f"fc_eval_metodo_{segmento_ev}",
                            )
                            desc_eval = DESCRIPCION_METODO_SKU.get(metodo_ver_eval, {})
                            if desc_eval:
                                st.caption(
                                    f"**Cómo se calcula:** {desc_eval['calculo']}  \n"
                                    f"**Fortalezas:** {desc_eval['fortalezas']}  \n"
                                    f"**Cuidados:** {desc_eval['cuidados']}"
                                )
                            fig_eval = go.Figure()
                            fig_eval.add_scatter(
                                name="Real", x=curva_seg["mes"], y=curva_seg["real"],
                                mode="lines+markers", line=dict(color="#64ffda", width=2),
                            )
                            fig_eval.add_scatter(
                                name=metodo_ver_eval, x=curva_seg["mes"],
                                y=curva_seg[metodo_ver_eval],
                                mode="lines+markers",
                                line=dict(color="#ffa94d", width=2, dash="dash"),
                            )
                            fig_eval.update_layout(
                                height=300,
                                xaxis=dict(gridcolor=color_grilla()),
                                yaxis=dict(title="Unidades/mes (agregado del segmento, holdout)",
                                          gridcolor=color_grilla()),
                                **layout_grafico(),
                            )
                            st.plotly_chart(fig_eval, width="stretch")

        cat_sku = catalogo_cacheado(proyecto)
        recorte_sku, hay_filtro_sku = filtros_catalogo(cat_sku, "fc_sku", con_activo=True)

        default_suc = ([suc_sel] if (nivel != "sucursal" and suc_sel != TODAS) else [])
        sucs_exhibir = st.multiselect(
            "Sucursales a exhibir (vacío = todas, sin depósitos de logística)",
            sucursales["codigodepo"].tolist(), default=default_suc,
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
            key="fc_sku_sucs",
        )

        def _sumar_forecast_en_periodo(df_modelo: pd.DataFrame, columna: str) -> pd.Series:
            """Suma las filas 'forecast' de un módulo de forecasting por SKU
            (Croston/TSB o LightGBM pooled) dentro de [mes_desde_sku, mes_hasta_sku],
            para compararlo con `estimar_demanda_stock_sku` en el mismo período."""
            if df_modelo.empty:
                return pd.Series(name=columna, dtype="float64")
            en_periodo = df_modelo[
                (df_modelo["tipo"] == "forecast")
                & (df_modelo["mes"] >= mes_desde_sku_str)
                & (df_modelo["mes"] <= mes_hasta_sku_str)
            ]
            return en_periodo.groupby("codigo")["unidades"].sum().rename(columna)

        if st.button("Calcular estimación y stock", key="fc_sku_btn"):
            with st.spinner("Calculando demanda estimada y stock "
                            "(primera vez puede tardar ~1 min: Croston/TSB + LightGBM)..."):
                try:
                    df_calc = estimar_demanda_stock_sku(
                        proyecto, mes_desde=mes_desde_sku_str, mes_hasta=mes_hasta_sku_str,
                        codigos=recorte_sku["codigo"].tolist() if hay_filtro_sku else None,
                        sucursales_exhibir=sucs_exhibir or None,
                    )
                    if not df_calc.empty:
                        df_calc = df_calc.merge(
                            clasificar_demanda_sku_cacheada(proyecto)[["codigo", "segmento"]],
                            on="codigo", how="left",
                        )
                        df_interm = forecast_intermitente_cacheado(proyecto, horizonte=MESES_A_PROYECTAR)
                        df_calc = df_calc.merge(
                            _sumar_forecast_en_periodo(df_interm, "demanda_croston_tsb"),
                            on="codigo", how="left")
                        df_pooled = forecast_pooled_cacheado(proyecto, horizonte=MESES_A_PROYECTAR)
                        df_calc = df_calc.merge(
                            _sumar_forecast_en_periodo(df_pooled, "demanda_lgbm_pooled"),
                            on="codigo", how="left")
                        st.session_state["fc_sku_wmape_pooled"] = df_pooled.attrs.get("wmape")
                    st.session_state["df_sku"] = df_calc
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_sku" in st.session_state:
            df_sku = st.session_state["df_sku"]
            if df_sku.empty:
                st.info("Sin ventas ni stock para ese recorte.")
            else:
                metodo_sku_sel = st.selectbox(
                    "Método para Demanda estimada / Clasificación",
                    list(DESCRIPCION_METODO_SKU),
                    key="fc_sku_metodo",
                )
                desc_sku = DESCRIPCION_METODO_SKU[metodo_sku_sel]
                st.caption(
                    f"**Cómo se calcula:** {desc_sku['calculo']}  \n"
                    f"**Fortalezas:** {desc_sku['fortalezas']}  \n"
                    f"**Cuidados:** {desc_sku['cuidados']}"
                )
                if metodo_sku_sel in ("Croston/TSB", "LightGBM pooled"):
                    st.caption(
                        f"Fuera de su segmento, \"{metodo_sku_sel}\" no aplica — esas "
                        "filas caen al Baseline."
                    )

                df_sku = df_sku.assign(
                    demanda_activa=df_sku.apply(
                        lambda r: _demanda_por_metodo(r, metodo_sku_sel), axis=1))
                precio_unit = (df_sku["valor_estimado"]
                              / df_sku["demanda_estimada"].where(df_sku["demanda_estimada"] > 0))
                df_sku["valor_activa"] = (df_sku["demanda_activa"] * precio_unit).fillna(0.0)

                hay_exhibir = df_sku["stock_exhibir"] > 0
                hay_distribucion = df_sku["stock_distribucion"] > 0
                hay_demanda_activa = df_sku["demanda_activa"] > 0
                clasificacion_activa = pd.Series("Sin rotación", index=df_sku.index)
                clasificacion_activa[~hay_exhibir & hay_distribucion] = "Pedir"
                clasificacion_activa[~hay_exhibir & ~hay_distribucion] = "Recomendar compra"
                clasificacion_activa[hay_exhibir & hay_demanda_activa] = "Exhibir"
                df_sku["clasificacion"] = clasificacion_activa

                m1, m2, m3 = st.columns(3)
                m1.metric("Demanda estimada (período)",
                          formatear_pesos(df_sku["valor_activa"].sum()))
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
                if df_sku["segmento"].eq("intermitente").any():
                    st.caption(
                        "Para SKUs **intermitentes** (venden salteado) se suma la "
                        "columna **Demanda (Croston/TSB)**: un método pensado para "
                        "ese patrón — compite Croston contra TSB por error de "
                        "backtest, ver el segmento en el expander de arriba. Es una "
                        "segunda opinión al lado de la estimación de tendencia, no "
                        "un reemplazo todavía."
                    )
                if df_sku["segmento"].isin(["continuo", "erratico", "lumpy"]).any():
                    wmape_pooled = st.session_state.get("fc_sku_wmape_pooled")
                    wmape_txt = f" (WMAPE de backtest: {wmape_pooled:.0f}%)" if wmape_pooled else ""
                    st.caption(
                        "Para SKUs **continuo/errático/lumpy** se suma la columna "
                        "**Demanda (LightGBM pooled)**: un solo modelo entrenado con "
                        "todos esos SKUs juntos (no uno por SKU — a esa escala no "
                        "alcanza la señal individual)" + wmape_txt + ". También es una "
                        "segunda opinión al lado de la estimación de tendencia, no un "
                        "reemplazo — especialmente en errático/lumpy, donde ningún "
                        "método rinde realmente bien."
                    )

                venta_cols = sorted(c for c in df_sku.columns if c.startswith("venta_"))
                etiquetas_venta = {c: f"Venta {c.split('_')[1]}" for c in venta_cols}

                vista = df_sku.drop(columns=["demanda_diaria"]).copy()
                vista["demanda_tendencia"] = vista["demanda_tendencia"].apply(formatear_unidades)
                vista["demanda_historica_prom"] = vista["demanda_historica_prom"].apply(
                    lambda v: formatear_unidades(v) if pd.notna(v) else "—")
                vista["demanda_estimada"] = vista["demanda_estimada"].apply(formatear_unidades)
                vista["demanda_croston_tsb"] = vista["demanda_croston_tsb"].apply(
                    lambda v: formatear_unidades(v) if pd.notna(v) else "—")
                vista["demanda_lgbm_pooled"] = vista["demanda_lgbm_pooled"].apply(
                    lambda v: formatear_unidades(v) if pd.notna(v) else "—")
                vista["demanda_activa"] = vista["demanda_activa"].apply(formatear_unidades)
                vista["valor_estimado"] = vista["valor_estimado"].apply(formatear_pesos)
                vista["valor_activa"] = vista["valor_activa"].apply(formatear_pesos)
                vista["stock_exhibir"] = vista["stock_exhibir"].apply(formatear_unidades)
                vista["stock_distribucion"] = vista["stock_distribucion"].apply(formatear_unidades)
                vista["cobertura_cd_dias"] = vista["cobertura_cd_dias"].apply(
                    lambda v: f"{v:,.0f} d" if pd.notna(v) else "—")
                for c in venta_cols:
                    vista[c] = vista[c].apply(formatear_unidades)
                vista = vista.rename(columns={
                    "codigo": "Código", "descripcion": "Descripción", "rubro": "Rubro",
                    "marca": "Marca", "clasificacion": "Clasificación",
                    "segmento": "Segmento demanda",
                    "demanda_tendencia": "Demanda (tendencia)",
                    "demanda_historica_prom": "Demanda (años ant.)",
                    "demanda_estimada": "Demanda estimada",
                    "demanda_croston_tsb": "Demanda (Croston/TSB)",
                    "demanda_lgbm_pooled": "Demanda (LightGBM pooled)",
                    "demanda_activa": "Demanda elegida",
                    "valor_estimado": "Valor estimado (baseline)",
                    "valor_activa": "Valor elegido",
                    "stock_exhibir": "Stock exhibir", "stock_distribucion": "Stock a pedir (CD)",
                    "cobertura_cd_dias": "Cobertura CD", **etiquetas_venta,
                })
                orden_cols = (["Código", "Descripción", "Rubro", "Marca", "Clasificación",
                               "Segmento demanda",
                               "Demanda (tendencia)", "Demanda (años ant.)", "Demanda estimada",
                               "Demanda (Croston/TSB)", "Demanda (LightGBM pooled)",
                               "Demanda elegida",
                               "Valor estimado (baseline)", "Valor elegido", "Stock exhibir",
                               "Stock a pedir (CD)", "Cobertura CD"]
                              + [etiquetas_venta[c] for c in venta_cols])
                vista = vista[orden_cols]
                st.dataframe(vista.head(500), width="stretch", hide_index=True, height=420)
                if len(df_sku) > 500:
                    st.caption(
                        f"Mostrando 500 de {len(df_sku):,} SKUs — descargá el "
                        "listado completo.")
                botones_descarga(df_sku, "detalle_sku_estimacion_stock", "fc_sku")

        # --- Export semanal por SKU (Prophet): rubro/marca/sucursal, año a año --
        st.markdown("---")
        st.markdown("#### 🗓️ Export semanal por SKU (Prophet, comparación año a año)")
        st.caption(
            "Para un recorte chico de SKUs (por ejemplo un rubro puntual) corre "
            "Prophet **por SKU** — igual que el forecast semanal de arriba, pero "
            "a nivel SKU y filtrable por rubro/marca — y arma una planilla con la "
            "demanda prevista semana a semana junto con la demanda real de esa "
            "misma semana (mismo número de semana ISO) en años anteriores, para "
            "planear compras y distribución. Más lento que el forecast agregado: "
            "cada SKU (y cada sucursal elegida) es una corrida de Prophet propia, "
            "por eso conviene acotar bien el filtro."
        )

        recorte_exp, _ = filtros_catalogo(cat_sku, "fc_exp", con_activo=True)
        c1, c2, c3 = st.columns(3)
        sucursales_exp = c1.multiselect(
            "Sucursales (vacío = todas sumadas)", sucursales["codigodepo"].tolist(),
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
            key="fc_exp_sucs",
        )
        horizonte_exp = c2.number_input(
            "Semanas a proyectar", min_value=4, max_value=52, value=14, step=1,
            key="fc_exp_horizonte",
        )
        anios_exp = c3.number_input(
            "Años de historia a comparar", min_value=1, max_value=5, value=3, step=1,
            key="fc_exp_anios",
        )

        n_skus_exp = len(recorte_exp)
        n_combinaciones_exp = n_skus_exp * max(len(sucursales_exp), 1)
        if n_skus_exp == 0:
            st.info("Sin SKUs para este recorte — ajustá el filtro de rubro/marca/búsqueda.")
        elif n_combinaciones_exp > LIMITE_COMBINACIONES:
            st.warning(
                f"{n_skus_exp} SKUs × {max(len(sucursales_exp), 1)} sucursal(es) = "
                f"{n_combinaciones_exp} corridas de Prophet — supera el límite de "
                f"{LIMITE_COMBINACIONES}. Acotá el filtro de rubro/marca o de "
                "sucursales para poder generar."
            )
        else:
            st.caption(f"{n_combinaciones_exp} corrida(s) de Prophet para este recorte.")
            if st.button("Generar export semanal por SKU", key="fc_exp_btn"):
                with st.spinner(
                    f"Ajustando Prophet para {n_combinaciones_exp} combinación(es) "
                    "SKU×sucursal (puede tardar varios minutos)..."
                ):
                    try:
                        st.session_state["df_exp_sku"] = forecast_semanal_sku_cacheado(
                            proyecto, codigos=tuple(recorte_exp["codigo"].tolist()),
                            codigodepos=tuple(sucursales_exp) if sucursales_exp else None,
                            horizonte=int(horizonte_exp), anios_comparacion=int(anios_exp),
                        )
                        st.session_state["fc_exp_anios_usado"] = int(anios_exp)
                    except Exception as e:
                        st.error(f"Error: {e}")

        if "df_exp_sku" in st.session_state:
            df_exp = st.session_state["df_exp_sku"]
            if df_exp.empty:
                st.info("Sin datos suficientes para proyectar ese recorte.")
            else:
                anios_usado = st.session_state.get("fc_exp_anios_usado", 3)
                orden_serie_exp = ["Prevista"] + [f"Año-{k}" for k in range(1, anios_usado + 1)]
                vista_exp = df_exp.assign(
                    serie=df_exp["anio_offset"].map(
                        lambda k: "Prevista" if k == 0 else f"Año-{k}")
                )
                tabla_exp = vista_exp.pivot_table(
                    index=["codigo", "descripcion", "rubro", "marca"],
                    columns=["semana", "serie"], values="unidades",
                    aggfunc=lambda s: s.sum(min_count=1),
                ).round(0)
                semanas_exp = sorted(tabla_exp.columns.get_level_values(0).unique())
                cols_exp_ordenadas = [(s, serie) for s in semanas_exp for serie in orden_serie_exp
                                      if (s, serie) in tabla_exp.columns]
                tabla_exp = tabla_exp[cols_exp_ordenadas]
                stock_actual_exp = df_exp.attrs.get("stock_actual") or {}
                if stock_actual_exp:
                    tabla_exp.insert(
                        0, ("Stock actual", ""),
                        tabla_exp.index.get_level_values("codigo").map(stock_actual_exp),
                    )
                st.dataframe(tabla_exp, width="stretch")

                dc1, dc2 = st.columns(2)
                dc1.download_button(
                    "⬇ Excel (semana × año, formateado)",
                    a_excel_forecast_semanal_sku(df_exp, anios_usado, stock_actual_exp),
                    "forecast_semanal_sku.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="fc_exp_xlsx_formateado",
                )
                with dc2:
                    botones_descarga(df_exp, "forecast_semanal_sku_detalle", "fc_exp")

                wmape_exp = df_exp.attrs.get("wmape", {})
                if wmape_exp:
                    st.caption(
                        "WMAPE de backtest promedio (entre sucursales elegidas) por "
                        "SKU: "
                        + ", ".join(f"{c}: {w:.0f}%" for c, w in
                                   sorted(wmape_exp.items(), key=lambda kv: -kv[1])[:10])
                        + ("…" if len(wmape_exp) > 10 else "")
                    )
                excluidos_exp = df_exp.attrs.get("excluidos", [])
                if excluidos_exp:
                    st.caption(
                        f"{len(excluidos_exp)} SKU(s) o combinación(es) sin "
                        "proyección (poca historia o backtest con error alto): "
                        + "; ".join(excluidos_exp[:8])
                        + ("…" if len(excluidos_exp) > 8 else "")
                    )
