"""ui/paginas/inventario_cruce.py — Cruce de sucursales por rubro / marca"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403

COLORES_SUC = px.colors.qualitative.Light24

# Arriba de este tamaño la reconstrucción empieza a tardar y el gráfico deja
# de decir algo: conviene acotar el filtro.
LIMITE_SKUS = 25_000


def render(proyecto):
    st.title("Cruce por sucursal")
    st.caption(
        "Elegí un período y un recorte de catálogo (gran super rubro, rubro, "
        "marca o una palabra) y compará cómo evolucionó el stock de ese "
        "recorte en las sucursales que quieras. Usa la misma reconstrucción "
        "día a día que la Serie de stock."
    )

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)
    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
        return

    fmin, fmax = rango_disponible(proyecto)
    suc_largo, suc_corto = etiquetas_sucursal(sucursales)
    suc_label = {c: v.split(" — ", 1)[-1] for c, v in suc_largo.items()}

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    def corto_suc(c):
        return suc_corto.get(c, str(c))

    # --- Período y sucursales ------------------------------------------------
    c1, c2, c3 = st.columns(3)
    with c1:
        f_desde = st.date_input("Desde", value=max(fmin, fmax.replace(month=1, day=1)),
                                min_value=fmin, max_value=fmax, key="crz_desde")
    with c2:
        f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                max_value=fmax, key="crz_hasta")
    with c3:
        f_snap = st.selectbox(
            "Anclar al stock del", snapshots, key="crz_snap",
            help="Snapshot desde el que se camina hacia atrás/adelante.")

    todas = sucursales["codigodepo"].tolist()
    sucs = st.multiselect("Sucursales a comparar", todas, default=todas[:3],
                          format_func=etiqueta_suc, key="crz_sucs")

    # --- Recorte de catálogo -------------------------------------------------
    st.markdown("##### Qué mirar")
    cat = catalogo_cacheado(proyecto)
    recorte, hay_filtro = filtros_catalogo(cat, "crz", con_activo=True)

    if not hay_filtro:
        st.info("Elegí al menos un rubro, marca o palabra para acotar el análisis.")
        return
    if recorte.empty:
        st.warning("Ningún SKU del catálogo coincide con esos filtros.")
        return
    if len(recorte) > LIMITE_SKUS:
        st.warning(
            f"El filtro alcanza {len(recorte):,} SKUs. Acotalo un poco más "
            f"(máximo {LIMITE_SKUS:,}) para que el cruce sea rápido y legible.")
        return

    st.caption(f"**{len(recorte):,} SKUs** en el recorte · "
               f"{len(sucs)} sucursal(es) seleccionada(s).")

    if st.button("Analizar cruce") and sucs:
        with st.spinner("Reconstruyendo stock por sucursal..."):
            try:
                s = serie_stock(proyecto, sucs, str(f_desde), str(f_hasta),
                                fecha_stock=f_snap,
                                codigos=recorte["codigo"].tolist())
                st.session_state["cruce_serie"] = s
                st.session_state["cruce_agg"] = stock_por_sucursal(s)
            except Exception as e:
                st.error(f"Error: {e}")

    if "cruce_agg" not in st.session_state:
        return

    serie = st.session_state["cruce_serie"]
    agg = st.session_state["cruce_agg"]
    if agg.empty:
        st.info("Sin movimientos en el rango para esas sucursales y ese recorte.")
        return

    st.markdown("---")

    # --- KPIs ----------------------------------------------------------------
    ini = agg[agg["fecha"] == agg["fecha"].min()]["stock"].sum()
    fin = agg[agg["fecha"] == agg["fecha"].max()]["stock"].sum()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Sucursales con datos", f"{agg['codigodepo'].nunique():,}")
    k2.metric("SKUs con movimiento", f"{serie['codigo'].nunique():,}")
    k3.metric("Stock inicial (u)", formatear_unidades(ini))
    k4.metric("Stock final (u)", formatear_unidades(fin),
              delta=f"{(fin - ini) / abs(ini) * 100:+.1f}%" if ini else None)
    st.caption(f"Anclado al stock del **{serie.attrs['fecha_stock']}** · "
               f"{serie.attrs['desde']} → {serie.attrs['hasta']}.")

    # --- Evolución -----------------------------------------------------------
    e1, e2 = st.columns([2, 3])
    with e1:
        metrica = st.radio(
            "Métrica", ["Stock (unidades)", "Base 100", "SKUs en quiebre"],
            horizontal=True, key="crz_metrica",
            help="Base 100 normaliza cada sucursal contra su primer día: "
                 "compara la evolución sin que el tamaño tape la tendencia.")
    with e2:
        marcar = st.checkbox(
            "Marcar remitos / inventario físico / ajustes de merma", value=False,
            key="crz_marcas", disabled=(metrica != "Stock (unidades)"),
            help="Una X por día y sucursal, sumando todos los SKUs del "
                 "recorte (no por sucursal individual como en Serie de "
                 "stock). Solo disponible en la vista de unidades.")

    titulo = {"Stock (unidades)": "Unidades",
              "Base 100": "Índice (día inicial = 100)",
              "SKUs en quiebre": "SKUs sin stock"}[metrica]

    # Base 100 solo tiene sentido con un stock inicial positivo: si la
    # reconstrucción arranca en 0 o en negativo (movimientos que no reconcilian
    # con el snapshot), el índice se invierte y el gráfico miente.
    sin_base = []
    if metrica == "Base 100":
        base_dep = agg.sort_values("fecha").groupby("codigodepo")["stock"].first()
        sin_base = sorted(base_dep[base_dep <= 0].index.tolist())

    fig = go.Figure()
    for i, dep in enumerate(sorted(agg["codigodepo"].unique())):
        if dep in sin_base:
            continue
        d = agg[agg["codigodepo"] == dep].sort_values("fecha")
        if metrica == "Stock (unidades)":
            y = d["stock"]
        elif metrica == "Base 100":
            y = d["stock"] / d["stock"].iloc[0] * 100
        else:
            y = d["skus_en_quiebre"]
        fig.add_scatter(
            x=d["fecha"], y=y, mode="lines", name=corto_suc(dep),
            line=dict(color=COLORES_SUC[i % len(COLORES_SUC)], width=2),
            hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f}<extra>" + dep + "</extra>")
    if metrica == "Base 100":
        fig.add_hline(y=100, line=dict(color="#8892b0", width=1, dash="dot"))
    if metrica == "Stock (unidades)" and marcar:
        marcas = marcadores_sucursal(serie, agg)
        for tipo, cfg in TIPOS_MARCA_SUC.items():
            m = marcas[(marcas["tipo"] == tipo) & (~marcas["codigodepo"].isin(sin_base))]
            if m.empty:
                continue
            fig.add_scatter(
                x=m["fecha"], y=m["stock"], mode="markers", name=cfg["etiqueta"],
                marker=dict(symbol=cfg["simbolo"], size=10, color=cfg["color"],
                           line=dict(width=1.5, color=cfg["color"])),
                customdata=m[["codigodepo", "delta"]],
                hovertemplate=(cfg["etiqueta"] + " %{customdata[0]}<br>%{x|%d/%m/%Y}"
                              "<br>%{customdata[1]:+,.0f} u<extra></extra>"))
    fig.update_layout(height=430, xaxis=dict(gridcolor=color_grilla()),
                      yaxis=dict(title=titulo, gridcolor=color_grilla()),
                      **layout_grafico())
    st.plotly_chart(fig, width="stretch")
    if marcar and metrica == "Stock (unidades)":
        st.caption(
            "Marcadores: " + " · ".join(
                f"**{cfg['etiqueta']}**" for cfg in TIPOS_MARCA_SUC.values())
            + " — suman todos los SKUs del recorte por sucursal y día."
        )
    if sin_base:
        st.caption(
            "Fuera del índice por arrancar el período con stock ≤ 0 (no se "
            "puede indexar): **" + ", ".join(etiqueta_suc(d) for d in sin_base)
            + "**. Miralas en unidades.")

    # --- Evolución por categoría -----------------------------------------------
    st.markdown("#### Evolución por categoría")
    st.caption(
        "El mismo gráfico, pero sumando las sucursales elegidas y abriendo "
        "por rubro, marca o gran super rubro — para ver qué categoría explica "
        "el movimiento del stock, más allá de en qué sucursal está."
    )
    dim_dispo = {"gran_super_rubro": "Gran Super Rubro", "rubro": "Rubro", "marca": "Marca"}
    dc1, dc2, dc3 = st.columns([1, 2, 1])
    with dc1:
        dimension = st.selectbox(
            "Desagregar por", list(dim_dispo), format_func=lambda d: dim_dispo[d],
            key="crz_cat_dim")
    with dc2:
        metrica_cat = st.radio(
            "Métrica", ["Stock (unidades)", "Base 100", "SKUs en quiebre"],
            horizontal=True, key="crz_cat_metrica")
    with dc3:
        top_n_cat = st.selectbox("Mostrar top", [10, 15, 20, 30], key="crz_cat_topn")

    agg_cat = stock_por_categoria(serie, dimension)

    titulo_cat = {"Stock (unidades)": "Unidades",
                  "Base 100": "Índice (día inicial = 100)",
                  "SKUs en quiebre": "SKUs sin stock"}[metrica_cat]

    orden_cat = (agg_cat.sort_values("fecha").groupby(dimension)["stock"].last()
                 .sort_values(ascending=False).head(top_n_cat).index.tolist())

    sin_base_cat = []
    if metrica_cat == "Base 100":
        base_cat = agg_cat.sort_values("fecha").groupby(dimension)["stock"].first()
        sin_base_cat = [c for c in orden_cat if base_cat.get(c, 0) <= 0]

    fig_cat = go.Figure()
    for i, cat in enumerate(orden_cat):
        if cat in sin_base_cat:
            continue
        d = agg_cat[agg_cat[dimension] == cat].sort_values("fecha")
        if metrica_cat == "Stock (unidades)":
            y = d["stock"]
        elif metrica_cat == "Base 100":
            y = d["stock"] / d["stock"].iloc[0] * 100
        else:
            y = d["skus_en_quiebre"]
        fig_cat.add_scatter(
            x=d["fecha"], y=y, mode="lines", name=str(cat),
            line=dict(color=COLORES_SUC[i % len(COLORES_SUC)], width=2),
            hovertemplate="%{x|%d/%m/%Y}<br>%{y:,.0f}<extra>" + str(cat) + "</extra>")
    if metrica_cat == "Base 100":
        fig_cat.add_hline(y=100, line=dict(color="#8892b0", width=1, dash="dot"))
    fig_cat.update_layout(height=430, xaxis=dict(gridcolor=color_grilla()),
                          yaxis=dict(title=titulo_cat, gridcolor=color_grilla()),
                          **layout_grafico())
    st.plotly_chart(fig_cat, width="stretch")

    n_cats = agg_cat[dimension].nunique()
    if sin_base_cat:
        st.caption(
            "Fuera del índice por arrancar el período con stock ≤ 0: **"
            + ", ".join(str(c) for c in sin_base_cat) + "**. Miralas en unidades.")
    if n_cats > top_n_cat:
        st.caption(f"Mostrando las {top_n_cat} categorías con mayor stock final, "
                   f"de {n_cats:,} en el recorte.")

    # --- Tabla por sucursal --------------------------------------------------
    st.markdown("#### Resumen por sucursal")
    g = agg.sort_values("fecha").groupby("codigodepo")
    res = pd.DataFrame({
        "stock_inicial": g["stock"].first(),
        "stock_final":   g["stock"].last(),
        "stock_prom":    g["stock"].mean(),
        "stock_min":     g["stock"].min(),
        "stock_max":     g["stock"].max(),
        "skus":          g["skus"].max(),
        "quiebre_prom":  g["skus_en_quiebre"].mean(),
    }).reset_index()
    res["variacion_pct"] = ((res["stock_final"] - res["stock_inicial"])
                            / res["stock_inicial"].replace(0, pd.NA) * 100)
    res["pct_skus_quiebre"] = res["quiebre_prom"] / res["skus"].replace(0, pd.NA) * 100
    res["sucursal"] = res["codigodepo"].map(suc_label)

    cols = ["codigodepo", "sucursal", "skus", "stock_inicial", "stock_final",
            "variacion_pct", "stock_prom", "stock_min", "stock_max",
            "quiebre_prom", "pct_skus_quiebre"]
    disp = res[cols].copy()
    for c in ["skus", "stock_inicial", "stock_final", "stock_min", "stock_max"]:
        disp[c] = disp[c].apply(formatear_unidades)
    for c in ["stock_prom", "quiebre_prom"]:
        disp[c] = disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
    for c in ["variacion_pct", "pct_skus_quiebre"]:
        disp[c] = disp[c].apply(formatear_pct)
    disp.columns = ["Código", "Sucursal", "SKUs", "Stock inicial", "Stock final",
                    "Variación", "Stock prom", "Stock mín", "Stock máx",
                    "SKUs en quiebre (prom)", "% SKUs en quiebre"]
    st.dataframe(disp, width="stretch", hide_index=True)
    botones_descarga(res[cols], "cruce_sucursales", "crz")

    # --- Detalle diario ------------------------------------------------------
    with st.expander("Ver el detalle diario (una fila por día y sucursal)"):
        det = agg.copy()
        det["fecha"] = det["fecha"].dt.date
        det["sucursal"] = det["codigodepo"].map(suc_label)
        det = det[["fecha", "codigodepo", "sucursal", "stock",
                   "skus_en_quiebre", "skus"]]
        det.columns = ["Fecha", "Código", "Sucursal", "Stock",
                       "SKUs en quiebre", "SKUs"]
        st.dataframe(det, width="stretch", hide_index=True, height=300)

    # --- Reporte imprimible ---------------------------------------------------
    st.markdown("---")
    meta_crz = {
        "proyecto": proyecto,
        "desde": serie.attrs["desde"],
        "hasta": serie.attrs["hasta"],
        "fecha_stock": serie.attrs["fecha_stock"],
        "recorte": f"{len(recorte):,} SKUs",
        "sucursales": ", ".join(etiqueta_suc(s) for s in sucs) if sucs else "todas",
        "skus": int(serie["codigo"].nunique()),
        "nombres_sucursal": suc_label,
    }
    html_crz = generar_reporte_cruce(proyecto, meta_crz, agg)
    boton_reporte(html_crz, f"cruce_sucursales_{meta_crz['fecha_stock']}.html", "dl_rep_crz")
