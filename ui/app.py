"""
ui/app.py — Interfaz principal de MermaIQ (v2: análisis por selección de período)

Estructura de páginas:
    1. Inicio        — Selección de proyecto, métricas rápidas
    2. Ingesta       — Ver períodos cargados, ingestar archivos pendientes
    3. Análisis      — Tabla de merma por SKU (pivot por categoría)
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
from datetime import date
import sys

# Asegurar que el root del proyecto esté en el path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from setup_db import get_db_path, get_connection
from core.merma import calcular_merma, listar_categorias


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


# Paleta para las categorías de merma en los gráficos
PALETA = ["#ff6b6b", "#ffa94d", "#748ffc", "#64ffda", "#f783ac", "#a9e34b", "#ffd43b"]


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


def listar_sucursales(proyecto: str) -> pd.DataFrame:
    """Sucursales con movimientos, con nombre si existe."""
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT DISTINCT m.codigodepo, d.nombre
        FROM movimientos m
        LEFT JOIN depositos d ON m.codigodepo = d.codigodepo
        WHERE m.codigodepo IS NOT NULL
        ORDER BY m.codigodepo
    """).df()
    conn.close()
    return df


def rango_disponible(proyecto: str) -> tuple[date | None, date | None]:
    """Rango de fechas con movimientos."""
    conn = get_connection(proyecto)
    rm = conn.execute("SELECT MIN(fecha), MAX(fecha) FROM movimientos").fetchone()
    conn.close()
    return rm[0], rm[1]


def categorias_merma(proyecto: str) -> list[str]:
    """Categorías marcadas es_merma, en orden de catálogo."""
    conn = get_connection(proyecto)
    rows = conn.execute("""
        SELECT categoria FROM tipos_categoria
        WHERE es_merma GROUP BY categoria ORDER BY MIN(orden), categoria
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


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


def merma_por_categoria(df: pd.DataFrame, cat: str) -> pd.Series:
    """Magnitud de merma (parte negativa, en positivo) de una columna-categoría."""
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return (-df[cat]).clip(lower=0)


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
# Controles compartidos de período/valorización
# ---------------------------------------------------------------------------

def controles_periodo(proyecto: str, key_prefix: str):
    """Renderiza fecha_desde, fecha_hasta y modo_valorizacion."""
    fmin, fmax = rango_disponible(proyecto)
    if fmin is None:
        st.info("No hay movimientos cargados todavía.")
        return None

    c1, c2, c3 = st.columns(3)
    with c1:
        fecha_desde = st.date_input(
            "Desde", value=fmin, min_value=fmin, max_value=fmax,
            key=f"{key_prefix}_desde",
        )
    with c2:
        fecha_hasta = st.date_input(
            "Hasta", value=fmax, min_value=fmin, max_value=fmax,
            key=f"{key_prefix}_hasta",
        )
    with c3:
        modo_val = st.selectbox(
            "Valorización", ["costo", "lista_1"],
            format_func=lambda x: "A costo" if x == "costo" else "A precio de lista",
            key=f"{key_prefix}_modo",
        )

    return {
        "fecha_desde": str(fecha_desde),
        "fecha_hasta": str(fecha_hasta),
        "modo_valorizacion": modo_val,
    }


# ---------------------------------------------------------------------------
# Página: INICIO
# ---------------------------------------------------------------------------

if pagina == "Inicio":
    st.title(f"Bienvenido — {proyecto}")

    conn = get_connection(proyecto)
    n_inv = conn.execute(
        "SELECT COUNT(DISTINCT fecha || '|' || codigodepo) FROM movimientos WHERE tipomov = 'INV'"
    ).fetchone()[0]
    n_movimientos = conn.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
    n_skus = conn.execute("SELECT COUNT(DISTINCT codigo) FROM articulos WHERE activo = true").fetchone()[0]
    n_sucursales = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]
    conn.close()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Inventarios cargados", n_inv)
    col2.metric("Movimientos", f"{n_movimientos:,}")
    col3.metric("SKUs activos", f"{n_skus:,}")
    col4.metric("Sucursales", n_sucursales)

    fmin, fmax = rango_disponible(proyecto)
    if fmin:
        st.caption(f"Datos disponibles: {fmin} → {fmax}")

    st.markdown("---")
    st.subheader("Últimas ingestas")
    df_periodos = listar_periodos_ingesta(proyecto)
    if df_periodos.empty:
        st.info("No hay datos cargados aún. Ingresá los archivos en la página Ingesta.")
    else:
        st.dataframe(df_periodos.head(15), width="stretch", hide_index=True)


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
            tablas = ["Todos"] + sorted(df_periodos["tabla"].unique().tolist())
            filtro = st.selectbox("Filtrar por tabla", tablas)
            if filtro != "Todos":
                df_periodos = df_periodos[df_periodos["tabla"] == filtro]
            st.dataframe(df_periodos, width="stretch", hide_index=True)

    with tab2:
        st.markdown("#### Cargar archivo manualmente")
        st.caption(
            "El inventario físico entra dentro de **movimientos** "
            "(TIPOMOV='INV'), en el mismo export del ERP — no se carga como "
            "archivo aparte."
        )

        tipo_archivo = st.selectbox(
            "Tipo de archivo",
            ["movimientos", "stock", "depositos", "estructura", "articulos"]
        )

        archivo = st.file_uploader(
            "Seleccioná el archivo Excel",
            type=["xlsx"],
            key=f"uploader_{tipo_archivo}"
        )

        if tipo_archivo == "stock":
            fecha_snapshot = st.date_input("Fecha del snapshot de stock")

        multi_hoja = False
        if tipo_archivo == "movimientos":
            multi_hoja = st.checkbox(
                "Archivo con varias pestañas (una por mes)", value=False,
                help="Carga cada pestaña como su propio período.",
            )

        forzar = st.checkbox("Reemplazar si ya existe (forzar)", value=False)

        if archivo and st.button("Ingestar"):
            tmp_dir = ROOT / "exports"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tmp_dir / archivo.name
            tmp_path.write_bytes(archivo.read())

            try:
                with st.spinner("Procesando..."):
                    if tipo_archivo == "movimientos":
                        if multi_hoja:
                            from ingesta.movimientos import ingestar_libro_movimientos
                            r = ingestar_libro_movimientos(tmp_path, proyecto, forzar=forzar)
                            st.success(
                                f"✓ {r['registros_total']} movimientos — "
                                f"{r['cargadas']}/{r['hojas']} pestañas cargadas"
                            )
                            if r["errores"]:
                                st.warning(
                                    "Pestañas con error: "
                                    + ", ".join(f"{e['hoja']} ({e['error']})" for e in r["errores"])
                                )
                        else:
                            from ingesta.movimientos import ingestar_movimientos
                            r = ingestar_movimientos(tmp_path, proyecto, forzar=forzar)
                            st.success(f"✓ {r['registros']} movimientos cargados — período {r['periodo']}")

                    elif tipo_archivo == "stock":
                        from ingesta.stock import ingestar_stock
                        r = ingestar_stock(
                            tmp_path, proyecto,
                            fecha_snapshot=str(fecha_snapshot),
                            forzar=forzar
                        )
                        st.success(
                            f"✓ {r['registros']:,} registros cargados — "
                            f"{len(r['sucursales_mapeadas'])} sucursales — "
                            f"snapshot {r['fecha_snapshot']}"
                        )
                        if r['columnas_no_mapeadas']:
                            st.warning(
                                f"Columnas sin mapeo en depósitos (ignoradas): "
                                f"{r['columnas_no_mapeadas']}"
                            )

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

    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
    else:
        suc_opts = sucursales["codigodepo"].tolist()
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        suc_sel = st.selectbox(
            "Sucursal", suc_opts,
            format_func=lambda c: f"{c} — {suc_label.get(c, '')}".strip(" —"),
        )

        ctrl = controles_periodo(proyecto, "analisis")

        if ctrl and st.button("Calcular merma"):
            with st.spinner("Calculando..."):
                try:
                    df = calcular_merma(proyecto, suc_sel, **ctrl)
                    st.session_state["df_merma"] = df
                    st.session_state["cats_merma"] = df.attrs.get("categorias", [])
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_merma" in st.session_state:
            df = st.session_state["df_merma"]
            cats = st.session_state.get("cats_merma", [])

            st.markdown("---")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("SKUs analizados", len(df))
            merma_total = df["merma_total_valorizada"].sum()
            venta_total = df["venta_neta"].sum()
            col2.metric("Merma total valorizada", formatear_pesos(merma_total))
            col3.metric("Venta total período", formatear_pesos(venta_total))
            pct_total = (merma_total / venta_total * 100) if venta_total > 0 else None
            col4.metric("% Merma s/ventas", formatear_pct(pct_total))

            # Tabla detalle
            st.markdown("#### Detalle por SKU")
            cols_base = ["codigo", "descripcion"]
            cols_fin = ["merma_total_valorizada", "venta_neta", "pct_merma_sobre_ventas"]
            cols_mostrar = cols_base + cats + cols_fin

            df_display = df[cols_mostrar].copy()
            for c in cats + ["merma_total_valorizada", "venta_neta"]:
                df_display[c] = df_display[c].apply(formatear_pesos)
            df_display["pct_merma_sobre_ventas"] = df_display["pct_merma_sobre_ventas"].apply(formatear_pct)
            df_display.columns = (
                ["Código", "Descripción"] + [f"{c} $" for c in cats] +
                ["Merma Total $", "Venta $", "% Merma"]
            )
            st.dataframe(df_display, width="stretch", hide_index=True)

            # Gráfico top 15 por merma (stack de categorías de merma)
            merma_cats = [c for c in categorias_merma(proyecto) if c in df.columns]
            if merma_cats:
                st.markdown("#### Top 15 SKUs por merma valorizada")
                df_top = df.nlargest(15, "merma_total_valorizada").copy()
                df_top["label"] = df_top["codigo"] + " - " + df_top["descripcion"].fillna("")

                fig = go.Figure()
                for i, cat in enumerate(merma_cats):
                    fig.add_bar(
                        name=cat, x=df_top["label"],
                        y=merma_por_categoria(df_top, cat),
                        marker_color=PALETA[i % len(PALETA)],
                    )
                fig.update_layout(
                    barmode="stack",
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(tickangle=-45, gridcolor="#1e2130"),
                    yaxis=dict(gridcolor="#1e2130"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    height=450, margin=dict(b=160),
                )
                st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Página: SUCURSALES
# ---------------------------------------------------------------------------

elif pagina == "Sucursales":
    st.title("Resumen por sucursal")

    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay datos cargados.")
    else:
        ctrl = controles_periodo(proyecto, "sucursales")

        if ctrl and st.button("Calcular todas las sucursales"):
            merma_cats = categorias_merma(proyecto)
            resultados = []
            progress = st.progress(0)
            sucs = sucursales["codigodepo"].tolist()

            for i, suc in enumerate(sucs):
                try:
                    dfx = calcular_merma(proyecto, suc, **ctrl)
                    fila = {
                        "codigodepo": suc,
                        "skus":       len(dfx),
                        "merma_total": dfx["merma_total_valorizada"].sum(),
                        "venta_neta":  dfx["venta_neta"].sum(),
                    }
                    for cat in merma_cats:
                        fila[cat] = merma_por_categoria(dfx, cat).sum()
                    resultados.append(fila)
                except Exception as e:
                    st.warning(f"Sucursal {suc}: {e}")
                progress.progress((i + 1) / len(sucs))

            if resultados:
                df_res = pd.DataFrame(resultados)
                df_res = df_res.merge(sucursales, on="codigodepo", how="left")
                st.session_state["df_sucursales"] = df_res
                st.session_state["cats_merma_suc"] = merma_cats

        if "df_sucursales" in st.session_state:
            df_res = st.session_state["df_sucursales"]
            merma_cats = st.session_state.get("cats_merma_suc", [])

            col1, col2, col3 = st.columns(3)
            col1.metric("Merma total", formatear_pesos(df_res["merma_total"].sum()))
            col2.metric("Venta total", formatear_pesos(df_res["venta_neta"].sum()))
            venta_t = df_res["venta_neta"].sum()
            merma_t = df_res["merma_total"].sum()
            col3.metric("% Merma global", formatear_pct((merma_t / venta_t * 100) if venta_t > 0 else None))

            # Tabla
            df_res = df_res.copy()
            df_res["pct_merma"] = (df_res["merma_total"] / df_res["venta_neta"] * 100).where(
                df_res["venta_neta"] > 0
            )
            cols = ["codigodepo", "nombre", "skus"] + merma_cats + ["merma_total", "venta_neta", "pct_merma"]
            df_display = df_res[cols].copy()
            for c in merma_cats + ["merma_total", "venta_neta"]:
                df_display[c] = df_display[c].apply(formatear_pesos)
            df_display["pct_merma"] = df_display["pct_merma"].apply(formatear_pct)
            df_display.columns = (
                ["Código", "Sucursal", "SKUs"] + [f"{c} $" for c in merma_cats] +
                ["Merma Total", "Venta", "% Merma"]
            )
            st.dataframe(df_display, width="stretch", hide_index=True)

            # Gráfico comparativo
            if merma_cats:
                etiquetas = df_res["nombre"].fillna(df_res["codigodepo"])
                fig = go.Figure()
                for i, cat in enumerate(merma_cats):
                    fig.add_bar(
                        name=cat, x=etiquetas, y=df_res[cat],
                        marker_color=PALETA[i % len(PALETA)],
                    )
                fig.update_layout(
                    barmode="stack",
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(gridcolor="#1e2130"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    height=400,
                )
                st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Página: RUBROS
# ---------------------------------------------------------------------------

elif pagina == "Rubros":
    st.title("Pareto por rubro")

    if "df_merma" not in st.session_state:
        st.info("Calculá primero el análisis en la página Análisis.")
    else:
        df = st.session_state["df_merma"].copy()

        conn = get_connection(proyecto)
        arts = conn.execute("SELECT codigo, rubro FROM articulos").df()
        est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura"
        ).df()
        conn.close()

        # Lookup por código O por nombre: articulos.rubro puede traer cualquiera
        registros = []
        for _, r in est.iterrows():
            for clave in (r["rubro_cod"], r["rubro"]):
                if pd.notna(clave):
                    registros.append({
                        "_clave": str(clave).strip(),
                        "rubro_desc": r["rubro"],
                        "super_rubro": r["super_rubro"],
                        "gran_super_rubro": r["gran_super_rubro"],
                    })
        mapa = (pd.DataFrame(registros).drop_duplicates("_clave") if registros
                else pd.DataFrame(columns=["_clave", "rubro_desc", "super_rubro", "gran_super_rubro"]))

        df = df.merge(arts, on="codigo", how="left")
        df["_clave"] = df["rubro"].astype("string").str.strip()
        df = df.merge(mapa, on="_clave", how="left")
        # Mostrar el nombre de rubro; si no matcheó, cae al valor crudo
        df["rubro"] = df["rubro_desc"].fillna(df["rubro"])

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
