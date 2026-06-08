"""
ui/app.py — Interfaz principal de MermaIQ

Estructura de páginas:
    1. Inicio        — Selección de proyecto, métricas rápidas
    2. Ingesta       — Ver períodos cargados, ingestar archivos pendientes
    3. Análisis      — Tabla de merma por SKU (INV / REM / CS)
    4. Sucursales    — Resumen agregado por sucursal
    5. Rubros        — Pareto por rubro

Correr con:
    streamlit run ui/app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import sys

# Asegurar que el root del proyecto esté en el path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from setup_db import get_db_path, get_connection
from core.merma import calcular_merma


# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="MermaIQ",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# CSS personalizado
st.markdown("""
<style>
    /* Fuente principal */
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f1117;
        border-right: 1px solid #1e2130;
    }
    [data-testid="stSidebar"] * {
        color: #e0e0e0 !important;
    }

    /* Métricas */
    [data-testid="stMetric"] {
        background: #1e2130;
        border: 1px solid #2d3148;
        border-radius: 8px;
        padding: 16px;
    }
    [data-testid="stMetricLabel"] {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: #8892b0 !important;
    }
    [data-testid="stMetricValue"] {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 1.6rem;
        color: #ccd6f6 !important;
    }

    /* Encabezados */
    h1, h2, h3 {
        font-family: 'IBM Plex Mono', monospace;
        letter-spacing: -0.02em;
    }

    /* Tablas */
    [data-testid="stDataFrame"] {
        border: 1px solid #1e2130;
        border-radius: 8px;
    }

    /* Botones */
    .stButton > button {
        background: #1e2130;
        border: 1px solid #2d3148;
        color: #ccd6f6;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.8rem;
        letter-spacing: 0.05em;
        border-radius: 4px;
        transition: all 0.2s;
    }
    .stButton > button:hover {
        background: #2d3148;
        border-color: #64ffda;
        color: #64ffda;
    }

    /* Selectbox */
    .stSelectbox label {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.75rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #8892b0;
    }

    /* Tag de merma alta */
    .merma-alta { color: #ff6b6b; font-weight: 600; }
    .merma-media { color: #ffa94d; }
    .merma-baja { color: #64ffda; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def listar_proyectos() -> list[str]:
    projects_dir = ROOT / "projects"
    if not projects_dir.exists():
        return []
    return sorted([
        p.name for p in projects_dir.iterdir()
        if p.is_dir() and (p / "data.duckdb").exists()
    ])


def listar_conteos(proyecto: str) -> pd.DataFrame:
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT
            c.codigodepo,
            d.nombre        AS sucursal,
            c.fecha_conteo,
            COUNT(*)        AS skus,
            SUM(CASE WHEN c.diferencia < 0 THEN 1 ELSE 0 END) AS skus_con_merma
        FROM conteos c
        LEFT JOIN depositos d ON c.codigodepo = d.codigodepo
        GROUP BY c.codigodepo, d.nombre, c.fecha_conteo
        ORDER BY c.fecha_conteo DESC, c.codigodepo
    """).df()
    conn.close()
    return df


def listar_periodos_ingesta(proyecto: str) -> pd.DataFrame:
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT tabla, periodo, archivo_origen, estado,
               registros_cargados, fecha_ingesta
        FROM periodos_ingesta
        ORDER BY fecha_ingesta DESC
    """).df()
    conn.close()
    return df


def formatear_pesos(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"$ {valor:,.0f}"


def formatear_pct(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"{valor:.2f}%"


# ---------------------------------------------------------------------------
# Sidebar — Selección de proyecto y navegación
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 📦 MermaIQ")
    st.markdown("---")

    proyectos = listar_proyectos()

    if not proyectos:
        st.warning("No hay proyectos. Creá uno con:\n`python setup_db.py --project nombre`")
        st.stop()

    proyecto = st.selectbox(
        "PROYECTO",
        proyectos,
        key="proyecto_activo",
    )

    st.markdown("---")

    pagina = st.radio(
        "NAVEGACIÓN",
        ["Inicio", "Ingesta", "Análisis", "Sucursales", "Rubros"],
        key="pagina_activa",
    )

    st.markdown("---")
    st.markdown(
        f"<small style='color:#4a5568;font-family:IBM Plex Mono,monospace'>"
        f"proyecto: {proyecto}</small>",
        unsafe_allow_html=True
    )


# ---------------------------------------------------------------------------
# Página: INICIO
# ---------------------------------------------------------------------------

if pagina == "Inicio":
    st.title(f"Bienvenido — {proyecto}")

    conn = get_connection(proyecto)

    # Métricas rápidas
    n_conteos = conn.execute("SELECT COUNT(DISTINCT fecha_conteo || codigodepo) FROM conteos").fetchone()[0]
    n_movimientos = conn.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
    n_ventas = conn.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    n_skus = conn.execute("SELECT COUNT(DISTINCT codigo) FROM articulos WHERE activo = true").fetchone()[0]
    n_sucursales = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]
    conn.close()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Conteos cargados", n_conteos)
    col2.metric("Movimientos", f"{n_movimientos:,}")
    col3.metric("Registros de ventas", f"{n_ventas:,}")
    col4.metric("SKUs activos", f"{n_skus:,}")
    col5.metric("Sucursales", n_sucursales)

    st.markdown("---")

    # Últimos conteos
    st.subheader("Últimos conteos cargados")
    df_conteos = listar_conteos(proyecto)
    if df_conteos.empty:
        st.info("No hay conteos cargados aún. Ingresá los archivos en la página Ingesta.")
    else:
        st.dataframe(df_conteos, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Página: INGESTA
# ---------------------------------------------------------------------------

elif pagina == "Ingesta":
    st.title("Ingesta de datos")

    tab1, tab2 = st.tabs(["📋 Períodos cargados", "📁 Cargar archivo"])

    with tab1:
        df_periodos = listar_periodos_ingesta(proyecto)
        if df_periodos.empty:
            st.info("No hay datos cargados aún.")
        else:
            # Filtro por tabla
            tablas = ["Todos"] + sorted(df_periodos["tabla"].unique().tolist())
            filtro = st.selectbox("Filtrar por tabla", tablas)
            if filtro != "Todos":
                df_periodos = df_periodos[df_periodos["tabla"] == filtro]
            st.dataframe(df_periodos, use_container_width=True, hide_index=True)

    with tab2:
        st.markdown("#### Cargar archivo manualmente")

        tipo_archivo = st.selectbox(
            "Tipo de archivo",
            ["movimientos", "ventas", "conteo", "depositos", "estructura", "articulos"]
        )

        archivo = st.file_uploader(
            "Seleccioná el archivo Excel",
            type=["xlsx"],
            key=f"uploader_{tipo_archivo}"
        )

        if tipo_archivo == "conteo" and archivo:
            col1, col2 = st.columns(2)
            with col1:
                codigodepo = st.text_input("Código de sucursal (ej: 002)")
            with col2:
                fecha_conteo = st.date_input("Fecha del conteo")

        forzar = st.checkbox("Reemplazar si ya existe (forzar)", value=False)

        if archivo and st.button("Ingestar"):
            # Guardar temporalmente
            tmp_path = ROOT / "exports" / archivo.name
            tmp_path.write_bytes(archivo.read())

            try:
                with st.spinner("Procesando..."):
                    if tipo_archivo == "movimientos":
                        from ingesta.movimientos import ingestar_movimientos
                        r = ingestar_movimientos(tmp_path, proyecto, forzar=forzar)
                        st.success(f"✓ {r['registros']} movimientos cargados — período {r['periodo']}")

                    elif tipo_archivo == "ventas":
                        from ingesta.ventas import ingestar_ventas
                        r = ingestar_ventas(tmp_path, proyecto, forzar=forzar)
                        st.success(f"✓ {r['registros']} ventas cargadas — período {r['periodo']}")

                    elif tipo_archivo == "conteo":
                        if not codigodepo:
                            st.error("Ingresá el código de sucursal.")
                        else:
                            from ingesta.conteos import ingestar_conteo
                            r = ingestar_conteo(
                                tmp_path, proyecto,
                                codigodepo=codigodepo,
                                fecha_conteo=str(fecha_conteo),
                                forzar=forzar
                            )
                            st.success(f"✓ {r['registros']} SKUs cargados — merma $ {r['merma_valorizada']:,.0f}")

                    elif tipo_archivo == "depositos":
                        from ingesta.referencias import cargar_depositos
                        r = cargar_depositos(tmp_path, proyecto)
                        st.success(f"✓ Depósitos: {r['insertados']} nuevos, {r['actualizados']} actualizados")

                    elif tipo_archivo == "estructura":
                        from ingesta.referencias import cargar_estructura
                        r = cargar_estructura(tmp_path, proyecto)
                        st.success(f"✓ Estructura: {r['insertados']} nuevos, {r['actualizados']} actualizados")

                    elif tipo_archivo == "articulos":
                        from ingesta.referencias import cargar_articulos
                        r = cargar_articulos(tmp_path, proyecto)
                        st.success(f"✓ Artículos: {r['insertados']} nuevos, {r['actualizados']} actualizados")

            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Página: ANÁLISIS
# ---------------------------------------------------------------------------

elif pagina == "Análisis":
    st.title("Análisis de merma")

    df_conteos = listar_conteos(proyecto)

    if df_conteos.empty:
        st.info("No hay conteos cargados. Ingresá los archivos primero.")
    else:
        # Controles
        col1, col2, col3 = st.columns(3)

        with col1:
            sucursales = df_conteos["codigodepo"].unique().tolist()
            suc_sel = st.selectbox("Sucursal", sucursales)

        with col2:
            fechas = df_conteos[df_conteos["codigodepo"] == suc_sel]["fecha_conteo"].tolist()
            fecha_sel = st.selectbox("Fecha de conteo", fechas)

        with col3:
            modo = st.selectbox(
                "Modo de ventana",
                ["conteo_anterior", "periodo_fijo"],
                format_func=lambda x: "Desde conteo anterior" if x == "conteo_anterior" else "Período fijo"
            )

        dias = 90
        if modo == "periodo_fijo":
            dias = st.slider("Días hacia atrás", 30, 365, 90)

        if st.button("Calcular merma"):
            with st.spinner("Calculando..."):
                try:
                    df = calcular_merma(
                        proyecto, suc_sel, str(fecha_sel),
                        modo=modo, dias=dias
                    )
                    st.session_state["df_merma"] = df
                except Exception as e:
                    st.error(f"Error: {e}")

        # Mostrar resultados
        if "df_merma" in st.session_state:
            df = st.session_state["df_merma"]

            # Métricas resumen
            st.markdown("---")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("SKUs analizados", len(df))
            col2.metric(
                "Merma total valorizada",
                formatear_pesos(df["merma_total_valorizada"].sum())
            )
            col3.metric(
                "Venta total período",
                formatear_pesos(df["venta_neta"].sum())
            )
            venta_total = df["venta_neta"].sum()
            merma_total = df["merma_total_valorizada"].sum()
            pct_total = (merma_total / venta_total * 100) if venta_total > 0 else None
            col4.metric(
                "% Merma s/ventas",
                formatear_pct(pct_total)
            )

            # Tabla detalle
            st.markdown("#### Detalle por SKU")

            cols_mostrar = [
                "codigo", "descripcion",
                "merma_inv_unidades", "merma_inv_valorizada",
                "merma_rem_unidades", "merma_rem_valorizada",
                "merma_cs_unidades",  "merma_cs_valorizada",
                "merma_total_valorizada",
                "venta_neta", "pct_merma_sobre_ventas",
                "ventana_origen", "dias_ventana",
            ]

            df_display = df[cols_mostrar].copy()
            df_display["merma_inv_valorizada"]   = df_display["merma_inv_valorizada"].apply(formatear_pesos)
            df_display["merma_rem_valorizada"]   = df_display["merma_rem_valorizada"].apply(formatear_pesos)
            df_display["merma_cs_valorizada"]    = df_display["merma_cs_valorizada"].apply(formatear_pesos)
            df_display["merma_total_valorizada"] = df_display["merma_total_valorizada"].apply(formatear_pesos)
            df_display["venta_neta"]             = df_display["venta_neta"].apply(formatear_pesos)
            df_display["pct_merma_sobre_ventas"] = df_display["pct_merma_sobre_ventas"].apply(formatear_pct)

            df_display.columns = [
                "Código", "Descripción",
                "INV u.", "INV $",
                "REM u.", "REM $",
                "CS u.",  "CS $",
                "Total $",
                "Venta $", "% Merma",
                "Ventana", "Días",
            ]

            st.dataframe(df_display, use_container_width=True, hide_index=True)

            # Gráfico top 15 por merma valorizada
            st.markdown("#### Top 15 SKUs por merma valorizada")
            df_top = df.nlargest(15, "merma_total_valorizada").copy()
            df_top["label"] = df_top["codigo"] + " - " + df_top["descripcion"].fillna("")

            fig = go.Figure()
            fig.add_bar(
                name="INV", x=df_top["label"], y=df_top["merma_inv_valorizada"].fillna(0),
                marker_color="#ff6b6b"
            )
            fig.add_bar(
                name="REM", x=df_top["label"], y=df_top["merma_rem_valorizada"].fillna(0),
                marker_color="#ffa94d"
            )
            fig.add_bar(
                name="CS", x=df_top["label"], y=df_top["merma_cs_valorizada"].fillna(0),
                marker_color="#748ffc"
            )
            fig.update_layout(
                barmode="stack",
                plot_bgcolor="#0f1117",
                paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                xaxis=dict(tickangle=-45, gridcolor="#1e2130"),
                yaxis=dict(gridcolor="#1e2130"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                height=450,
                margin=dict(b=160),
            )
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Página: SUCURSALES
# ---------------------------------------------------------------------------

elif pagina == "Sucursales":
    st.title("Resumen por sucursal")

    df_conteos = listar_conteos(proyecto)

    if df_conteos.empty:
        st.info("No hay conteos cargados.")
    else:
        fecha_sel = st.selectbox(
            "Fecha de conteo",
            sorted(df_conteos["fecha_conteo"].unique().tolist(), reverse=True)
        )

        modo = st.selectbox(
            "Modo de ventana",
            ["conteo_anterior", "periodo_fijo"],
            format_func=lambda x: "Desde conteo anterior" if x == "conteo_anterior" else "Período fijo"
        )
        dias = 90
        if modo == "periodo_fijo":
            dias = st.slider("Días hacia atrás", 30, 365, 90)

        if st.button("Calcular todas las sucursales"):
            sucursales = df_conteos[
                df_conteos["fecha_conteo"] == fecha_sel
            ]["codigodepo"].tolist()

            resultados = []
            progress = st.progress(0)

            for i, suc in enumerate(sucursales):
                try:
                    df_suc = calcular_merma(proyecto, suc, str(fecha_sel), modo=modo, dias=dias)
                    venta = df_suc["venta_neta"].sum()
                    merma = df_suc["merma_total_valorizada"].sum()
                    resultados.append({
                        "codigodepo": suc,
                        "skus":       len(df_suc),
                        "merma_inv":  df_suc["merma_inv_valorizada"].sum(),
                        "merma_rem":  df_suc["merma_rem_valorizada"].sum(),
                        "merma_cs":   df_suc["merma_cs_valorizada"].sum(),
                        "merma_total": merma,
                        "venta_neta": venta,
                        "pct_merma":  (merma / venta * 100) if venta > 0 else None,
                    })
                except Exception as e:
                    st.warning(f"Sucursal {suc}: {e}")
                progress.progress((i + 1) / len(sucursales))

            if resultados:
                df_res = pd.DataFrame(resultados)

                # Join nombre sucursal
                conn = get_connection(proyecto)
                deps = conn.execute("SELECT codigodepo, nombre, abreviacion FROM depositos").df()
                conn.close()
                df_res = df_res.merge(deps, on="codigodepo", how="left")

                st.session_state["df_sucursales"] = df_res

        if "df_sucursales" in st.session_state:
            df_res = st.session_state["df_sucursales"]

            # Métricas globales
            col1, col2, col3 = st.columns(3)
            col1.metric("Merma total", formatear_pesos(df_res["merma_total"].sum()))
            col2.metric("Venta total", formatear_pesos(df_res["venta_neta"].sum()))
            venta_t = df_res["venta_neta"].sum()
            merma_t = df_res["merma_total"].sum()
            col3.metric("% Merma global", formatear_pct((merma_t / venta_t * 100) if venta_t > 0 else None))

            # Tabla
            df_display = df_res[[
                "codigodepo", "nombre", "skus",
                "merma_inv", "merma_rem", "merma_cs",
                "merma_total", "venta_neta", "pct_merma"
            ]].copy()

            for col in ["merma_inv", "merma_rem", "merma_cs", "merma_total", "venta_neta"]:
                df_display[col] = df_display[col].apply(formatear_pesos)
            df_display["pct_merma"] = df_display["pct_merma"].apply(formatear_pct)

            df_display.columns = [
                "Código", "Sucursal", "SKUs",
                "Merma INV", "Merma REM", "Merma CS",
                "Merma Total", "Venta", "% Merma"
            ]
            st.dataframe(df_display, use_container_width=True, hide_index=True)

            # Gráfico comparativo
            fig = go.Figure()
            fig.add_bar(
                name="INV", x=df_res["nombre"].fillna(df_res["codigodepo"]),
                y=df_res["merma_inv"], marker_color="#ff6b6b"
            )
            fig.add_bar(
                name="REM", x=df_res["nombre"].fillna(df_res["codigodepo"]),
                y=df_res["merma_rem"], marker_color="#ffa94d"
            )
            fig.add_bar(
                name="CS", x=df_res["nombre"].fillna(df_res["codigodepo"]),
                y=df_res["merma_cs"], marker_color="#748ffc"
            )
            fig.update_layout(
                barmode="stack",
                plot_bgcolor="#0f1117",
                paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                xaxis=dict(gridcolor="#1e2130"),
                yaxis=dict(gridcolor="#1e2130"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                height=400,
            )
            st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Página: RUBROS
# ---------------------------------------------------------------------------

elif pagina == "Rubros":
    st.title("Pareto por rubro")

    if "df_merma" not in st.session_state:
        st.info("Calculá primero el análisis en la página Análisis.")
    else:
        df = st.session_state["df_merma"].copy()

        # Join con estructura
        conn = get_connection(proyecto)
        arts = conn.execute(
            "SELECT codigo, rubro FROM articulos"
        ).df()
        est = conn.execute(
            "SELECT rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()
        conn.close()

        df = df.merge(arts, on="codigo", how="left")
        df = df.merge(est, on="rubro", how="left")

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

        # Pareto acumulado
        df_rubro["pct_acumulado"] = (
            df_rubro["merma_total"].cumsum() /
            df_rubro["merma_total"].sum() * 100
        )

        # Gráfico Pareto
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
        st.plotly_chart(fig, use_container_width=True)

        # Tabla
        df_display = df_rubro.copy()
        df_display["merma_total"] = df_display["merma_total"].apply(formatear_pesos)
        df_display["venta_total"] = df_display["venta_total"].apply(formatear_pesos)
        df_display["pct_merma"]   = df_display["pct_merma"].apply(formatear_pct)
        df_display["pct_acumulado"] = df_display["pct_acumulado"].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "—"
        )
        df_display.columns = [nivel.replace("_", " ").title(), "Merma $", "Venta $", "SKUs", "% Merma", "% Acum."]
        st.dataframe(df_display, use_container_width=True, hide_index=True)
