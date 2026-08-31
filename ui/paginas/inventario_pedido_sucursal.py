"""ui/paginas/inventario_pedido_sucursal.py — Pedido desde sucursal"""
import streamlit as st

from ui.comun import *  # noqa: F401,F403

LIMITE_SKUS = 25_000
LIMITE_IMPRESION = 800


def render(proyecto):
    st.title("Pedido desde sucursal")
    st.caption(
        "Hoja de pedido por artículo para una sucursal: venta promedio "
        "ajustada, stock disponible, últimos movimientos y una cantidad "
        "recomendada para la cobertura elegida — con una columna en blanco "
        "para que el repositor anote lo que decide pedir."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados todavía.")
        return

    fmin, fmax = rango_disponible(proyecto)
    suc_largo, _ = etiquetas_sucursal(sucursales)

    def etiqueta_suc(c):
        return suc_largo.get(c, str(c))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        suc_sel = st.selectbox(
            "Sucursal", sucursales["codigodepo"].tolist(),
            format_func=etiqueta_suc, key="ped_suc")
    with c2:
        f_desde = st.date_input("Desde", value=max(fmin, fmax.replace(month=1, day=1)),
                                min_value=fmin, max_value=fmax, key="ped_desde")
    with c3:
        f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                max_value=fmax, key="ped_hasta")
    with c4:
        dias_cobertura = st.number_input(
            "Cobertura (días)", min_value=1, value=15, step=1, key="ped_dias",
            help="Pedido recomendado = venta diaria ajustada × estos días, "
                 "menos el stock actual de la sucursal.")

    st.markdown("##### Qué mirar")
    cat = catalogo_cacheado(proyecto)
    recorte, hay_filtro = filtros_catalogo(cat, "ped")

    if not hay_filtro:
        st.info("Elegí al menos un rubro, marca o palabra para acotar el pedido.")
        return
    if recorte.empty:
        st.warning("Ningún SKU del catálogo coincide con esos filtros.")
        return
    if len(recorte) > LIMITE_SKUS:
        st.warning(
            f"El filtro alcanza {len(recorte):,} SKUs. Acotalo un poco más "
            f"(máximo {LIMITE_SKUS:,}) para que el cálculo sea rápido.")
        return

    st.caption(f"**{len(recorte):,} SKUs** en el recorte.")

    if st.button("Calcular pedido"):
        with st.spinner("Cruzando remitos, ventas y stock..."):
            try:
                p = perfil_reposicion(
                    proyecto, suc_sel, str(f_desde), str(f_hasta),
                    codigos=recorte["codigo"].tolist(), umbral_barrera=0)
                st.session_state["ped_perfil"] = p
            except Exception as e:
                st.error(f"Error: {e}")

    if "ped_perfil" not in st.session_state:
        return

    perfil = st.session_state["ped_perfil"]
    if perfil.empty:
        st.info("Sin movimientos en el rango para esa sucursal y ese recorte.")
        return

    disp = perfil.merge(recorte[["codigo", "ean"]], on="codigo", how="left")
    disp["pedido_recomendado"] = pedido_por_dias(disp, dias_cobertura)

    st.markdown("---")
    ocultar_sin_stock_cd = st.checkbox(
        "Ocultar SKUs sin stock en depósito 001", value=False, key="ped_sin_stock_cd",
        help="Si no hay nada para pedir de inmediato al centro de distribución, "
             "no tiene sentido incluirlo en la hoja de pedido.")
    if ocultar_sin_stock_cd:
        disp = disp[disp["stock_cd_001"] > 0]

    st.caption(
        f"Venta ajustada sobre **{perfil.attrs['desde']} → {perfil.attrs['hasta']}** · "
        f"stock anclado al **{perfil.attrs['fecha_stock']}** · sucursal "
        f"**{etiqueta_suc(suc_sel)}** · cobertura **{dias_cobertura} días**"
    )

    # --- Tabla -------------------------------------------------------------
    st.markdown("#### Detalle por SKU")
    st.caption(
        "**Pedido** puede dar negativo si ya sobra stock para la cobertura "
        "elegida — el impreso lo muestra como 0."
    )
    cols = ["codigo", "descripcion", "rubro", "marca", "ean", "uxb", "costo",
            "lista_1", "ventas_promedio_ajustado", "dias_con_stock",
            "dias_quiebre", "stock_sucursal", "stock_cd_001",
            "fecha_ultimo_remito", "fecha_ultima_venta", "pedido_recomendado"]
    tabla = disp[cols].copy()
    for c in ["uxb", "ventas_promedio_ajustado", "dias_con_stock", "dias_quiebre",
              "stock_sucursal", "stock_cd_001", "pedido_recomendado"]:
        tabla[c] = tabla[c].apply(formatear_unidades)
    for c in ["costo", "lista_1"]:
        tabla[c] = tabla[c].apply(formatear_pesos)
    tabla.columns = ["Código", "Descripción", "Rubro", "Marca", "EAN", "UxB",
                     "Costo", "Lista 1", "Venta prom. ajustada",
                     "Días con stock", "Días de quiebre", "Stock sucursal",
                     "Stock CD 001", "Últ. remito", "Últ. venta",
                     f"Pedido ({dias_cobertura} d)"]
    st.dataframe(tabla.head(1000), width="stretch", hide_index=True)
    if len(disp) > 1000:
        st.caption(f"Mostrando 1.000 de {len(disp):,} — descargá el listado completo.")

    botones_descarga(disp[cols], "pedido_sucursal", "ped")

    # --- Reporte imprimible --------------------------------------------------
    if len(disp) > LIMITE_IMPRESION:
        st.caption(
            f"⚠️ El recorte tiene {len(disp):,} SKUs — una hoja de pedido en "
            f"papel con tantas filas no es práctica. Se puede generar igual, "
            f"pero conviene acotar más el filtro para imprimir."
        )

    meta_rep = {
        "proyecto": proyecto,
        "sucursal": etiqueta_suc(suc_sel),
        "desde": perfil.attrs["desde"], "hasta": perfil.attrs["hasta"],
        "dias_cobertura": dias_cobertura, "fecha_stock": perfil.attrs["fecha_stock"],
        "recorte": f"{len(recorte):,} SKUs filtrados",
    }
    html_rep = generar_reporte_pedido_sucursal(proyecto, meta_rep, disp)
    boton_reporte(html_rep, f"pedido_sucursal_{suc_sel}_{perfil.attrs['hasta']}.html",
                 "dl_ped_rep")
