"""ui/paginas/merma_matriz.py — Página Matriz por sucursal (Merma)"""
import streamlit as st

from ui.comun import *  # noqa: F401,F403


# Tope de filas de la vista previa: a nivel SKU la matriz puede tener decenas
# de miles de filas × (sucursales × 7) columnas; el Excel lleva todo.
_TOPE_PREVIA = 1000


def render(proyecto):
    st.title("Matriz por sucursal")
    st.caption(
        "Por rubro, gran super rubro, marca o SKU, y por sucursal: diferencias "
        "de inventario y ajustes (unidades y $), SKUs inventariados y venta "
        "del período. SKUs activos (flag del ERP) va una sola vez por fila, "
        "igual para todas las sucursales. "
        "Se descarga en Excel con una columna agrupada por sucursal."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
        return
    suc_largo, _ = etiquetas_sucursal(sucursales)
    abrevs = abreviaciones_sucursal(sucursales)
    cat = catalogo_cacheado(proyecto)

    cat_f, filtro_activo = filtros_catalogo(cat, "mms")
    ctrl = controles_periodo(proyecto, "mms", modos=list(MODOS_VALORIZACION_MERMA))

    c1, c2 = st.columns([3, 1])
    with c1:
        suc_sel = st.multiselect(
            "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
            format_func=lambda c: suc_largo.get(c, str(c)), key="mms_sucs")
    with c2:
        nivel = st.selectbox(
            "Filas por", ["rubro", "gran_super_rubro", "marca", "codigo"],
            format_func=lambda x: {"rubro": "Rubro", "gran_super_rubro": "Gran Super Rubro",
                                   "marca": "Marca", "codigo": "SKU"}[x],
            key="mms_nivel")

    if ctrl and st.button("Calcular", key="mms_btn"):
        codigos_t = tuple(sorted(cat_f["codigo"])) if filtro_activo else None
        depo_t = tuple(sorted(suc_sel)) if suc_sel else None
        with st.spinner("Calculando..."):
            try:
                df_l = merma_matriz_cacheada(
                    proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"], nivel,
                    ctrl["modo_valorizacion"], ctrl["fecha_valorizacion"],
                    codigos_t, depo_t)
                filtros_txt = []
                for k, etq in (("mms_gsr", "GSR"), ("mms_rub", "Rubro"), ("mms_mar", "Marca")):
                    if st.session_state.get(k):
                        filtros_txt.append(f"{etq}: " + ", ".join(st.session_state[k]))
                if st.session_state.get("mms_txt"):
                    filtros_txt.append(f"Búsqueda: '{st.session_state['mms_txt']}'")
                st.session_state["mms_df"] = df_l
                st.session_state["mms_param"] = {
                    "Período": f"{ctrl['fecha_desde']} → {ctrl['fecha_hasta']}",
                    "Sucursales": etiqueta_seleccion_sucursales(suc_sel, suc_largo),
                    "Valorización": ETIQUETAS_MODO_VALORIZACION.get(
                        ctrl["modo_valorizacion"], ctrl["modo_valorizacion"]),
                    "Snapshot de valorización": str(df_l.attrs.get("fecha_valorizacion")),
                    "Filtros": " · ".join(filtros_txt) or "—",
                    "Signo": "Inventario/Ajustes: faltante positivo, sobrante negativo",
                }
            except Exception as e:
                st.error(f"Error: {e}")

    if "mms_df" not in st.session_state:
        return

    df_l = st.session_state["mms_df"]
    param = st.session_state["mms_param"]
    if df_l.empty:
        st.info("Sin movimientos para el período y filtros elegidos.")
        return

    # Opciones de salida: se aplican sobre el resultado ya calculado.
    grupos = grupos_matriz_merma(df_l)
    o1, o2, o3 = st.columns([1, 1, 3])
    signo_real = o1.checkbox(
        "Signo real", value=True, key="mms_signo_real",
        help="Inventario y Ajustes con el signo del movimiento (sobrante "
             "positivo, faltante negativo) en vez del signo de merma.")
    miles = o2.checkbox("$ en miles", value=False, key="mms_miles",
                        help="Valorizados (inventario, ajustes y venta) divididos por 1000.")
    grupos_sel = o3.multiselect("Mostrar", grupos, default=grupos, key="mms_grupos")
    if not grupos_sel:
        st.info("Elegí al menos una métrica para mostrar.")
        return

    ancho = matriz_merma_ancha(df_l, abrevs, signo_real=signo_real, miles=miles,
                               grupos=grupos_sel)
    param = {
        **param,
        "Signo": ("Inventario/Ajustes: signo real (sobrante positivo, faltante negativo)"
                  if signo_real else
                  "Inventario/Ajustes: signo de merma (faltante positivo, sobrante negativo)"),
        "Valorizados": "en miles de $" if miles else "en $",
        "Métricas": ", ".join(grupos_sel),
    }
    st.caption(f"{param['Período']} · {param['Sucursales']} · {param['Valorización']} "
               f"(stock del {param['Snapshot de valorización']}) · "
               f"{formatear_unidades(len(ancho))} filas")

    previa = ancho.head(_TOPE_PREVIA).copy()
    previa.columns = [f"{s} · {m}" if m else s for s, m in previa.columns]
    st.dataframe(previa, width="stretch")
    if len(ancho) > _TOPE_PREVIA:
        st.caption(f"Vista previa de las primeras {_TOPE_PREVIA} filas; el Excel trae todas.")

    desde, hasta = param["Período"].split(" → ")
    st.download_button(
        "⬇️ Descargar Excel", a_excel_matriz_merma(ancho, param),
        f"matriz_merma_{df_l.attrs['nivel']}_{desde}_{hasta}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="mms_dl")
