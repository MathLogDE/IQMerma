"""ui/paginas/calidad_datos.py — Página Calidad de datos"""
import streamlit as st
import pandas as pd

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Calidad de datos")
    st.caption(
        "Compara, para cada sucursal, cuántos movimientos tuvo cada día "
        "contra su propia mediana histórica del mismo día de la semana. Un "
        "día muy por debajo de lo esperado suele ser la huella de un gap de "
        "ingesta del ERP para esa sucursal (como el que encontramos en "
        "Rodríguez Busto / SKU 2969010) — es una señal para revisar, no una "
        "confirmación: para eso hay que mirar el SKU puntual."
    )

    fmin, fmax = rango_disponible(proyecto)
    if fmin is None:
        st.info("No hay movimientos cargados todavía.")
        return

    c1, c2, c3, c4 = st.columns(4)
    fecha_desde = c1.date_input(
        "Desde", value=fmin, min_value=fmin, max_value=fmax, key="cal_desde")
    fecha_hasta = c2.date_input(
        "Hasta", value=fmax, min_value=fmin, max_value=fmax, key="cal_hasta")
    ratio_umbral = c3.slider(
        "Umbral (% de lo esperado)", min_value=5, max_value=50, value=15, step=5,
        key="cal_umbral",
        help="Se marca anómalo si ese día tuvo, como mucho, este % de la "
             "mediana histórica de esa sucursal para ese día de semana.",
    ) / 100
    mediana_minima = c4.number_input(
        "Mediana mínima", min_value=1, value=30, step=5, key="cal_mediana_min",
        help="Ignora sucursales/días con mediana histórica menor a esto — "
             "evita ruido en sucursales chicas.",
    )

    if st.button("Analizar", key="cal_btn"):
        with st.spinner("Comparando movimientos diarios por sucursal contra su histórico..."):
            try:
                st.session_state["df_calidad"] = dias_anomalos_cacheados(
                    proyecto, str(fecha_desde), str(fecha_hasta),
                    ratio_umbral=ratio_umbral, mediana_minima=float(mediana_minima),
                )
            except Exception as e:
                st.error(f"Error: {e}")

    if "df_calidad" not in st.session_state:
        return

    df = st.session_state["df_calidad"]
    if df.empty:
        st.success("Sin anomalías detectadas en el rango elegido.")
        return

    st.markdown("---")
    resumen = resumen_dias_anomalos(df)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Anomalías totales", f"{resumen['total']:,}")
    m2.metric("Aisladas", f"{resumen['aisladas']:,}",
             help=f"Afectan hasta {MAX_SUCURSALES_AISLADO} sucursales el mismo día.")
    m3.metric("Días con caída amplia", f"{resumen['dias_amplios']:,}",
             help="3+ sucursales anómalas el mismo día — probablemente feriado o corte general.")
    m4.metric("Sucursales con al menos 1", f"{resumen['sucursales_afectadas']:,}")

    max_aisladas = st.slider(
        "Considerar \"aislada\" hasta N sucursales afectadas el mismo día",
        min_value=1, max_value=max(int(df["sucursales_afectadas_ese_dia"].max()), 1),
        value=MAX_SUCURSALES_AISLADO, key="cal_max_aisladas",
    )
    aisladas = df[df["sucursales_afectadas_ese_dia"] <= max_aisladas].sort_values(["fecha", "codigodepo"])
    amplias = df[df["sucursales_afectadas_ese_dia"] > max_aisladas]

    st.markdown(f"#### Anomalías aisladas ({len(aisladas)})")
    st.caption(
        "Una o pocas sucursales sin datos ese día mientras el resto operaba "
        "normal — el patrón más parecido a un gap de ingesta puntual de esa "
        "sucursal."
    )
    if aisladas.empty:
        st.info("Ninguna con este criterio.")
    else:
        vista = aisladas.copy()
        vista["fecha"] = vista["fecha"].dt.strftime("%Y-%m-%d")
        vista["ratio"] = (vista["ratio"] * 100).round(1)
        vista["mediana_local"] = vista["mediana_local"].round(0)
        vista = vista.rename(columns={
            "fecha": "Fecha", "codigodepo": "Código", "nombre": "Sucursal",
            "movimientos": "Movimientos", "mediana_local": "Esperado (mediana)",
            "ratio": "% de lo esperado",
        })[["Fecha", "Código", "Sucursal", "Movimientos", "Esperado (mediana)", "% de lo esperado"]]
        st.dataframe(vista, width="stretch", hide_index=True, height=380)
        botones_descarga(aisladas, "dias_anomalos_aislados", "cal_aisladas")

    if not amplias.empty:
        dias_amplios = (amplias.groupby("fecha")["codigodepo"]
                        .apply(lambda s: ", ".join(sorted(s))).reset_index())
        dias_amplios["fecha"] = dias_amplios["fecha"].dt.strftime("%Y-%m-%d")
        st.markdown(f"#### Días con caída amplia ({len(dias_amplios)})")
        st.caption(
            "Varias sucursales a la vez el mismo día — revisar si no "
            "coincide con un feriado (25/12, 1/1, 1/5 ya suelen aparecer acá "
            "sin que sea un problema real)."
        )
        st.dataframe(
            dias_amplios.rename(columns={"fecha": "Fecha", "codigodepo": "Sucursales afectadas"}),
            width="stretch", hide_index=True,
        )
