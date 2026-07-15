"""ui/paginas/comercial_margenes.py — Página Márgenes"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Márgenes")
    st.caption(
        "Margen teórico (lista vs costo), margen bruto del período y la "
        "**merma como % del margen** — cuánto de lo que el producto deja se "
        "pierde en merma. Con más de un snapshot de stock, también la "
        "evolución de costos (inflación de reposición)."
    )

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay movimientos cargados.")
    else:
        TODAS_M = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        fmin, fmax = rango_disponible(proyecto)
        c1, c2, c3 = st.columns(3)
        with c1:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_M] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas" if c == TODAS_M
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="mg_suc")
        with c2:
            f_desde = st.date_input("Desde", value=fmin, min_value=fmin,
                                    max_value=fmax, key="mg_desde")
        with c3:
            f_hasta = st.date_input("Hasta", value=fmax, min_value=fmin,
                                    max_value=fmax, key="mg_hasta")

        if st.button("Analizar márgenes"):
            with st.spinner("Analizando..."):
                try:
                    df_mg = analizar_margenes(
                        proyecto,
                        codigodepo=None if suc_sel == TODAS_M else suc_sel,
                        fecha_desde=str(f_desde), fecha_hasta=str(f_hasta),
                    )
                    st.session_state["df_margenes"] = df_mg
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_margenes" in st.session_state:
            df_full = st.session_state["df_margenes"]
            st.markdown("---")

            fc1, fc2 = st.columns(2)
            with fc1:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                    key="mg_f_gsr")
            with fc2:
                f_txt = st.text_input("Buscar SKU / descripción", key="mg_f_txt")

            df = df_full
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_txt:
                t = f_txt.strip().lower()
                df = df[df["codigo"].str.lower().str.contains(t, na=False)
                        | df["descripcion"].str.lower().str.contains(t, na=False)]

            venta_t = df["venta_valorizada"].sum()
            margen_t = df["margen_bruto"].sum()
            merma_t = df["merma_costo"].sum()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Venta (a lista)", formatear_pesos(venta_t))
            k2.metric("Margen bruto", formatear_pesos(margen_t),
                      delta=(f"{margen_t / venta_t * 100:.1f}% de la venta"
                             if venta_t > 0 else None), delta_color="off")
            k3.metric("Merma (a costo)", formatear_pesos(merma_t))
            k4.metric("Merma s/ margen",
                      formatear_pct((merma_t / margen_t * 100) if margen_t > 0 else None),
                      help="Qué parte del margen bruto se pierde en merma.")

            # Agregado por gran super rubro
            st.markdown("#### Margen por gran super rubro")
            df_g = (df.groupby("gran_super_rubro", dropna=False)
                    .agg(venta=("venta_valorizada", "sum"),
                         margen=("margen_bruto", "sum"),
                         merma=("merma_costo", "sum"))
                    .reset_index().sort_values("margen", ascending=False).head(14))
            df_g["gran_super_rubro"] = df_g["gran_super_rubro"].fillna("Sin clasificar")
            df_g["merma_sm"] = (df_g["merma"] / df_g["margen"] * 100).where(df_g["margen"] > 0)
            fig = go.Figure()
            fig.add_bar(name="Margen bruto $", x=df_g["gran_super_rubro"],
                        y=df_g["margen"], marker_color="#64ffda", yaxis="y1")
            fig.add_scatter(name="Merma s/margen %", x=df_g["gran_super_rubro"],
                            y=df_g["merma_sm"], mode="lines+markers",
                            line=dict(color="#ff6b6b", width=2),
                            marker=dict(size=7), yaxis="y2")
            fig.update_layout(
                height=420,
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                xaxis=dict(tickangle=-30, gridcolor="#1e2130"),
                yaxis=dict(title="Margen bruto $", gridcolor="#1e2130"),
                yaxis2=dict(title="Merma s/margen", overlaying="y", side="right",
                            gridcolor="#1e2130", ticksuffix="%"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                margin=dict(b=110),
            )
            st.plotly_chart(fig, width="stretch")

            # Detalle por SKU
            st.markdown("#### Detalle por SKU")
            cols = ["codigo", "descripcion", "rubro", "costo", "lista_1",
                    "margen_pct", "unidades_vendidas", "venta_valorizada",
                    "margen_bruto", "merma_costo", "merma_sobre_margen"]
            df_display = df[cols].copy()
            for c in ["costo", "lista_1", "venta_valorizada", "margen_bruto", "merma_costo"]:
                df_display[c] = df_display[c].apply(formatear_pesos)
            df_display["unidades_vendidas"] = df_display["unidades_vendidas"].apply(formatear_unidades)
            for c in ["margen_pct", "merma_sobre_margen"]:
                df_display[c] = df_display[c].apply(formatear_pct)
            df_display.columns = ["Código", "Descripción", "Rubro", "Costo", "Lista",
                                  "% Margen", "Unid. vend.", "Venta $",
                                  "Margen bruto $", "Merma $", "Merma s/margen"]
            st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
            if len(df) > 2000:
                st.caption(f"Mostrando 2.000 de {len(df):,} filas — descargá el listado completo.")
            botones_descarga(df[cols], "margenes", "mg")

            # Evolución de costos
            st.markdown("#### Evolución de costos (inflación de reposición)")
            df_ev = evolucion_costos(proyecto)
            n_snap = df_ev.attrs.get("n_snapshots", 0)
            if n_snap < 2:
                st.info(
                    "Se necesita más de un snapshot de stock para ver la evolución. "
                    "A medida que cargues stock semanalmente, acá aparece la curva "
                    "de costos medianos por gran super rubro."
                )
            else:
                top_gsr = (df_ev.groupby("gran_super_rubro")["skus"].max()
                           .sort_values(ascending=False).head(8).index.tolist())
                fig = go.Figure()
                for i, g in enumerate(top_gsr):
                    d = df_ev[df_ev["gran_super_rubro"] == g]
                    fig.add_scatter(name=g, x=d["fecha_snapshot"], y=d["costo_mediano"],
                                    mode="lines+markers",
                                    line=dict(color=PALETA[i % len(PALETA)], width=2))
                fig.update_layout(
                    height=420,
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(title="Costo mediano $", gridcolor="#1e2130"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, width="stretch")
