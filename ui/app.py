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

import io

from setup_db import get_db_path, get_connection
from core.merma import (
    calcular_merma, merma_por_sucursal,
    listar_categorias, listar_fechas_valorizacion,
)
from core.reporte import generar_reporte


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


def formatear_unidades(valor) -> str:
    if pd.isna(valor) or valor is None:
        return "—"
    return f"{valor:,.0f}"


def merma_por_categoria(df: pd.DataFrame, cat: str) -> pd.Series:
    """Magnitud de merma (parte negativa, en positivo) de una columna-categoría."""
    if cat not in df.columns:
        return pd.Series(0.0, index=df.index)
    return (-df[cat]).clip(lower=0)


def a_excel(df: pd.DataFrame) -> bytes:
    """Serializa un DataFrame a .xlsx en memoria."""
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def botones_descarga(df: pd.DataFrame, nombre: str, key: str) -> None:
    """Botones Excel + CSV para descargar un listado."""
    c1, c2, _ = st.columns([1, 1, 4])
    c1.download_button(
        "⬇ Excel", a_excel(df), f"{nombre}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key}_xlsx",
    )
    c2.download_button(
        "⬇ CSV", df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        f"{nombre}.csv", "text/csv", key=f"{key}_csv",
    )


@st.cache_data(show_spinner=False)
def comparativa_cacheada(proyecto: str, fecha_desde: str, fecha_hasta: str,
                         modo_valorizacion: str, fecha_valorizacion,
                         codigos: tuple | None) -> pd.DataFrame:
    return merma_por_sucursal(
        proyecto, fecha_desde, fecha_hasta,
        modo_valorizacion=modo_valorizacion,
        fecha_valorizacion=fecha_valorizacion,
        codigos=list(codigos) if codigos is not None else None,
    )


def render_comparativa(df_suc: pd.DataFrame, merma_cats: list[str], key: str) -> None:
    """Tabla + gráfico + descarga de la comparativa por sucursal."""
    if df_suc.empty:
        st.info("Sin datos para la comparativa.")
        return

    df_suc = df_suc.copy()
    df_suc["sucursal"] = df_suc["codigodepo"] + " — " + df_suc["nombre"].fillna("")

    # Tabla
    cols = ["codigodepo", "nombre", "skus", "skus_con_merma"]
    headers = ["Código", "Sucursal", "SKUs", "SKUs c/merma"]
    formato = {}
    for c in merma_cats:
        if c in df_suc.columns:
            cols.append(c); headers.append(f"{c} $"); formato[c] = formatear_pesos
            cu = f"{c} (u)"
            cols.append(cu); headers.append(f"{c} u."); formato[cu] = formatear_unidades
    for c, h, f in [
        ("merma_total_valorizada", "Merma Total $", formatear_pesos),
        ("merma_total_unidades",   "Merma Total u.", formatear_unidades),
        ("venta_neta",             "Venta $",        formatear_pesos),
        ("pct_merma_sobre_ventas", "% Merma",        formatear_pct),
    ]:
        cols.append(c); headers.append(h); formato[c] = f

    df_display = df_suc[cols].copy()
    for c, f in formato.items():
        df_display[c] = df_display[c].apply(f)
    df_display.columns = headers
    st.dataframe(df_display, width="stretch", hide_index=True)

    botones_descarga(df_suc[cols], "comparativa_sucursales", key)

    # Gráfico: merma apilada por categoría + % en eje secundario
    fig = go.Figure()
    for i, cat in enumerate(merma_cats):
        cu = f"{cat} (u)"
        if cat in df_suc.columns:
            fig.add_bar(
                name=cat, x=df_suc["sucursal"],
                y=(-df_suc[cat]).clip(lower=0),
                marker_color=PALETA[i % len(PALETA)],
            )
    fig.add_scatter(
        name="% Merma s/venta", x=df_suc["sucursal"],
        y=df_suc["pct_merma_sobre_ventas"],
        mode="lines+markers", line=dict(color="#64ffda", width=2),
        marker=dict(size=7), yaxis="y2",
    )
    fig.update_layout(
        barmode="stack", height=430,
        plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
        font=dict(color="#ccd6f6", family="IBM Plex Mono"),
        xaxis=dict(tickangle=-30, gridcolor="#1e2130"),
        yaxis=dict(title="Merma $", gridcolor="#1e2130"),
        yaxis2=dict(title="% Merma", overlaying="y", side="right",
                    gridcolor="#1e2130", ticksuffix="%"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(b=120),
    )
    st.plotly_chart(fig, width="stretch")


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
    """Renderiza fecha_desde, fecha_hasta, modo y fecha de valorización."""
    fmin, fmax = rango_disponible(proyecto)
    if fmin is None:
        st.info("No hay movimientos cargados todavía.")
        return None

    snapshots = listar_fechas_valorizacion(proyecto)

    c1, c2, c3, c4 = st.columns(4)
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
    with c4:
        if snapshots:
            fecha_val = st.selectbox(
                "Valorizar al (snapshot)", snapshots,
                key=f"{key_prefix}_fval",
                help="Fecha del stock con la que se valoriza. Default: el más reciente.",
            )
        else:
            fecha_val = None
            st.warning("Sin snapshots de stock — no se puede valorizar.")

    return {
        "fecha_desde": str(fecha_desde),
        "fecha_hasta": str(fecha_hasta),
        "modo_valorizacion": modo_val,
        "fecha_valorizacion": fecha_val,
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
        TODAS = "__todas__"
        suc_opts = [TODAS] + sucursales["codigodepo"].tolist()
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        suc_sel = st.selectbox(
            "Sucursal", suc_opts,
            format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS
                                   else f"{c} — {suc_label.get(c, '')}".strip(" —")),
        )

        ctrl = controles_periodo(proyecto, "analisis")

        if ctrl and st.button("Calcular merma"):
            with st.spinner("Calculando..."):
                try:
                    depo = None if suc_sel == TODAS else suc_sel
                    df = calcular_merma(proyecto, depo, **ctrl)
                    st.session_state["df_merma"] = df
                    st.session_state["cats_merma"] = df.attrs.get("categorias", [])
                    st.session_state["fval_merma"] = df.attrs.get("fecha_valorizacion")
                    st.session_state["ctrl_merma"] = ctrl
                    st.session_state["suc_merma"] = suc_sel
                    st.session_state["suc_merma_label"] = (
                        "Todas las sucursales" if suc_sel == TODAS
                        else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")
                    )
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_merma" in st.session_state:
            df_full = st.session_state["df_merma"]
            cats = st.session_state.get("cats_merma", [])
            fval = st.session_state.get("fval_merma")

            st.markdown("---")

            # --- Filtros sobre el resultado (sin recalcular) -----------------
            st.markdown("#### Filtros")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                )
            with fc2:
                f_rubro = st.multiselect(
                    "Rubro",
                    sorted(df_full["rubro"].dropna().unique().tolist()),
                )
            with fc3:
                f_marca = st.multiselect(
                    "Marca",
                    sorted(df_full["marca"].dropna().unique().tolist()),
                )
            with fc4:
                f_texto = st.text_input("Buscar SKU / descripción")

            df = df_full
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_rubro:
                df = df[df["rubro"].isin(f_rubro)]
            if f_marca:
                df = df[df["marca"].isin(f_marca)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            # --- KPIs ---------------------------------------------------------
            merma_total   = df["merma_total_valorizada"].sum()
            merma_total_u = df["merma_total_unidades"].sum()
            venta_total   = df["venta_neta"].sum()
            pct_total = (merma_total / venta_total * 100) if venta_total > 0 else None
            skus_con_merma = int((df["merma_total_valorizada"] > 0).sum())

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("SKUs analizados", f"{len(df):,}")
            col2.metric("SKUs con merma", f"{skus_con_merma:,}")
            col3.metric("Merma total", formatear_pesos(merma_total),
                        delta=f"{merma_total_u:,.0f} unidades", delta_color="off")
            col4.metric("Venta total período", formatear_pesos(venta_total))
            col5.metric("% Merma s/ventas", formatear_pct(pct_total))
            if fval:
                st.caption(f"Valorizado con snapshot de stock del **{fval}**")

            # --- Tabla detalle -------------------------------------------------
            st.markdown("#### Detalle por SKU")
            vista = st.radio(
                "Mostrar", ["Valorizado", "Unidades", "Ambos"],
                horizontal=True, key="analisis_vista",
            )

            cols_base = ["codigo", "descripcion", "rubro"]
            headers   = ["Código", "Descripción", "Rubro"]
            cols_mostrar, formato = [], {}
            for c in cats:
                if vista in ("Valorizado", "Ambos"):
                    cols_mostrar.append(c); headers.append(f"{c} $")
                    formato[c] = formatear_pesos
                if vista in ("Unidades", "Ambos"):
                    cu = f"{c} (u)"
                    cols_mostrar.append(cu); headers.append(f"{c} u.")
                    formato[cu] = formatear_unidades
            cols_fin = [
                ("merma_total_valorizada", "Merma Total $", formatear_pesos),
                ("merma_total_unidades",   "Merma Total u.", formatear_unidades),
                ("venta_neta",             "Venta $",        formatear_pesos),
                ("pct_merma_sobre_ventas", "% Merma",        formatear_pct),
            ]
            for c, h, f in cols_fin:
                cols_mostrar.append(c); headers.append(h); formato[c] = f

            df_display = df[cols_base + cols_mostrar].copy()
            for c, f in formato.items():
                df_display[c] = df_display[c].apply(f)
            df_display.columns = headers
            st.dataframe(df_display, width="stretch", hide_index=True)
            botones_descarga(df[cols_base + cols_mostrar], "detalle_merma_sku", "det")

            # --- Dashboard ------------------------------------------------------
            merma_cats = [c for c in categorias_merma(proyecto) if c in df.columns]
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )

            if merma_cats and merma_total > 0:
                g1, g2 = st.columns([1, 2])

                # Composición de la merma por categoría
                with g1:
                    st.markdown("#### Composición de la merma")
                    comp = {c: merma_por_categoria(df, c).sum() for c in merma_cats}
                    comp = {k: v for k, v in comp.items() if v > 0}
                    fig = go.Figure(go.Pie(
                        labels=list(comp.keys()), values=list(comp.values()),
                        hole=0.55,
                        marker=dict(colors=[PALETA[i % len(PALETA)] for i in range(len(comp))]),
                        textinfo="label+percent",
                    ))
                    fig.update_layout(height=380, showlegend=False, **layout_oscuro)
                    st.plotly_chart(fig, width="stretch")

                # Top 15 SKUs por merma
                with g2:
                    st.markdown("#### Top 15 SKUs por merma")
                    df_top = df.nlargest(15, "merma_total_valorizada").copy()
                    df_top["label"] = df_top["codigo"] + " - " + df_top["descripcion"].fillna("").str.slice(0, 30)
                    fig = go.Figure()
                    for i, cat in enumerate(merma_cats):
                        fig.add_bar(
                            name=cat, x=df_top["label"],
                            y=merma_por_categoria(df_top, cat),
                            marker_color=PALETA[i % len(PALETA)],
                        )
                    fig.update_layout(
                        barmode="stack", height=380,
                        xaxis=dict(tickangle=-45, gridcolor="#1e2130"),
                        yaxis=dict(gridcolor="#1e2130"),
                        margin=dict(b=120), **layout_oscuro,
                    )
                    st.plotly_chart(fig, width="stretch")

                # Merma vs venta por gran super rubro (o rubro si no hay jerarquía)
                nivel_dash = ("gran_super_rubro"
                              if df["gran_super_rubro"].notna().any() else "rubro")
                st.markdown(f"#### Merma vs venta por {'gran super rubro' if nivel_dash == 'gran_super_rubro' else 'rubro'}")
                df_gr = (
                    df.groupby(nivel_dash, dropna=False)
                    .agg(merma=("merma_total_valorizada", "sum"),
                         venta=("venta_neta", "sum"))
                    .reset_index()
                    .sort_values("merma", ascending=False)
                    .head(12)
                )
                df_gr[nivel_dash] = df_gr[nivel_dash].fillna("Sin clasificar")
                df_gr["pct"] = (df_gr["merma"] / df_gr["venta"] * 100).where(df_gr["venta"] > 0)
                fig = go.Figure()
                fig.add_bar(
                    name="Merma $", x=df_gr[nivel_dash], y=df_gr["merma"],
                    marker_color="#ff6b6b", yaxis="y1",
                )
                fig.add_scatter(
                    name="% Merma s/venta", x=df_gr[nivel_dash], y=df_gr["pct"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=7), yaxis="y2",
                )
                fig.update_layout(
                    height=420,
                    xaxis=dict(tickangle=-30, gridcolor="#1e2130"),
                    yaxis=dict(title="Merma $", gridcolor="#1e2130"),
                    yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                gridcolor="#1e2130", ticksuffix="%"),
                    margin=dict(b=120), **layout_oscuro,
                )
                st.plotly_chart(fig, width="stretch")

            # --- Comparativa por sucursal (solo con "Todas") --------------------
            ctrl_calc = st.session_state.get("ctrl_merma")
            df_suc = None
            if st.session_state.get("suc_merma") == TODAS and ctrl_calc:
                st.markdown("---")
                st.markdown("### Comparativa por sucursal")
                filtrado = len(df) != len(df_full)
                codigos_t = tuple(sorted(df["codigo"])) if filtrado else None
                with st.spinner("Armando comparativa..."):
                    df_suc = comparativa_cacheada(
                        proyecto,
                        ctrl_calc["fecha_desde"], ctrl_calc["fecha_hasta"],
                        ctrl_calc["modo_valorizacion"], ctrl_calc["fecha_valorizacion"],
                        codigos_t,
                    )
                if filtrado:
                    st.caption("La comparativa respeta los filtros aplicados arriba.")
                render_comparativa(df_suc, merma_cats, "comp")

            # --- Reporte imprimible ----------------------------------------------
            st.markdown("---")
            st.markdown("### Reporte imprimible")
            filtros_txt = []
            if f_gsr:
                filtros_txt.append("GSR: " + ", ".join(f_gsr))
            if f_rubro:
                filtros_txt.append("Rubro: " + ", ".join(f_rubro))
            if f_marca:
                filtros_txt.append("Marca: " + ", ".join(f_marca))
            if f_texto:
                filtros_txt.append(f"Búsqueda: '{f_texto}'")
            meta = {
                "proyecto":           proyecto,
                "sucursal":           st.session_state.get("suc_merma_label", ""),
                "fecha_desde":        ctrl_calc["fecha_desde"] if ctrl_calc else "",
                "fecha_hasta":        ctrl_calc["fecha_hasta"] if ctrl_calc else "",
                "modo":               ctrl_calc["modo_valorizacion"] if ctrl_calc else "costo",
                "fecha_valorizacion": fval or "—",
                "filtros":            " · ".join(filtros_txt),
            }
            html_reporte = generar_reporte(df, meta, merma_cats, df_sucursales=df_suc)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)",
                html_reporte.encode("utf-8"),
                f"reporte_merma_{meta['fecha_desde']}_{meta['fecha_hasta']}.html",
                "text/html", key="dl_reporte",
            )
            st.caption("Abrilo en el navegador e imprimí con Ctrl+P (o guardá como PDF).")


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
            with st.spinner("Calculando..."):
                try:
                    df_res = merma_por_sucursal(proyecto, **ctrl)
                    st.session_state["df_sucursales"] = df_res
                    st.session_state["cats_merma_suc"] = [
                        c for c in categorias_merma(proyecto)
                        if c in df_res.columns
                    ]
                    st.session_state["fval_suc"] = df_res.attrs.get("fecha_valorizacion")
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_sucursales" in st.session_state:
            df_res = st.session_state["df_sucursales"]
            merma_cats = st.session_state.get("cats_merma_suc", [])

            merma_t = df_res["merma_total_valorizada"].sum()
            venta_t = df_res["venta_neta"].sum()
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Sucursales", len(df_res))
            col2.metric("Merma total", formatear_pesos(merma_t),
                        delta=f"{df_res['merma_total_unidades'].sum():,.0f} unidades",
                        delta_color="off")
            col3.metric("Venta total", formatear_pesos(venta_t))
            col4.metric("% Merma global",
                        formatear_pct((merma_t / venta_t * 100) if venta_t > 0 else None))
            fval_suc = st.session_state.get("fval_suc")
            if fval_suc:
                st.caption(f"Valorizado con snapshot de stock del **{fval_suc}**")

            render_comparativa(df_res, merma_cats, "suc")


# ---------------------------------------------------------------------------
# Página: RUBROS
# ---------------------------------------------------------------------------

elif pagina == "Rubros":
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
