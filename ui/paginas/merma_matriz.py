"""ui/paginas/merma_matriz.py — Página Matriz por sucursal (Merma)

También sirve "Matriz por sucursal (fechas variables)": render(...,
fechas_variables=True) pide un período por familia de comprobante (AJU, VTA,
REM) y guarda su estado con prefijo propio ("mmsv" en vez de "mms")."""
import streamlit as st

from ui.comun import *  # noqa: F401,F403


# Tope de filas de la vista previa: a nivel SKU la matriz puede tener decenas
# de miles de filas × (sucursales × 7) columnas; el Excel lleva todo.
_TOPE_PREVIA = 1000


def render(proyecto, fechas_variables: bool = False):
    # Prefijo de claves: las dos páginas conviven sin pisarse el resultado.
    kp = "mmsv" if fechas_variables else "mms"

    st.title("Matriz por sucursal (fechas variables)" if fechas_variables
             else "Matriz por sucursal")
    st.caption(
        "Por rubro, gran super rubro, marca o SKU, y por sucursal: diferencias "
        "de inventario y ajustes (unidades y $), SKUs inventariados y venta "
        "del período. SKUs activos (flag del ERP) va una sola vez por fila, "
        "igual para todas las sucursales. "
        "Se descarga en Excel con una columna agrupada por sucursal."
        + (" Cada familia de comprobante toma movimientos de su propio "
           "período: inventario y ajustes de AJU, venta de VTA. **Stock "
           "inicial**: el stock del sistema justo antes de cada conteo (INV) "
           "de la ventana AJU, con o sin diferencia (10→8 suma 10; 10→10 "
           "también); un SKU contado dos veces suma los dos saldos."
           if fechas_variables else "")
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
        return
    suc_largo, _ = etiquetas_sucursal(sucursales)
    abrevs = abreviaciones_sucursal(sucursales)
    cat = catalogo_cacheado(proyecto)

    cat_f, filtro_activo = filtros_catalogo(cat, kp)
    if fechas_variables:
        ctrl = controles_periodo_variable(proyecto, kp, modos=list(MODOS_VALORIZACION_MERMA))
    else:
        ctrl = controles_periodo(proyecto, kp, modos=list(MODOS_VALORIZACION_MERMA))
    ventanas = ctrl.get("ventanas") if ctrl else None

    c1, c2 = st.columns([3, 1])
    with c1:
        suc_sel = st.multiselect(
            "Sucursales (vacío = todas)", sucursales["codigodepo"].tolist(),
            format_func=lambda c: suc_largo.get(c, str(c)), key=f"{kp}_sucs")
    with c2:
        nivel = st.selectbox(
            "Filas por", ["rubro", "gran_super_rubro", "marca", "codigo"],
            format_func=lambda x: {"rubro": "Rubro", "gran_super_rubro": "Gran Super Rubro",
                                   "marca": "Marca", "codigo": "SKU"}[x],
            key=f"{kp}_nivel")

    if ctrl and st.button("Calcular", key=f"{kp}_btn"):
        codigos_t = tuple(sorted(cat_f["codigo"])) if filtro_activo else None
        depo_t = tuple(sorted(suc_sel)) if suc_sel else None
        with st.spinner("Calculando..."):
            try:
                df_l = merma_matriz_cacheada(
                    proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"], nivel,
                    ctrl["modo_valorizacion"], ctrl["fecha_valorizacion"],
                    codigos_t, depo_t, ventanas_tupla(ventanas),
                    incluir_stock_inicial=fechas_variables)
                filtros_txt = []
                for k, etq in (("gsr", "GSR"), ("rub", "Rubro"), ("mar", "Marca")):
                    if st.session_state.get(f"{kp}_{k}"):
                        filtros_txt.append(f"{etq}: " + ", ".join(st.session_state[f"{kp}_{k}"]))
                if st.session_state.get(f"{kp}_txt"):
                    filtros_txt.append(f"Búsqueda: '{st.session_state[f'{kp}_txt']}'")
                st.session_state[f"{kp}_df"] = df_l
                st.session_state[f"{kp}_param"] = {
                    "Período": (texto_ventanas(ventanas) if ventanas
                                else f"{ctrl['fecha_desde']} → {ctrl['fecha_hasta']}"),
                    "Sucursales": etiqueta_seleccion_sucursales(suc_sel, suc_largo),
                    "Valorización": ETIQUETAS_MODO_VALORIZACION.get(
                        ctrl["modo_valorizacion"], ctrl["modo_valorizacion"]),
                    "Snapshot de valorización": str(df_l.attrs.get("fecha_valorizacion")),
                    "Filtros": " · ".join(filtros_txt) or "—",
                    "Signo": "Inventario/Ajustes: faltante positivo, sobrante negativo",
                }
                if fechas_variables:
                    st.session_state[f"{kp}_param"]["Stock inicial"] = (
                        "Stock del sistema justo antes de cada línea INV de la ventana "
                        "AJU (con o sin diferencia; dos conteos suman dos saldos). "
                        "Reconstruido desde el snapshot de valorización; dentro de un "
                        "día, orden de ingesta.")
            except Exception as e:
                st.error(f"Error: {e}")

    if f"{kp}_df" not in st.session_state:
        return

    df_l = st.session_state[f"{kp}_df"]
    param = st.session_state[f"{kp}_param"]
    fuera = df_l.attrs.get("tipomov_fuera") or {}
    if fuera:
        st.warning("Quedaron afuera movimientos con un tipo de comprobante "
                   "que no es AJU/VTA/REM: "
                   + ", ".join(f"{t} ({n:,})" for t, n in fuera.items()))
    if df_l.empty:
        st.info("Sin movimientos para el período y filtros elegidos.")
        return

    # Opciones de salida: se aplican sobre el resultado ya calculado.
    grupos = grupos_matriz_merma(df_l)
    o1, o2, o3 = st.columns([1, 1, 3])
    signo_real = o1.checkbox(
        "Signo real", value=True, key=f"{kp}_signo_real",
        help="Inventario y Ajustes con el signo del movimiento (sobrante "
             "positivo, faltante negativo) en vez del signo de merma.")
    miles = o2.checkbox("$ en miles", value=False, key=f"{kp}_miles",
                        help="Valorizados (inventario, ajustes y venta) divididos por 1000.")
    grupos_sel = o3.multiselect("Mostrar", grupos, default=grupos, key=f"{kp}_grupos")
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

    if df_l.attrs.get("ventanas"):
        nombre = f"matriz_merma_{df_l.attrs['nivel']}_fechas_variables.xlsx"
    else:
        desde, hasta = param["Período"].split(" → ")
        nombre = f"matriz_merma_{df_l.attrs['nivel']}_{desde}_{hasta}.xlsx"
    st.download_button(
        "⬇️ Descargar Excel", a_excel_matriz_merma(ancho, param), nombre,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{kp}_dl")
