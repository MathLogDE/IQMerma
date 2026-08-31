"""ui/paginas/distribucion_diferencias.py — Diferencias de camión (MD)"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

COLOR_FALT = "#ff6b6b"
COLOR_SOBR = "#64ffda"


def render(proyecto):
    st.title("Diferencias de camión")
    st.caption(
        "Movimientos **REM / MD**: lo que el ERP registra cuando lo recibido "
        "no coincide con lo despachado. Cada remito mueve las unidades en "
        "disputa entre las dos puntas, mirado siempre desde el centro de "
        "distribución: **faltante** = la sucursal recibió de menos "
        "(sucursal → depósito); **sobrante** = recibió de más "
        "(depósito → sucursal)."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados todavía.")
        return

    suc_largo, suc_corto = etiquetas_sucursal(sucursales)
    suc_label = {c: v.split(" — ", 1)[-1] for c, v in suc_largo.items()}

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    # --- Período y valorización -----------------------------------------------
    # Los MD vienen con costo 0 en el ERP: se valorizan con el snapshot elegido.
    ctrl = controles_periodo(proyecto, "dif", desde_default="anio")
    if not ctrl:
        return
    f_desde, f_hasta = ctrl["fecha_desde"], ctrl["fecha_hasta"]
    modo, f_val = ctrl["modo_valorizacion"], ctrl["fecha_valorizacion"]

    # --- Depósitos y sucursales ----------------------------------------------
    d1, d2 = st.columns([1, 3])
    with d1:
        depos = st.multiselect(
            "Depósitos de distribución", sucursales["codigodepo"].tolist(),
            default=[d for d in DEPOSITOS_DISTRIBUCION
                     if d in set(sucursales["codigodepo"])],
            format_func=etiqueta_suc, key="dif_depos",
            help="Las diferencias se miden contra estos depósitos.")
    with d2:
        otras = [c for c in sucursales["codigodepo"] if c not in depos]
        sucs = st.multiselect("Sucursales (vacío = todas)", otras,
                              format_func=etiqueta_suc, key="dif_sucs")

    excluir = st.multiselect(
        "Excluir sucursales", [c for c in otras if c not in sucs],
        format_func=etiqueta_suc, key="dif_excl",
        help="Quedan fuera de todo el análisis: KPIs, gráficos, tablas y "
             "reporte. Sirve para sacar depósitos de roturas, outlets o "
             "e-commerce que distorsionan el resultado.")

    entre_dep = st.checkbox(
        "Incluir movimientos entre depósitos de distribución", value=False,
        key="dif_entre",
        help="Los MD de un depósito a otro (001 ↔ 002) no son faltantes de "
             "camión de una sucursal: quedan afuera salvo que los pidas.")

    if not depos:
        st.warning("Elegí al menos un depósito de distribución.")
        return

    # --- Recorte de catálogo -------------------------------------------------
    with st.expander("Filtrar por rubro / marca / SKU"):
        cat = catalogo_cacheado(proyecto)
        recorte, hay_filtro = filtros_catalogo(cat, "dif_cat", con_activo=True)
        if hay_filtro:
            st.caption(f"{len(recorte):,} SKUs en el recorte.")

    if st.button("Buscar diferencias"):
        with st.spinner("Cruzando las dos patas de cada remito..."):
            try:
                det = diferencias_camion(
                    proyecto, str(f_desde), str(f_hasta),
                    depositos=depos,
                    sucursales=sucs or None,
                    excluir_sucursales=excluir or None,
                    codigos=recorte["codigo"].tolist() if hay_filtro else None,
                    modo_valorizacion=modo, fecha_valorizacion=f_val,
                    incluir_entre_depositos=entre_dep)
                st.session_state["dif_det"] = det
                st.session_state["dif_transf"] = total_transferido(
                    proyecto, str(f_desde), str(f_hasta),
                    sucursales=sucs or None,
                    excluir_sucursales=excluir or None,
                    codigos=recorte["codigo"].tolist() if hay_filtro else None,
                    modo_valorizacion=modo, fecha_valorizacion=f_val)
            except Exception as e:
                st.error(f"Error: {e}")

    if "dif_det" not in st.session_state:
        return

    det = st.session_state["dif_det"]
    transf = st.session_state.get("dif_transf", pd.DataFrame())
    if det.empty:
        st.info("Sin diferencias de camión con esos filtros.")
        return

    st.markdown("---")

    # --- KPIs ----------------------------------------------------------------
    falt = det[det["sentido"] == FALTANTE]
    sobr = det[det["sentido"] == SOBRANTE]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Faltantes", formatear_pesos(falt["valorizado"].sum()),
              delta=f"{formatear_unidades(falt['unidades'].sum())} u",
              delta_color="off")
    k2.metric("Sobrantes", formatear_pesos(sobr["valorizado"].sum()),
              delta=f"{formatear_unidades(sobr['unidades'].sum())} u",
              delta_color="off")
    neto = falt["valorizado"].sum() - sobr["valorizado"].sum()
    k3.metric("Neto (falt. − sobr.)", formatear_pesos(neto),
              delta="a favor del depósito" if neto > 0 else "a favor de la sucursal",
              delta_color="off")
    k4.metric("Remitos con diferencia", f"{det['numero'].nunique():,}",
              delta=f"{det['codigo'].nunique():,} SKUs", delta_color="off")

    pie = (f"Valorizado a **{det.attrs['modo_valorizacion']}** del snapshot "
           f"{det.attrs['fecha_valorizacion']} · {det.attrs['desde']} → "
           f"{det.attrs['hasta']}.")
    if det.attrs.get("excluidas"):
        pie += (" Excluidas: **"
                + ", ".join(etiqueta_suc(s) for s in det.attrs["excluidas"])
                + f"** ({det.attrs['pares_excluidos']:,} líneas fuera del análisis).")
    if det.attrs.get("pares_sin_deposito"):
        pie += (f" No se cuentan {det.attrs['pares_sin_deposito']:,} MD entre dos "
                "sucursales (no pasan por un depósito de distribución).")
    sin_precio = det["precio_unitario"].isna().sum()
    if sin_precio:
        pie += (f" ⚠️ {sin_precio:,} filas sin precio en el snapshot: "
                "cuentan en unidades pero no en pesos.")
    st.caption(pie)

    # --- Contraste vs. total transferido --------------------------------------
    st.markdown("#### Diferencia vs. total transferido")
    if transf.empty:
        st.info("No hay remitos **RE**/**RI** en el período con esos filtros "
                "para contrastar la diferencia contra el volumen transferido.")
    else:
        st.caption(
            "Compara la diferencia total (faltante + sobrante) contra las "
            "unidades remitidas por **RE** (remito externo) y **RI** (remito "
            "interno) en el mismo período: cuánto pesa la diferencia frente "
            "al volumen que realmente se movió."
        )
        transf_tot_u = transf["unidades"].sum()
        transf_tot_val = transf["valorizado"].sum()
        dif_tot_u = falt["unidades"].sum() + sobr["unidades"].sum()
        dif_tot_val = falt["valorizado"].sum() + sobr["valorizado"].sum()
        ct1, ct2 = st.columns(2)
        ct1.metric("% de diferencia sobre lo transferido (u.)",
                   formatear_pct(dif_tot_u / transf_tot_u * 100 if transf_tot_u else None))
        ct2.metric("% de diferencia sobre lo transferido ($)",
                   formatear_pct(dif_tot_val / transf_tot_val * 100 if transf_tot_val else None))

        cont_suc = agregar_contraste_transferido(
            resumen_diferencias(det, por="sucursal"), transf, "sucursal")
        cont_suc = cont_suc.sort_values("pct_faltante_val", ascending=False, na_position="last")

        disp_c = cont_suc.copy()
        disp_c["sucursal"] = disp_c["sucursal"].map(etiqueta_suc)
        for c in ["transferido_u", "faltante_u", "sobrante_u"]:
            disp_c[c] = disp_c[c].apply(formatear_unidades)
        for c in ["transferido_val", "faltante_val", "sobrante_val"]:
            disp_c[c] = disp_c[c].apply(formatear_pesos)
        for c in ["pct_faltante_u", "pct_sobrante_u", "pct_faltante_val", "pct_sobrante_val"]:
            disp_c[c] = disp_c[c].apply(formatear_pct)
        disp_c = disp_c[["sucursal", "transferido_u", "transferido_val",
                         "faltante_u", "pct_faltante_u", "faltante_val", "pct_faltante_val",
                         "sobrante_u", "pct_sobrante_u", "sobrante_val", "pct_sobrante_val"]]
        disp_c.columns = ["Sucursal", "Transferido u.", "Transferido $",
                          "Faltante u.", "% Falt./transf. (u)", "Faltante $", "% Falt./transf. ($)",
                          "Sobrante u.", "% Sobr./transf. (u)", "Sobrante $", "% Sobr./transf. ($)"]
        st.dataframe(disp_c, width="stretch", hide_index=True)
        botones_descarga(cont_suc, "diferencias_vs_transferido_por_sucursal", "dif_transf_suc")

    # --- Evolución -----------------------------------------------------------
    e1, e2 = st.columns([3, 1])
    with e2:
        unidad = st.radio("Ver en", ["Pesos", "Unidades"], key="dif_unidad")
        freq = st.radio("Agrupar por", ["Mes", "Semana"], key="dif_freq",
                        horizontal=True)
    suf = "val" if unidad == "Pesos" else "u"
    fmt = formatear_pesos if unidad == "Pesos" else formatear_unidades

    ev = evolucion_diferencias(det, freq="M" if freq == "Mes" else "W")
    with e1:
        st.markdown("#### Evolución")
        fig = go.Figure()
        fig.add_bar(x=ev["periodo"], y=ev[f"faltante_{suf}"], name="Faltante",
                    marker_color=COLOR_FALT)
        fig.add_bar(x=ev["periodo"], y=-ev[f"sobrante_{suf}"], name="Sobrante",
                    marker_color=COLOR_SOBR)
        fig.add_scatter(x=ev["periodo"], y=ev[f"neto_{suf}"], name="Neto",
                        mode="lines+markers", line=dict(color="#ffd43b", width=2))
        fig.add_hline(y=0, line=dict(color="#8892b0", width=1))
        fig.update_layout(height=360, barmode="relative",
                          xaxis=dict(gridcolor=color_grilla()),
                          yaxis=dict(title=unidad, gridcolor=color_grilla()),
                          **layout_grafico())
        st.plotly_chart(fig, width="stretch")
        st.caption("Los sobrantes se dibujan hacia abajo para leer el neto de "
                   "un vistazo.")

    # --- Ranking -------------------------------------------------------------
    st.markdown("#### Ranking")
    dims = {"Sucursal": "sucursal", "Gran Super Rubro": "gran_super_rubro",
            "Rubro": "rubro", "Marca": "marca", "SKU": "codigo",
            "Usuario": "usuario", "Depósito": "deposito"}
    dim = st.selectbox("Agrupar por", list(dims), key="dif_dim")
    col = dims[dim]

    res = resumen_diferencias(det, por=col)
    if col == "codigo" and not transf.empty:
        res = agregar_contraste_transferido(res, transf, col)
    top = res.head(20).iloc[::-1]
    etiquetas = (top[col].map(lambda c: suc_corto.get(c, str(c)))
                 if col in ("sucursal", "deposito")
                 else top[col].astype(str).str.slice(0, 45))

    fig = go.Figure()
    fig.add_bar(y=etiquetas, x=top[f"faltante_{suf}"], name="Faltante",
                orientation="h", marker_color=COLOR_FALT)
    fig.add_bar(y=etiquetas, x=-top[f"sobrante_{suf}"], name="Sobrante",
                orientation="h", marker_color=COLOR_SOBR)
    fig.update_layout(height=max(320, 22 * len(top)), barmode="relative",
                      xaxis=dict(title=unidad, gridcolor=color_grilla()),
                      yaxis=dict(gridcolor=color_grilla()), **layout_grafico())
    st.plotly_chart(fig, width="stretch")

    disp = res.copy()
    if col in ("sucursal", "deposito"):
        disp[col] = disp[col].map(etiqueta_suc)
    for c in ["faltante_u", "sobrante_u", "neto_u", "remitos", "skus"]:
        disp[c] = disp[c].apply(formatear_unidades)
    for c in ["faltante_val", "sobrante_val", "neto_val"]:
        disp[c] = disp[c].apply(formatear_pesos)
    if "transferido_u" in disp.columns:
        disp["transferido_u"] = disp["transferido_u"].apply(formatear_unidades)
        disp["transferido_val"] = disp["transferido_val"].apply(formatear_pesos)
        for c in ["pct_faltante_u", "pct_sobrante_u", "pct_faltante_val", "pct_sobrante_val"]:
            disp[c] = disp[c].apply(formatear_pct)
        disp = disp[[col, "transferido_u", "transferido_val",
                     "faltante_u", "pct_faltante_u", "faltante_val", "pct_faltante_val",
                     "sobrante_u", "pct_sobrante_u", "sobrante_val", "pct_sobrante_val",
                     "neto_u", "neto_val", "remitos", "skus"]]
        disp.columns = [dim, "Transferido u.", "Transferido $",
                        "Faltante u.", "% Falt./transf. (u)", "Faltante $", "% Falt./transf. ($)",
                        "Sobrante u.", "% Sobr./transf. (u)", "Sobrante $", "% Sobr./transf. ($)",
                        "Neto u.", "Neto $", "Remitos", "SKUs"]
    else:
        disp.columns = [dim, "Faltante u.", "Sobrante u.", "Neto u.", "Faltante $",
                        "Sobrante $", "Neto $", "Remitos", "SKUs"]
    st.dataframe(disp, width="stretch", hide_index=True)
    botones_descarga(res, f"diferencias_camion_por_{col}", "dif_res")

    # --- Detalle -------------------------------------------------------------
    st.markdown("#### Detalle por remito")
    presentes = [s for s in (FALTANTE, SOBRANTE, ENTRE_DEPOSITOS)
                 if s in set(det["sentido"])]
    solo = st.multiselect("Sentido", presentes, default=presentes,
                          key="dif_sentido")
    d = det[det["sentido"].isin(solo)] if solo else det
    d = d.sort_values("valorizado", ascending=False)

    cols = ["fecha", "numero", "deposito", "sucursal", "sentido", "codigo",
            "descripcion", "rubro", "marca", "unidades", "valorizado", "usuario"]
    vista = d[cols].copy()
    vista["fecha"] = vista["fecha"].dt.date
    vista["sucursal"] = vista["sucursal"].map(etiqueta_suc)
    vista["unidades"] = vista["unidades"].apply(formatear_unidades)
    vista["valorizado"] = vista["valorizado"].apply(formatear_pesos)
    vista.columns = ["Fecha", "Remito", "Depósito", "Sucursal", "Sentido",
                     "Código", "Descripción", "Rubro", "Marca", "Unidades",
                     "Valorizado", "Usuario"]
    st.dataframe(vista.head(1000), width="stretch", hide_index=True, height=420)
    if len(d) > 1000:
        st.caption(f"Mostrando las 1.000 de mayor valor, de {len(d):,} — "
                   "descargá el listado completo.")
    botones_descarga(d[cols], "diferencias_camion_detalle", "dif_det")

    # --- Reporte imprimible --------------------------------------------------
    st.markdown("---")
    st.markdown("#### Reporte imprimible")
    r1, r2 = st.columns([1, 3])
    with r1:
        top_n = st.number_input("Filas por ranking", min_value=5, max_value=100,
                                value=25, step=5, key="dif_topn")
    meta_r = {
        "proyecto": proyecto,
        "desde": det.attrs["desde"],
        "hasta": det.attrs["hasta"],
        "depositos": ", ".join(det.attrs["depositos"]),
        "modo_valorizacion": ("costo" if det.attrs["modo_valorizacion"] == "costo"
                              else "precio de lista"),
        "fecha_valorizacion": det.attrs["fecha_valorizacion"] or "—",
        "sucursales": ", ".join(etiqueta_suc(s) for s in sucs) if sucs else "",
        "excluidas": ", ".join(etiqueta_suc(s) for s in det.attrs["excluidas"]),
        "nombres_sucursal": suc_label,
    }
    html = generar_reporte_diferencias(proyecto, meta_r, det, top_n=int(top_n))
    boton_reporte(html,
                  f"diferencias_camion_{det.attrs['desde']}_{det.attrs['hasta']}.html",
                  "dl_rep_dif")
