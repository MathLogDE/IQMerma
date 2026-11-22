"""ui/paginas/merma_ultimos_movimientos.py — Último movimiento por tipo de comprobante"""
import streamlit as st
import pandas as pd

from ui.comun import *  # noqa: F401,F403

LIMITE_SKUS = 25_000


def render(proyecto):
    st.title("Últimos movimientos")
    st.caption(
        "Por SKU, la fecha del último comprobante registrado de cada tipo "
        "(FA, FB, INV, RI, NCB, CS, TR...) en una sucursal — pensado para "
        "exportar a Excel."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados todavía.")
        return

    suc_largo, _ = etiquetas_sucursal(sucursales)
    suc_sel = st.selectbox(
        "Sucursal", sucursales["codigodepo"].tolist(),
        format_func=lambda c: suc_largo.get(c, str(c)), key="ultmov_suc")

    cat = catalogo_cacheado(proyecto)
    recorte, _ = filtros_catalogo(cat, "ultmov", con_activo=True)
    if recorte.empty:
        st.warning("Ningún SKU del catálogo coincide con esos filtros.")
        return
    if len(recorte) > LIMITE_SKUS:
        st.warning(f"El filtro alcanza {len(recorte):,} SKUs. Acotalo un poco "
                   f"más (máximo {LIMITE_SKUS:,}).")
        return
    st.caption(f"**{len(recorte):,} SKUs** en el recorte.")

    if st.button("Calcular"):
        with st.spinner("Buscando últimos movimientos..."):
            try:
                df = ultimo_movimiento_por_tipo(
                    proyecto, suc_sel, codigos=recorte["codigo"].tolist())
                st.session_state["ultmov_df"] = df
                st.session_state["ultmov_tipos"] = df.attrs.get("tipos", [])
            except Exception as e:
                st.error(f"Error: {e}")

    if "ultmov_df" not in st.session_state:
        return

    df = st.session_state["ultmov_df"]
    tipos = st.session_state.get("ultmov_tipos", [])
    disp = df.merge(recorte[["codigo", "descripcion", "rubro", "marca"]],
                    on="codigo", how="left")

    cols = ["codigo", "descripcion", "rubro", "marca"] + tipos
    tabla = disp[cols].copy()
    for t in tipos:
        tabla[t] = tabla[t].apply(lambda v: v.isoformat() if pd.notna(v) else "—")
    tabla.columns = ["Código", "Descripción", "Rubro", "Marca"] + tipos

    st.markdown("---")
    st.dataframe(tabla.head(1000), width="stretch", hide_index=True)
    if len(disp) > 1000:
        st.caption(f"Mostrando 1.000 de {len(disp):,} — descargá el listado completo.")

    botones_descarga(disp[cols], "ultimos_movimientos", "ultmov")
