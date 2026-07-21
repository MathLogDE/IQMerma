"""ui/paginas/inventario_series.py — Serie de stock diaria y quiebres"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


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
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        f_gsr = st.multiselect(
            "Gran Super Rubro",
            sorted(res_full["gran_super_rubro"].dropna().unique().tolist()),
            key="ser_f_gsr")
    with fc2:
        f_txt = st.text_input("Buscar SKU / descripción", key="ser_f_txt")
    with fc3:
        solo_q = st.checkbox("Solo SKUs con quiebre", value=True, key="ser_f_q")

    res = res_full
    if f_gsr:
        res = res[res["gran_super_rubro"].isin(f_gsr)]
    if f_txt:
        t = f_txt.strip().lower()
        res = res[res["codigo"].str.lower().str.contains(t, na=False)
                  | res["descripcion"].str.lower().str.contains(t, na=False)]
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
    fig.update_layout(height=320, xaxis=dict(gridcolor="#1e2130"),
                      yaxis=dict(title="SKUs sin stock", gridcolor="#1e2130"),
                      **LAYOUT_OSCURO)
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

    # --- Detalle de un SKU ---------------------------------------------------
    st.markdown("#### Evolución diaria de un SKU")
    if res.empty:
        st.info("Sin SKUs con los filtros aplicados.")
        return
    opciones = res["codigo"].tolist()[:500]
    etiqueta = dict(zip(res["codigo"], res["descripcion"].fillna("")))
    sku = st.selectbox(
        "SKU", opciones,
        format_func=lambda c: f"{c} — {str(etiqueta.get(c, ''))[:60]}",
        key="ser_sku")

    d = serie_diaria(serie, sku)
    fig = go.Figure()
    fig.add_scatter(x=d["fecha"], y=d["stock"], mode="lines",
                    line=dict(color="#64ffda", width=2), name="Stock estimado")
    fig.add_hline(y=0, line=dict(color="#8892b0", width=1, dash="dot"))
    for ini, fin in _rachas_quiebre(d)[:60]:
        fig.add_vrect(x0=ini, x1=fin, fillcolor="#ff6b6b", opacity=0.18,
                      line_width=0)
    fig.update_layout(height=380, xaxis=dict(gridcolor="#1e2130"),
                      yaxis=dict(title="Unidades", gridcolor="#1e2130"),
                      **LAYOUT_OSCURO)
    st.plotly_chart(fig, width="stretch")
    st.caption("Las zonas rojas son los tramos sin stock (quiebre).")

    mov = serie[serie["codigo"] == sku][["fecha", "delta", "stock", "vigencia_dias"]].copy()
    mov["fecha"] = mov["fecha"].dt.date
    mov.columns = ["Fecha", "Movimiento", "Stock resultante", "Días vigente"]
    st.dataframe(mov, width="stretch", hide_index=True, height=260)
