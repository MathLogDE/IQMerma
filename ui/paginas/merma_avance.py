"""ui/paginas/merma_avance.py — Página Avance de inventario (Merma)"""
import streamlit as st
import pandas as pd

from ui.comun import *  # noqa: F401,F403


_ETQ_NIVEL = {"rubro": "Rubro", "super_rubro": "Super Rubro",
             "gran_super_rubro": "Gran Super Rubro"}
_ETQ_METODO = {"movimientos": "SKUs con movimiento en el período",
              "catalogo": "Catálogo activo"}


def render(proyecto):
    st.title("Avance de inventario")

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
        return
    suc_largo, _ = etiquetas_sucursal(sucursales)
    cat = catalogo_cacheado(proyecto)
    fmin, fmax = rango_disponible(proyecto)

    tab_gen, tab_conteo = st.tabs(["🗺️ Avance por sucursal", "🔢 Conteo y diferencias"])

    # ======================================================================
    # Avance por sucursal (matriz nivel × sucursal)
    # ======================================================================
    with tab_gen:
        cat_f, filtro_activo = filtros_catalogo(cat, "avg", con_activo=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            f_desde = st.date_input("Desde", value=fmin, min_value=fmin,
                                    max_value=fmax, key="avg_desde")
        with c2:
            f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                    max_value=fmax, key="avg_hasta")
        with c3:
            suc_sel = st.multiselect(
                "Sucursales (columnas, vacío = todas)",
                sucursales["codigodepo"].tolist(),
                format_func=lambda c: suc_largo.get(c, str(c)), key="avg_sucs")

        c4, c5 = st.columns(2)
        with c4:
            nivel = st.selectbox(
                "Nivel de agrupación", list(NIVELES_AVANCE),
                format_func=lambda x: _ETQ_NIVEL[x], key="avg_nivel")
        with c5:
            metodo = st.radio(
                "Método de cálculo del % avance", list(METODOS_DENOMINADOR),
                format_func=lambda x: _ETQ_METODO[x], key="avg_metodo",
                help="'SKUs con movimiento en el período': denominador = SKUs de ese "
                     "nivel con cualquier movimiento en el rango, por sucursal. "
                     "'Catálogo activo': denominador = SKUs activos del catálogo en "
                     "ese nivel, igual para todas las sucursales.",
            )

        if st.button("Calcular avance", key="avg_btn"):
            codigos_t = tuple(sorted(cat_f["codigo"])) if filtro_activo else None
            depo_t = tuple(sorted(suc_sel)) if suc_sel else None
            with st.spinner("Calculando..."):
                try:
                    df_mat = avance_matriz_cacheada(
                        proyecto, str(f_desde), str(f_hasta), nivel, metodo,
                        codigos_t, depo_t)
                    st.session_state["avg_df"] = df_mat
                    filtros_txt = []
                    if st.session_state.get("avg_gsr"):
                        filtros_txt.append("GSR: " + ", ".join(st.session_state["avg_gsr"]))
                    if st.session_state.get("avg_rub"):
                        filtros_txt.append("Rubro: " + ", ".join(st.session_state["avg_rub"]))
                    if st.session_state.get("avg_mar"):
                        filtros_txt.append("Marca: " + ", ".join(st.session_state["avg_mar"]))
                    if st.session_state.get("avg_txt"):
                        filtros_txt.append(f"Búsqueda: '{st.session_state['avg_txt']}'")
                    st.session_state["avg_meta"] = {
                        "proyecto":          proyecto,
                        "fecha_desde":       str(f_desde),
                        "fecha_hasta":       str(f_hasta),
                        "sucursales_filtro": etiqueta_seleccion_sucursales(suc_sel, suc_largo),
                        "filtros":           " · ".join(filtros_txt),
                    }
                except Exception as e:
                    st.error(f"Error: {e}")

        if "avg_df" in st.session_state:
            df_mat = st.session_state["avg_df"]
            suc_attrs = df_mat.attrs.get("sucursales", [])
            if not suc_attrs:
                st.info("Sin datos para el período y filtros elegidos.")
            else:
                cols_dep = [s["codigodepo"] for s in suc_attrs]
                etq = {s["codigodepo"]: s["etiqueta"] for s in suc_attrs}
                df_disp = df_mat.copy()
                for c in cols_dep:
                    df_disp[c] = df_disp[c].apply(formatear_pct)
                df_disp = df_disp.rename(columns=etq).rename(
                    columns={"nivel_valor": _ETQ_NIVEL[df_mat.attrs["nivel"]]})
                st.dataframe(df_disp, width="stretch", hide_index=True)
                botones_descarga(df_mat, "avance_matriz", "avg_dl")

                st.markdown("---")
                st.markdown("#### Reporte imprimible")
                html = generar_reporte_avance_general(
                    proyecto, st.session_state["avg_meta"], df_mat)
                nombre = (f"avance_general_{st.session_state['avg_meta']['fecha_desde']}_"
                         f"{st.session_state['avg_meta']['fecha_hasta']}.html")
                boton_reporte(html, nombre, "dl_rep_avance_general")

    # ======================================================================
    # Conteo y diferencias (totales, no distingue sucursal)
    # ======================================================================
    with tab_conteo:
        cat_f2, filtro_activo2 = filtros_catalogo(cat, "avc", con_activo=True)
        ctrl = controles_periodo(proyecto, "avc", modos=list(MODOS_VALORIZACION_MERMA))
        suc_sel2 = st.multiselect(
            "Sucursales (filtro, vacío = todas — no se desglosan)",
            sucursales["codigodepo"].tolist(),
            format_func=lambda c: suc_largo.get(c, str(c)), key="avc_sucs")

        if ctrl and st.button("Calcular conteo", key="avc_btn"):
            codigos_t = tuple(sorted(cat_f2["codigo"])) if filtro_activo2 else None
            depo_t = tuple(sorted(suc_sel2)) if suc_sel2 else None
            with st.spinner("Calculando..."):
                try:
                    df_c = avance_conteo_cacheada(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"],
                        ctrl["modo_valorizacion"], ctrl["fecha_valorizacion"],
                        codigos_t, depo_t)
                    st.session_state["avc_df"] = df_c
                    st.session_state["avc_ctrl"] = ctrl
                    st.session_state["avc_suc_label"] = etiqueta_seleccion_sucursales(
                        suc_sel2, suc_largo)
                    filtros_txt = []
                    if st.session_state.get("avc_gsr"):
                        filtros_txt.append("GSR: " + ", ".join(st.session_state["avc_gsr"]))
                    if st.session_state.get("avc_rub"):
                        filtros_txt.append("Rubro: " + ", ".join(st.session_state["avc_rub"]))
                    if st.session_state.get("avc_mar"):
                        filtros_txt.append("Marca: " + ", ".join(st.session_state["avc_mar"]))
                    if st.session_state.get("avc_txt"):
                        filtros_txt.append(f"Búsqueda: '{st.session_state['avc_txt']}'")
                    st.session_state["avc_filtros"] = " · ".join(filtros_txt)
                except Exception as e:
                    st.error(f"Error: {e}")

        if "avc_df" in st.session_state:
            df_c = st.session_state["avc_df"]
            skus_contados = len(df_c)
            unidades_contadas = float(df_c["unidades_contadas"].sum()) if not df_c.empty else 0.0
            diferencia_unidades = float(df_c["diferencia_unidades"].sum()) if not df_c.empty else 0.0
            diferencia_valorizada = float(df_c["diferencia_valorizada"].sum()) if not df_c.empty else 0.0

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("SKUs contados", f"{skus_contados:,}")
            k2.metric("Unidades contadas", formatear_unidades(unidades_contadas))
            k3.metric("Diferencia (u.)", formatear_unidades(diferencia_unidades))
            k4.metric("Diferencia valorizada", formatear_pesos(diferencia_valorizada))
            if not df_c.empty:
                botones_descarga(df_c, "avance_conteo_detalle", "avc_dl")

            st.markdown("---")
            st.markdown("#### Reporte imprimible")
            ctrl_c = st.session_state["avc_ctrl"]
            meta2 = {
                "proyecto":           proyecto,
                "sucursal":           st.session_state.get("avc_suc_label", ""),
                "fecha_desde":        ctrl_c["fecha_desde"],
                "fecha_hasta":        ctrl_c["fecha_hasta"],
                "modo":               ctrl_c["modo_valorizacion"],
                "fecha_valorizacion": ctrl_c["fecha_valorizacion"] or "—",
                "filtros":            st.session_state.get("avc_filtros", ""),
            }
            html2 = generar_reporte_avance_conteo(proyecto, meta2, df_c)
            nombre2 = f"avance_conteo_{meta2['fecha_desde']}_{meta2['fecha_hasta']}.html"
            boton_reporte(html2, nombre2, "dl_rep_avance_conteo")
