"""ui/paginas/merma_rubros.py — Página Rubros"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.comun import *  # noqa: F401,F403


def render(proyecto):
    st.title("Pareto por rubro")

    if "df_merma" not in st.session_state:
        st.info("Calculá primero el análisis en la página Análisis.")
    else:
        # El resultado del análisis ya trae rubro / super_rubro / gran_super_rubro
        df = st.session_state["df_merma"].copy()

        nivel = st.selectbox(
            "Nivel de agrupación",
            ["rubro", "super_rubro", "gran_super_rubro"],
            format_func=lambda x: {
                "rubro": "Rubro",
                "super_rubro": "Super Rubro",
                "gran_super_rubro": "Gran Super Rubro"
            }[x]
        )

        df_rubro = (
            df.groupby(nivel, dropna=False)
            .agg(
                merma_total=("merma_total_valorizada", "sum"),
                venta_total=("venta_neta", "sum"),
                skus=("codigo", "count"),
            )
            .reset_index()
            .sort_values("merma_total", ascending=False)
        )

        df_rubro["pct_merma"] = (
            df_rubro["merma_total"] / df_rubro["venta_total"] * 100
        ).where(df_rubro["venta_total"] > 0)

        df_rubro["pct_acumulado"] = (
            df_rubro["merma_total"].cumsum() /
            df_rubro["merma_total"].sum() * 100
        )

        fig = go.Figure()
        fig.add_bar(
            x=df_rubro[nivel].fillna("Sin rubro"),
            y=df_rubro["merma_total"],
            name="Merma $",
            marker_color="#ff6b6b",
            yaxis="y1",
        )
        fig.add_scatter(
            x=df_rubro[nivel].fillna("Sin rubro"),
            y=df_rubro["pct_acumulado"],
            name="% Acumulado",
            mode="lines+markers",
            line=dict(color="#64ffda", width=2),
            marker=dict(size=6),
            yaxis="y2",
        )
        fig.update_layout(
            plot_bgcolor="#0f1117",
            paper_bgcolor="#0f1117",
            font=dict(color="#ccd6f6", family="IBM Plex Mono"),
            xaxis=dict(tickangle=-45, gridcolor="#1e2130"),
            yaxis=dict(title="Merma $", gridcolor="#1e2130"),
            yaxis2=dict(
                title="% Acumulado",
                overlaying="y", side="right",
                range=[0, 110], gridcolor="#1e2130",
                ticksuffix="%",
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=500,
            margin=dict(b=160),
        )
        st.plotly_chart(fig, width="stretch")

        df_display = df_rubro.copy()
        df_display["merma_total"] = df_display["merma_total"].apply(formatear_pesos)
        df_display["venta_total"] = df_display["venta_total"].apply(formatear_pesos)
        df_display["pct_merma"]   = df_display["pct_merma"].apply(formatear_pct)
        df_display["pct_acumulado"] = df_display["pct_acumulado"].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "—"
        )
        df_display.columns = [nivel.replace("_", " ").title(), "Merma $", "Venta $", "SKUs", "% Merma", "% Acum."]
        st.dataframe(df_display, width="stretch", hide_index=True)
