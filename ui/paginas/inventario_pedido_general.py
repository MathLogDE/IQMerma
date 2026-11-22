"""ui/paginas/inventario_pedido_general.py — Pedido general de distribución (pull)"""
import streamlit as st

from ui.comun import *  # noqa: F401,F403

# El costo escala con SKUs × sucursales elegidas (una sola reconstrucción de
# stock, pero sobre esa cantidad de combinaciones) — límite sobre el producto,
# no solo sobre los SKUs como en las páginas de una sola sucursal.
LIMITE_FILAS = 150_000


def render(proyecto):
    st.title("Pedido general de distribución")
    st.caption(
        "Sistema **pull**: elegí varias sucursales y armá de una sola vez la "
        "hoja de distribución — venta promedio ajustada y stock de cada "
        "sucursal, el pedido recomendado por sucursal y un pedido "
        "consolidado con el total a despachar desde el centro de "
        "distribución. No reemplaza a 'Pedido desde sucursal' (una sucursal "
        "a la vez, con hoja imprimible)."
    )

    sucursales_df = listar_sucursales(proyecto)
    if sucursales_df.empty:
        st.info("No hay movimientos cargados todavía.")
        return

    fmin, fmax = rango_disponible(proyecto)
    suc_largo, suc_corto = etiquetas_sucursal(sucursales_df)

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    todas = sucursales_df["codigodepo"].tolist()
    suc_sel = st.multiselect(
        "Sucursales a abastecer", todas, format_func=etiqueta_suc, key="pg_sucs")

    c1, c2, c3 = st.columns(3)
    with c1:
        f_desde = st.date_input("Desde", value=max(fmin, fmax.replace(month=1, day=1)),
                                min_value=fmin, max_value=fmax, key="pg_desde")
    with c2:
        f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                max_value=fmax, key="pg_hasta")
    with c3:
        dias_cobertura = st.number_input(
            "Cobertura (días)", min_value=1, value=15, step=1, key="pg_dias",
            help="Pedido recomendado por sucursal = venta diaria ajustada × "
                 "estos días, menos el stock actual de esa sucursal. Mismo "
                 "criterio para todas las sucursales elegidas.")

    st.markdown("##### Qué mirar")
    cat = catalogo_cacheado(proyecto)
    recorte, hay_filtro = filtros_catalogo(cat, "pg")

    if not hay_filtro:
        st.info("Elegí al menos un rubro, marca o palabra para acotar el pedido.")
        return
    if recorte.empty:
        st.warning("Ningún SKU del catálogo coincide con esos filtros.")
        return
    if not suc_sel:
        st.info("Elegí al menos una sucursal a abastecer.")
        return
    if len(recorte) * len(suc_sel) > LIMITE_FILAS:
        st.warning(
            f"El filtro alcanza {len(recorte):,} SKUs × {len(suc_sel)} "
            f"sucursales — acotá el filtro o la selección de sucursales "
            f"(máximo {LIMITE_FILAS:,} combinaciones) para que el cálculo "
            f"sea rápido.")
        return

    st.caption(f"**{len(recorte):,} SKUs** × **{len(suc_sel)} sucursal(es)** en el recorte.")

    if st.button("Calcular pedido general"):
        with st.spinner("Cruzando ventas y stock de todas las sucursales elegidas..."):
            try:
                p = perfil_pedido_general(
                    proyecto, suc_sel, str(f_desde), str(f_hasta),
                    codigos=recorte["codigo"].tolist())
                st.session_state["pg_perfil"] = p
                st.session_state["pg_sucs_calc"] = list(suc_sel)
            except Exception as e:
                st.error(f"Error: {e}")

    if "pg_perfil" not in st.session_state:
        return

    perfil = st.session_state["pg_perfil"]
    if perfil.empty:
        st.info("Sin movimientos en el rango para esas sucursales y ese recorte.")
        return

    tabla = tabla_pedido_general(perfil, dias_cobertura, suc_corto)

    st.markdown("---")
    sucs_calc = st.session_state["pg_sucs_calc"]
    st.caption(
        f"Venta ajustada sobre **{perfil.attrs['desde']} → {perfil.attrs['hasta']}** · "
        f"stock anclado al **{perfil.attrs['fecha_stock']}** · "
        f"cobertura **{dias_cobertura} días** · "
        f"sucursales: {', '.join(etiqueta_suc(s) for s in sucs_calc)}"
    )

    k1, k2, k3 = st.columns(3)
    k1.metric("SKUs", f"{len(tabla):,}")
    k2.metric("Sucursales", f"{len(sucs_calc)}")
    k3.metric("Pedido consolidado (u)", formatear_unidades(tabla["pedido_consolidado"].sum()))

    # --- Tabla -------------------------------------------------------------
    st.markdown("#### Detalle por SKU")
    st.caption(
        "**Pedido** por sucursal: vacío si ya sobra stock para la cobertura "
        "elegida en esa sucursal, **-1** si no hay datos para calcularlo "
        "(sin movimientos de esa sucursal en el período)."
    )
    cols_unidad = [c for c in tabla.columns
                  if c.startswith(("vta_prom_", "stock_", "pedido_", "uxb"))
                  or c == "pedido_consolidado"]
    cols_pesos = [c for c in ("costo", "lista_1") if c in tabla.columns]

    disp = tabla.copy()
    for c in cols_unidad:
        disp[c] = disp[c].apply(formatear_unidades)
    for c in cols_pesos:
        disp[c] = disp[c].apply(formatear_pesos)

    st.dataframe(disp.head(1000), width="stretch", hide_index=True)
    if len(disp) > 1000:
        st.caption(f"Mostrando 1.000 de {len(disp):,} — descargá el listado completo.")

    botones_descarga(tabla, "pedido_general_distribucion", "pg")
