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
from core.reporte import (
    generar_reporte, generar_reporte_control, generar_reporte_salud,
    generar_reporte_politicas, generar_reporte_forecast,
    generar_reporte_transferencias,
    cargar_branding, guardar_branding,
)
from core.transferencias import sugerir_transferencias
from core.margenes import analizar_margenes, evolucion_costos
from core.salud_stock import analizar_stock, resumen_salud, ESTADOS
from core.control_merma import (
    evolucion_mensual, movimientos_outliers, ajustes_por_usuario,
)
from core.politicas_stock import (
    calcular_politicas, resumen_politicas, ESTADOS_POLITICA,
)
from core.forecast import forecast_ventas, serie_mensual_real, NIVELES, METRICAS


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
        ["Inicio", "Ingesta", "Análisis", "Control de merma",
         "Salud de stock", "Min / Opt / Max", "Transferencias",
         "Márgenes", "Forecast", "Sucursales", "Rubros"],
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
    # El ERP registra el inventario físico como TIPO='INV' (bajo TIPOMOV='AJU')
    n_inv = conn.execute(
        "SELECT COUNT(DISTINCT fecha || '|' || codigodepo) FROM movimientos WHERE tipo = 'INV'"
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

    tab1, tab2, tab3, tab4 = st.tabs(
        ["📋 Períodos cargados", "📁 Cargar archivo", "⚙ Depósitos", "🎨 Cliente"]
    )

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

    with tab3:
        st.markdown("#### Depósitos y centros de logística")
        st.caption(
            "Marcá `es_logistica` para los centros de distribución (CDC, CR2…). "
            "Los CD se excluyen de las comparativas de merma y del análisis de "
            "salud de stock. El flag se preserva al recargar el archivo de depósitos."
        )
        conn = get_connection(proyecto)
        df_deps = conn.execute("""
            SELECT codigodepo, nombre, abreviacion, es_logistica
            FROM depositos ORDER BY codigodepo
        """).df()
        conn.close()

        if df_deps.empty:
            st.info("No hay depósitos cargados.")
        else:
            df_edit = st.data_editor(
                df_deps,
                column_config={
                    "codigodepo":   st.column_config.TextColumn("Código", disabled=True),
                    "nombre":       st.column_config.TextColumn("Nombre", disabled=True),
                    "abreviacion":  st.column_config.TextColumn("Abrev.", disabled=True),
                    "es_logistica": st.column_config.CheckboxColumn("Logística (CD)"),
                },
                hide_index=True, width="stretch", key="editor_deps",
            )
            if st.button("Guardar cambios", key="save_deps"):
                cambios = df_edit[
                    df_edit["es_logistica"].fillna(False)
                    != df_deps["es_logistica"].fillna(False)
                ]
                if cambios.empty:
                    st.info("No hay cambios para guardar.")
                else:
                    conn = get_connection(proyecto)
                    for _, r in cambios.iterrows():
                        conn.execute(
                            "UPDATE depositos SET es_logistica = ? WHERE codigodepo = ?",
                            [bool(r["es_logistica"]), r["codigodepo"]],
                        )
                    conn.close()
                    st.success(f"✓ {len(cambios)} depósitos actualizados")
                    st.rerun()

    with tab4:
        st.markdown("#### Marca del cliente en los reportes imprimibles")
        st.caption(
            "El logo y los datos aparecen en el encabezado de todos los "
            "reportes; el color de acento tiñe títulos, KPIs y barras. "
            "Se guarda junto a la base del proyecto (fuera de git)."
        )
        br = cargar_branding(proyecto)

        b1, b2 = st.columns([2, 1])
        with b1:
            br_nombre = st.text_input("Nombre del cliente", value=br["nombre"],
                                      key="br_nombre")
            br_datos = st.text_area(
                "Datos (una línea por dato: razón social, CUIT, contacto…)",
                value="\n".join(br["datos"]), height=100, key="br_datos",
            )
            br_pie = st.text_input(
                "Pie de página", value=br["pie"],
                placeholder="MermaIQ — análisis de inventario", key="br_pie",
            )
        with b2:
            br_color = st.color_picker("Color de acento",
                                       value=br["color"] or "#c0392b", key="br_color")
            logo_file = st.file_uploader("Logo (PNG/JPG)", type=["png", "jpg", "jpeg"],
                                         key="br_logo")
            if br["logo_uri"] and not logo_file:
                st.image(br["logo_uri"], caption="Logo actual", width=160)

        if st.button("Guardar marca", key="br_save"):
            logo_bytes, logo_ext = None, ".png"
            if logo_file is not None:
                logo_bytes = logo_file.read()
                logo_ext = Path(logo_file.name).suffix or ".png"
            guardar_branding(
                proyecto, nombre=br_nombre, datos=br_datos,
                color=br_color, pie=br_pie,
                logo_bytes=logo_bytes, logo_ext=logo_ext,
            )
            st.success("✓ Marca guardada — se aplica a todos los reportes")
            st.rerun()


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
                sin_log = st.checkbox(
                    "Excluir depósitos logísticos (CD)", value=True, key="comp_sinlog",
                    help="Los CD no venden: distorsionan el % de merma. "
                         "Marcalos en Ingesta → Depósitos.",
                )
                if sin_log and "es_logistica" in df_suc.columns:
                    df_suc = df_suc[~df_suc["es_logistica"].fillna(False)]
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
# Página: CONTROL DE MERMA
# ---------------------------------------------------------------------------

elif pagina == "Control de merma":
    st.title("Control de merma")

    sucursales = listar_sucursales(proyecto)
    if sucursales.empty:
        st.info("No hay datos cargados. Ingresá movimientos primero.")
    else:
        TODAS_C = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        suc_sel = st.selectbox(
            "Sucursal", [TODAS_C] + sucursales["codigodepo"].tolist(),
            format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_C
                                   else f"{c} — {suc_label.get(c, '')}".strip(" —")),
            key="control_suc",
        )
        ctrl = controles_periodo(proyecto, "control")

        if ctrl and st.button("Analizar"):
            with st.spinner("Analizando..."):
                try:
                    depo = None if suc_sel == TODAS_C else suc_sel
                    comunes = dict(
                        codigodepo=depo,
                        modo_valorizacion=ctrl["modo_valorizacion"],
                        fecha_valorizacion=ctrl["fecha_valorizacion"],
                    )
                    st.session_state["ctrl_ev"] = evolucion_mensual(
                        proyecto, fecha_desde=ctrl["fecha_desde"],
                        fecha_hasta=ctrl["fecha_hasta"], **comunes)
                    st.session_state["ctrl_out"] = movimientos_outliers(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"],
                        top_n=100, **comunes)
                    st.session_state["ctrl_us"] = ajustes_por_usuario(
                        proyecto, ctrl["fecha_desde"], ctrl["fecha_hasta"], **comunes)
                    st.session_state["ctrl_meta"] = {
                        "proyecto": proyecto,
                        "sucursal": ("Todas las sucursales" if suc_sel == TODAS_C
                                     else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                        "fecha_desde": ctrl["fecha_desde"],
                        "fecha_hasta": ctrl["fecha_hasta"],
                        "modo": ctrl["modo_valorizacion"],
                        "fecha_valorizacion": ctrl["fecha_valorizacion"] or "—",
                    }
                except Exception as e:
                    st.error(f"Error: {e}")

        layout_oscuro = dict(
            plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
            font=dict(color="#ccd6f6", family="IBM Plex Mono"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )

        # --- 1. Evolución mensual ------------------------------------------
        if "ctrl_ev" in st.session_state:
            ev = st.session_state["ctrl_ev"]
            st.markdown("---")
            st.markdown("### Evolución mensual de la merma")
            if ev.empty:
                st.info("Sin movimientos en el período.")
            else:
                if len(ev) >= 2:
                    ult, ant = ev.iloc[-1], ev.iloc[-2]
                    c1, c2, c3 = st.columns(3)
                    c1.metric(f"Merma {ult['mes']}", formatear_pesos(ult["merma_total"]),
                              delta=formatear_pesos(ult["merma_total"] - ant["merma_total"]),
                              delta_color="inverse")
                    c2.metric(f"Venta {ult['mes']}", formatear_pesos(ult["venta_neta"]))
                    c3.metric(f"% Merma {ult['mes']}", formatear_pct(ult["pct_merma_sobre_ventas"]),
                              delta=(f"{float(ult['pct_merma_sobre_ventas'] or 0) - float(ant['pct_merma_sobre_ventas'] or 0):+.2f} pp"),
                              delta_color="inverse")

                cats_ev = [c for c in ev.attrs.get("categorias_merma", []) if c in ev.columns]
                fig = go.Figure()
                for i, cat in enumerate(cats_ev):
                    fig.add_bar(name=cat, x=ev["mes"], y=ev[cat],
                                marker_color=PALETA[i % len(PALETA)])
                fig.add_scatter(
                    name="% Merma s/venta", x=ev["mes"], y=ev["pct_merma_sobre_ventas"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=7), yaxis="y2",
                )
                fig.update_layout(
                    barmode="stack", height=420,
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(title="Merma $", gridcolor="#1e2130"),
                    yaxis2=dict(title="% Merma", overlaying="y", side="right",
                                gridcolor="#1e2130", ticksuffix="%"),
                    **layout_oscuro,
                )
                st.plotly_chart(fig, width="stretch")
                botones_descarga(ev, "evolucion_mensual_merma", "ev")

        # --- 2. Outliers ------------------------------------------------------
        if "ctrl_out" in st.session_state:
            out = st.session_state["ctrl_out"]
            st.markdown("---")
            st.markdown("### Movimientos de mayor impacto (outliers)")
            if out.empty:
                st.info("Sin movimientos de merma en el período.")
            else:
                st.caption(
                    f"Merma bruta del período (faltantes por movimiento): "
                    f"**{formatear_pesos(out.attrs.get('merma_periodo'))}** · "
                    "un movimiento que concentre un % alto merece revisión en el ERP. "
                    "Pares de igual magnitud y signo opuesto suelen ser anulaciones."
                )
                top_n = st.slider("Mostrar top", 10, 100, 25, key="out_topn")
                df_o = out.head(top_n).copy()
                df_o["fecha"] = pd.to_datetime(df_o["fecha"]).dt.date
                df_o["valor"] = df_o["valor"].apply(formatear_pesos)
                df_o["unidades"] = df_o["unidades"].apply(formatear_unidades)
                df_o["pct_merma_periodo"] = df_o["pct_merma_periodo"].apply(formatear_pct)
                df_o = df_o[["fecha", "codigodepo", "sucursal", "tipo", "numero",
                             "usuario", "codigo", "descripcion", "unidades",
                             "valor", "pct_merma_periodo"]]
                df_o.columns = ["Fecha", "Cód.", "Sucursal", "Tipo", "N°", "Usuario",
                                "SKU", "Descripción", "Unid.", "Valor $", "% Merma per."]
                st.dataframe(df_o, width="stretch", hide_index=True)
                botones_descarga(out, "outliers_merma", "out")

        # --- 3. Ajustes por usuario -----------------------------------------
        if "ctrl_us" in st.session_state:
            us = st.session_state["ctrl_us"]
            st.markdown("---")
            st.markdown("### Merma por usuario")
            if us.empty:
                st.info("Sin ajustes en el período.")
            else:
                st.caption(
                    "Faltantes y sobrantes valorizados por usuario × sucursal. "
                    "Mucho volumen en ambos sentidos = correcciones cruzadas (revisar operatoria)."
                )
                g1, g2 = st.columns([1, 1])
                with g1:
                    top_u = (us.groupby("usuario")["faltante_valorizado"].sum()
                             .sort_values(ascending=False).head(10))
                    fig = go.Figure(go.Bar(
                        x=top_u.values, y=top_u.index, orientation="h",
                        marker_color="#ff6b6b",
                    ))
                    fig.update_layout(
                        height=380, xaxis=dict(title="Faltante $", gridcolor="#1e2130"),
                        yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                        **layout_oscuro,
                    )
                    st.plotly_chart(fig, width="stretch")
                with g2:
                    df_u = us.copy()
                    for c in ["faltante_valorizado", "sobrante_valorizado", "neto_valorizado"]:
                        df_u[c] = df_u[c].apply(formatear_pesos)
                    df_u["pct_faltante"] = df_u["pct_faltante"].apply(formatear_pct)
                    df_u = df_u[["usuario", "codigodepo", "movimientos", "skus",
                                 "faltante_valorizado", "sobrante_valorizado",
                                 "neto_valorizado", "pct_faltante"]]
                    df_u.columns = ["Usuario", "Suc.", "Movs", "SKUs",
                                    "Faltante $", "Sobrante $", "Neto $", "% Falt."]
                    st.dataframe(df_u, width="stretch", hide_index=True, height=380)
                botones_descarga(us, "merma_por_usuario", "us")

        # --- Reporte imprimible ------------------------------------------------
        if "ctrl_meta" in st.session_state and "ctrl_ev" in st.session_state:
            st.markdown("---")
            meta_c = st.session_state["ctrl_meta"]
            html_c = generar_reporte_control(
                proyecto, meta_c,
                st.session_state.get("ctrl_ev"),
                st.session_state.get("ctrl_out"),
                st.session_state.get("ctrl_us"),
            )
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_c.encode("utf-8"),
                f"control_merma_{meta_c['fecha_desde']}_{meta_c['fecha_hasta']}.html",
                "text/html", key="dl_rep_control",
            )


# ---------------------------------------------------------------------------
# Página: SALUD DE STOCK
# ---------------------------------------------------------------------------

elif pagina == "Salud de stock":
    st.title("Salud de stock")

    ETIQUETA_ESTADO = {
        "quiebre":    "🔴 Quiebre",
        "critico":    "🟠 Crítico",
        "ok":         "🟢 OK",
        "sobrestock": "🔵 Sobrestock",
        "muerto":     "⚫ Muerto",
    }
    COLOR_ESTADO = {
        "quiebre": "#ff6b6b", "critico": "#ffa94d", "ok": "#64ffda",
        "sobrestock": "#748ffc", "muerto": "#8892b0",
    }

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)

    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
    else:
        TODAS_S = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c0, c1, c2 = st.columns([2, 1, 1])
        with c0:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_S] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_S
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="salud_suc",
            )
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="salud_fs")
        with c2:
            dias_ventana = st.selectbox(
                "Ventana de demanda", [30, 60, 90, 180], index=2,
                format_func=lambda d: f"{d} días", key="salud_vent",
            )

        c3, c4 = st.columns([2, 1])
        with c3:
            banda = st.slider(
                "Banda de cobertura saludable (días)", 0, 365, (7, 60),
                key="salud_banda",
                help="Debajo del mínimo: crítico. Encima del máximo: sobrestock.",
            )
        with c4:
            incluir_log = st.checkbox(
                "Incluir depósitos logísticos", value=False, key="salud_log",
                help="Los CD no venden: quiebre/muerto no aplican. "
                     "Marcá los depósitos con marcar_logistica().",
            )

        if st.button("Analizar stock"):
            with st.spinner("Analizando..."):
                try:
                    df_salud = analizar_stock(
                        proyecto,
                        codigodepo=None if suc_sel == TODAS_S else suc_sel,
                        fecha_stock=fecha_stock,
                        dias_ventana=dias_ventana,
                        cobertura_min=banda[0],
                        cobertura_max=banda[1],
                        incluir_logistica=incluir_log,
                    )
                    st.session_state["df_salud"] = df_salud
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_salud" in st.session_state:
            df_full = st.session_state["df_salud"]

            st.markdown("---")
            # --- Filtros -----------------------------------------------------
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                f_estado = st.multiselect(
                    "Estado", list(ESTADOS),
                    format_func=lambda e: ETIQUETA_ESTADO[e], key="salud_f_estado",
                )
            with fc2:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                    key="salud_f_gsr",
                )
            with fc3:
                f_texto = st.text_input("Buscar SKU / descripción", key="salud_f_txt")

            df = df_full
            if f_estado:
                df = df[df["estado"].isin(f_estado)]
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            # --- KPIs ----------------------------------------------------------
            r = resumen_salud(df)
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Quiebres", f"{r['por_estado']['quiebre']:,}",
                      delta=f"pierden {formatear_pesos(r['venta_perdida_diaria'])}/día",
                      delta_color="off")
            k2.metric("Críticos", f"{r['por_estado']['critico']:,}")
            k3.metric("Stock muerto", formatear_pesos(r["stock_muerto_valor"]),
                      delta=f"{r['por_estado']['muerto']:,} SKU-sucursal", delta_color="off")
            k4.metric("Sobrestock", formatear_pesos(r["sobrestock_valor"]),
                      delta=f"{r['por_estado']['sobrestock']:,} SKU-sucursal", delta_color="off")
            k5.metric("Cobertura mediana",
                      f"{r['cobertura_mediana']:.0f} días" if r["cobertura_mediana"] else "—")
            st.caption(
                f"Stock al **{df_full.attrs.get('fecha_stock')}** · demanda de los últimos "
                f"**{df_full.attrs.get('dias_ventana')} días** · "
                f"capital total en stock: **{formatear_pesos(r['stock_valor_total'])}**"
            )

            # --- Tabla -----------------------------------------------------------
            st.markdown("#### Detalle")
            cols = ["codigo", "descripcion", "rubro", "codigodepo", "sucursal",
                    "estado", "stock", "venta_diaria", "cobertura_dias",
                    "stock_valorizado", "venta_perdida_diaria"]
            df_display = df[cols].copy()
            df_display["estado"] = df_display["estado"].map(ETIQUETA_ESTADO)
            df_display["stock"] = df_display["stock"].apply(formatear_unidades)
            df_display["venta_diaria"] = df_display["venta_diaria"].apply(
                lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            df_display["cobertura_dias"] = df_display["cobertura_dias"].apply(
                lambda v: f"{v:,.0f} d" if pd.notna(v) else "∞")
            df_display["stock_valorizado"] = df_display["stock_valorizado"].apply(formatear_pesos)
            df_display["venta_perdida_diaria"] = df_display["venta_perdida_diaria"].apply(formatear_pesos)
            df_display.columns = ["Código", "Descripción", "Rubro", "Cód.", "Sucursal",
                                  "Estado", "Stock", "Venta/día", "Cobertura",
                                  "Stock $", "Vta perdida $/día"]
            st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
            if len(df) > 2000:
                st.caption(f"Mostrando 2.000 de {len(df):,} filas — descargá el listado completo.")
            botones_descarga(df[cols], "salud_stock", "salud")

            # --- Gráficos -----------------------------------------------------------
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("#### SKU-sucursal por estado")
                conteo = df["estado"].value_counts().reindex(list(ESTADOS)).fillna(0)
                fig = go.Figure(go.Bar(
                    x=[ETIQUETA_ESTADO[e] for e in conteo.index], y=conteo.values,
                    marker_color=[COLOR_ESTADO[e] for e in conteo.index],
                ))
                fig.update_layout(height=340, xaxis=dict(gridcolor="#1e2130"),
                                  yaxis=dict(gridcolor="#1e2130"), **layout_oscuro)
                st.plotly_chart(fig, width="stretch")
            with g2:
                st.markdown("#### Capital en stock por estado")
                valor = df.groupby("estado")["stock_valorizado"].sum().reindex(list(ESTADOS)).fillna(0)
                fig = go.Figure(go.Bar(
                    x=[ETIQUETA_ESTADO[e] for e in valor.index], y=valor.values,
                    marker_color=[COLOR_ESTADO[e] for e in valor.index],
                ))
                fig.update_layout(height=340, xaxis=dict(gridcolor="#1e2130"),
                                  yaxis=dict(gridcolor="#1e2130"), **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            df_q = df[df["estado"] == "quiebre"]
            if not df_q.empty:
                st.markdown("#### Top 15 quiebres por venta perdida")
                df_topq = df_q.nlargest(15, "venta_perdida_diaria").copy()
                df_topq["label"] = (df_topq["codigo"] + " (" + df_topq["codigodepo"] + ") - "
                                    + df_topq["descripcion"].fillna("").str.slice(0, 28))
                fig = go.Figure(go.Bar(
                    x=df_topq["venta_perdida_diaria"], y=df_topq["label"],
                    orientation="h", marker_color="#ff6b6b",
                ))
                fig.update_layout(height=440,
                                  xaxis=dict(title="$ perdidos por día", gridcolor="#1e2130"),
                                  yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                  **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            df_m = df[df["estado"] == "muerto"]
            if not df_m.empty and df_m["gran_super_rubro"].notna().any():
                st.markdown("#### Stock muerto por gran super rubro")
                df_gm = (df_m.groupby("gran_super_rubro")["stock_valorizado"].sum()
                         .sort_values(ascending=False).head(10))
                fig = go.Figure(go.Bar(
                    x=df_gm.values, y=df_gm.index, orientation="h", marker_color="#8892b0",
                ))
                fig.update_layout(height=400,
                                  xaxis=dict(title="$ inmovilizados", gridcolor="#1e2130"),
                                  yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                  **layout_oscuro)
                st.plotly_chart(fig, width="stretch")

            # --- Reporte imprimible --------------------------------------------
            st.markdown("---")
            meta_s = {
                "proyecto": proyecto,
                "sucursal": ("Todas las sucursales" if suc_sel == TODAS_S
                             else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                "dias_ventana": df_full.attrs.get("dias_ventana", ""),
                "banda": f"{banda[0]}–{banda[1]} días",
            }
            html_s = generar_reporte_salud(proyecto, meta_s, df, r, ETIQUETA_ESTADO)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_s.encode("utf-8"),
                f"salud_stock_{meta_s['fecha_stock']}.html",
                "text/html", key="dl_rep_salud",
            )


# ---------------------------------------------------------------------------
# Página: MIN / OPT / MAX
# ---------------------------------------------------------------------------

elif pagina == "Min / Opt / Max":
    st.title("Políticas de stock — Mín / Ópt / Máx")

    ETIQUETA_POL = {
        "reponer":     "🔴 Reponer",
        "ok":          "🟢 OK",
        "exceso":      "🔵 Exceso",
        "sin_demanda": "⚫ Sin demanda",
    }

    snapshots = listar_fechas_valorizacion(proyecto)
    sucursales = listar_sucursales(proyecto)

    if not snapshots or sucursales.empty:
        st.info("Se necesita al menos un snapshot de stock y movimientos cargados.")
    else:
        TODAS_P = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c0, c1, c2 = st.columns([2, 1, 1])
        with c0:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_P] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas las sucursales" if c == TODAS_P
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="pol_suc",
            )
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="pol_fs")
        with c2:
            dias_demanda = st.selectbox(
                "Ventana de demanda", [56, 90, 180], index=1,
                format_func=lambda d: f"{d} días", key="pol_vent",
            )

        with st.expander("Parámetros del modelo"):
            p1, p2 = st.columns(2)
            with p1:
                lead_time = st.number_input(
                    "Lead time (días)", 1, 120, 7, key="pol_lead",
                    help="Días desde que se dispara el pedido hasta que llega.",
                )
            with p2:
                ciclo_default = st.number_input(
                    "Ciclo default (días)", 7, 365, 30, key="pol_ciclo",
                    help="Ciclo de reposición cuando no se puede estimar de los datos.",
                )
            st.caption(
                "El **ciclo de reposición** se estima por SKU × sucursal como la "
                "mediana de días entre llegadas (Remitido entrante), con fallback "
                "SKU global → rubro → default. Nivel de servicio por clase ABC: "
                "A 95% · B 90% · C 80%."
            )

        if st.button("Calcular políticas"):
            with st.spinner("Calculando..."):
                try:
                    df_pol = calcular_politicas(
                        proyecto,
                        codigodepo=None if suc_sel == TODAS_P else suc_sel,
                        fecha_stock=fecha_stock,
                        dias_demanda=dias_demanda,
                        lead_time_dias=lead_time,
                        ciclo_default=ciclo_default,
                    )
                    st.session_state["df_pol"] = df_pol
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_pol" in st.session_state:
            df_full = st.session_state["df_pol"]

            st.markdown("---")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                f_estado = st.multiselect(
                    "Estado", list(ESTADOS_POLITICA),
                    format_func=lambda e: ETIQUETA_POL[e], key="pol_f_estado",
                )
            with fc2:
                f_abc = st.multiselect("Clase ABC", ["A", "B", "C"], key="pol_f_abc")
            with fc3:
                f_gsr = st.multiselect(
                    "Gran Super Rubro",
                    sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                    key="pol_f_gsr",
                )
            with fc4:
                f_texto = st.text_input("Buscar SKU / descripción", key="pol_f_txt")

            df = df_full
            if f_estado:
                df = df[df["estado"].isin(f_estado)]
            if f_abc:
                df = df[df["clase_abc"].isin(f_abc)]
            if f_gsr:
                df = df[df["gran_super_rubro"].isin(f_gsr)]
            if f_texto:
                t = f_texto.strip().lower()
                df = df[
                    df["codigo"].str.lower().str.contains(t, na=False)
                    | df["descripcion"].str.lower().str.contains(t, na=False)
                ]

            r = resumen_politicas(df)
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("A reponer", f"{r['por_estado']['reponer']:,}",
                      delta=f"compra sugerida {formatear_pesos(r['compra_sugerida'])}",
                      delta_color="off")
            k2.metric("En exceso", f"{r['por_estado']['exceso']:,}",
                      delta=f"{formatear_pesos(r['exceso_valorizado'])} sobre el máx",
                      delta_color="off")
            k3.metric("OK", f"{r['por_estado']['ok']:,}")
            k4.metric("Ciclo de reposición mediano",
                      f"{r['ciclo_mediano']:.0f} días" if r["ciclo_mediano"] else "—")
            st.caption(
                f"Stock al **{df_full.attrs.get('fecha_stock')}** · demanda de "
                f"**{df_full.attrs.get('dias_demanda')} días** · lead time "
                f"**{df_full.attrs.get('lead_time_dias'):.0f} días** · compra sugerida "
                "redondeada a bultos (uxb) cuando el bulto cabe en el óptimo."
            )

            # --- Tabla ------------------------------------------------------
            st.markdown("#### Detalle")
            cols = ["codigo", "descripcion", "codigodepo", "sucursal",
                    "clase_abc", "clase_xyz", "estado",
                    "stock", "demanda_diaria", "ciclo_dias", "ciclo_origen",
                    "minimo", "optimo", "maximo",
                    "compra_sugerida", "compra_valorizada"]
            df_display = df[cols].copy()
            df_display["estado"] = df_display["estado"].map(ETIQUETA_POL)
            df_display["demanda_diaria"] = df_display["demanda_diaria"].apply(
                lambda v: f"{v:.2f}")
            for c in ["stock", "ciclo_dias", "minimo", "optimo", "maximo",
                      "compra_sugerida"]:
                df_display[c] = df_display[c].apply(formatear_unidades)
            df_display["compra_valorizada"] = df_display["compra_valorizada"].apply(formatear_pesos)
            df_display.columns = ["Código", "Descripción", "Cód.", "Sucursal",
                                  "ABC", "XYZ", "Estado", "Stock", "Vta/día",
                                  "Ciclo d", "Origen ciclo", "Mín", "Ópt", "Máx",
                                  "Compra u.", "Compra $"]
            st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
            if len(df) > 2000:
                st.caption(f"Mostrando 2.000 de {len(df):,} filas — descargá el listado completo.")
            botones_descarga(df[cols], "politicas_stock", "pol")

            # --- Gráficos ---------------------------------------------------
            layout_oscuro = dict(
                plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("#### Matriz ABC × XYZ (SKU-sucursal)")
                mat = df.pivot_table(index="clase_abc", columns="clase_xyz",
                                     values="codigo", aggfunc="count").fillna(0)
                mat = mat.reindex(index=["A", "B", "C"], columns=["X", "Y", "Z"]).fillna(0)
                fig = go.Figure(go.Heatmap(
                    z=mat.values, x=mat.columns, y=mat.index,
                    colorscale=[[0, "#1e2130"], [1, "#ff6b6b"]],
                    text=mat.values.astype(int), texttemplate="%{text:,}",
                    showscale=False,
                ))
                fig.update_layout(height=340, **layout_oscuro)
                st.plotly_chart(fig, width="stretch")
            with g2:
                df_rep = df[df["estado"] == "reponer"]
                if not df_rep.empty:
                    st.markdown("#### Top 15 compras sugeridas")
                    df_topc = df_rep.nlargest(15, "compra_valorizada").copy()
                    df_topc["label"] = (df_topc["codigo"] + " (" + df_topc["codigodepo"]
                                        + ") - " + df_topc["descripcion"].fillna("").str.slice(0, 25))
                    fig = go.Figure(go.Bar(
                        x=df_topc["compra_valorizada"], y=df_topc["label"],
                        orientation="h", marker_color="#ffa94d",
                    ))
                    fig.update_layout(height=340,
                                      xaxis=dict(title="Compra sugerida $", gridcolor="#1e2130"),
                                      yaxis=dict(autorange="reversed", gridcolor="#1e2130"),
                                      **layout_oscuro)
                    st.plotly_chart(fig, width="stretch")

            # --- Reporte imprimible --------------------------------------------
            st.markdown("---")
            meta_p = {
                "proyecto": proyecto,
                "sucursal": ("Todas las sucursales" if suc_sel == TODAS_P
                             else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                "dias_demanda": df_full.attrs.get("dias_demanda", ""),
                "lead_time": f"{df_full.attrs.get('lead_time_dias', 0):.0f}",
            }
            html_p = generar_reporte_politicas(proyecto, meta_p, df, r)
            st.download_button(
                "🖨 Descargar reporte (HTML imprimible)", html_p.encode("utf-8"),
                f"politicas_stock_{meta_p['fecha_stock']}.html",
                "text/html", key="dl_rep_pol",
            )


# ---------------------------------------------------------------------------
# Página: TRANSFERENCIAS
# ---------------------------------------------------------------------------

elif pagina == "Transferencias":
    st.title("Transferencias sugeridas")
    st.caption(
        "Redistribución lateral: SKUs en **exceso** en una sucursal que otra "
        "necesita **reponer** — se reabastece con capital ya comprado, sin "
        "esperar al proveedor. El donante nunca baja de su nivel óptimo. "
        "Los depósitos logísticos quedan fuera (CD→sucursal es la reposición normal)."
    )

    snapshots = listar_fechas_valorizacion(proyecto)
    if not snapshots:
        st.info("Se necesita al menos un snapshot de stock.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            fecha_stock = st.selectbox("Stock al", snapshots, key="tr_fs")
        with c2:
            dias_demanda = st.selectbox(
                "Ventana de demanda", [56, 90, 180], index=1,
                format_func=lambda d: f"{d} días", key="tr_vent")
        with c3:
            lead_time = st.number_input("Lead time (días)", 1, 120, 7, key="tr_lead")

        if st.button("Sugerir transferencias"):
            with st.spinner("Calculando políticas y cruzando excesos con faltantes..."):
                try:
                    df_tr = sugerir_transferencias(
                        proyecto, fecha_stock=fecha_stock,
                        dias_demanda=dias_demanda, lead_time_dias=lead_time,
                    )
                    st.session_state["df_transf"] = df_tr
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_transf" in st.session_state:
            df_full = st.session_state["df_transf"]
            st.markdown("---")

            if df_full.empty:
                st.info("No hay cruces exceso ↔ reposición entre sucursales con estos parámetros.")
            else:
                # Filtros
                fc1, fc2, fc3, fc4 = st.columns(4)
                with fc1:
                    f_ori = st.multiselect(
                        "Origen", sorted(df_full["origen"].unique().tolist()), key="tr_f_ori")
                with fc2:
                    f_des = st.multiselect(
                        "Destino", sorted(df_full["destino"].unique().tolist()), key="tr_f_des")
                with fc3:
                    f_gsr = st.multiselect(
                        "Gran Super Rubro",
                        sorted(df_full["gran_super_rubro"].dropna().unique().tolist()),
                        key="tr_f_gsr")
                with fc4:
                    f_txt = st.text_input("Buscar SKU / descripción", key="tr_f_txt")

                df = df_full
                if f_ori:
                    df = df[df["origen"].isin(f_ori)]
                if f_des:
                    df = df[df["destino"].isin(f_des)]
                if f_gsr:
                    df = df[df["gran_super_rubro"].isin(f_gsr)]
                if f_txt:
                    t = f_txt.strip().lower()
                    df = df[df["codigo"].str.lower().str.contains(t, na=False)
                            | df["descripcion"].str.lower().str.contains(t, na=False)]

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Sugerencias", f"{len(df):,}")
                k2.metric("SKUs", f"{df['codigo'].nunique():,}")
                k3.metric("Unidades", f"{df['unidades'].sum():,.0f}")
                k4.metric("Valor a costo", formatear_pesos(df["valor"].sum()),
                          delta="reposición sin comprar", delta_color="off")
                st.caption(f"Stock al **{df_full.attrs.get('fecha_stock')}**")

                # Tabla
                st.markdown("#### Detalle")
                cols = ["codigo", "descripcion", "rubro", "clase_abc",
                        "origen", "origen_nombre", "destino", "destino_nombre",
                        "unidades", "valor", "stock_origen", "optimo_origen",
                        "stock_destino", "optimo_destino"]
                df_display = df[cols].copy()
                for c in ["unidades", "stock_origen", "optimo_origen",
                          "stock_destino", "optimo_destino"]:
                    df_display[c] = df_display[c].apply(formatear_unidades)
                df_display["valor"] = df_display["valor"].apply(formatear_pesos)
                df_display.columns = ["Código", "Descripción", "Rubro", "ABC",
                                      "Origen", "Suc. origen", "Destino", "Suc. destino",
                                      "Enviar u.", "Valor $", "Stock orig.", "Ópt orig.",
                                      "Stock dest.", "Ópt dest."]
                st.dataframe(df_display.head(2000), width="stretch", hide_index=True)
                botones_descarga(df[cols], "transferencias_sugeridas", "tr")

                # Matriz origen × destino
                st.markdown("#### Valor por ruta (origen → destino)")
                mat = df.pivot_table(index="origen", columns="destino",
                                     values="valor", aggfunc="sum").fillna(0)
                fig = go.Figure(go.Heatmap(
                    z=mat.values, x=mat.columns, y=mat.index,
                    colorscale=[[0, "#1e2130"], [1, "#64ffda"]],
                    text=(mat.values / 1000).round(0), texttemplate="%{text:,}K",
                    showscale=False,
                ))
                fig.update_layout(
                    height=420,
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(title="Destino"), yaxis=dict(title="Origen"),
                )
                st.plotly_chart(fig, width="stretch")

                # Reporte imprimible
                st.markdown("---")
                meta_t = {
                    "proyecto": proyecto,
                    "fecha_stock": df_full.attrs.get("fecha_stock", ""),
                    "dias_demanda": dias_demanda,
                }
                html_t = generar_reporte_transferencias(proyecto, meta_t, df)
                st.download_button(
                    "🖨 Descargar reporte (HTML imprimible)", html_t.encode("utf-8"),
                    f"transferencias_{meta_t['fecha_stock']}.html",
                    "text/html", key="dl_rep_transf",
                )


# ---------------------------------------------------------------------------
# Página: MÁRGENES
# ---------------------------------------------------------------------------

elif pagina == "Márgenes":
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


# ---------------------------------------------------------------------------
# Página: FORECAST
# ---------------------------------------------------------------------------

elif pagina == "Forecast":
    st.title("Forecast de ventas")
    st.caption(
        "Proyección mensual de **unidades vendidas** por nivel de agregación "
        "(a nivel SKU la demanda es errática y el forecast sería ruido). "
        "Modelo: estacionalidad + tendencia robusta amortiguada; los meses "
        "atípicos se detectan y no dominan el ajuste. El error esperado (MAPE) "
        "se mide por backtest contra los últimos meses reales."
    )

    ETIQUETA_NIVEL = {
        "total": "Total", "gran_super_rubro": "Gran Super Rubro",
        "rubro": "Rubro", "sucursal": "Sucursal",
    }
    ETIQUETA_METRICA = {
        "ventas": "Ventas (salida)",
        "transferencias": "Transferencias recibidas",
    }
    sucursales = listar_sucursales(proyecto)

    if sucursales.empty:
        st.info("No hay movimientos cargados.")
    else:
        TODAS_F = "__todas__"
        suc_label = dict(zip(sucursales["codigodepo"], sucursales["nombre"].fillna("")))
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metrica = st.selectbox(
                "Métrica a proyectar", list(METRICAS),
                format_func=lambda m: ETIQUETA_METRICA[m], key="fc_metrica",
                help="Proyectar transferencias sirve para planificar el "
                     "abastecimiento; compararlas contra ventas muestra si "
                     "una caída de venta es de demanda o de abastecimiento.",
            )
        with c2:
            nivel = st.selectbox(
                "Nivel de agregación", list(NIVELES),
                format_func=lambda n: ETIQUETA_NIVEL[n], key="fc_nivel",
            )
        with c3:
            suc_sel = st.selectbox(
                "Sucursal", [TODAS_F] + sucursales["codigodepo"].tolist(),
                format_func=lambda c: ("⊕ Todas" if c == TODAS_F
                                       else f"{c} — {suc_label.get(c, '')}".strip(" —")),
                key="fc_suc", disabled=(nivel == "sucursal"),
            )
        with c4:
            horizonte = st.selectbox(
                "Horizonte", [3, 6, 12], index=1,
                format_func=lambda h: f"{h} meses", key="fc_hor",
            )

        superponer = st.checkbox(
            f"Superponer la serie real de "
            f"{'transferencias' if metrica == 'ventas' else 'ventas'}",
            value=True, key="fc_overlay",
            help="Si las transferencias caen antes que las ventas, la caída "
                 "es de abastecimiento, no de demanda.",
        )

        if st.button("Proyectar"):
            with st.spinner("Proyectando..."):
                try:
                    depo = (None if (suc_sel == TODAS_F or nivel == "sucursal")
                            else suc_sel)
                    df_fc = forecast_ventas(
                        proyecto, codigodepo=depo,
                        nivel=nivel, horizonte=horizonte, backtest=3,
                        metrica=metrica,
                    )
                    st.session_state["df_fc"] = df_fc
                    otra = "transferencias" if metrica == "ventas" else "ventas"
                    st.session_state["df_fc_otra"] = serie_mensual_real(
                        proyecto, codigodepo=depo, nivel=nivel, metrica=otra,
                    )
                    st.session_state["fc_otra_nombre"] = ETIQUETA_METRICA[otra]
                except Exception as e:
                    st.error(f"Error: {e}")

        if "df_fc" in st.session_state:
            df_fc = st.session_state["df_fc"]
            if df_fc.empty:
                st.info("Sin datos suficientes para proyectar.")
            else:
                st.markdown("---")
                mapes = df_fc.attrs.get("mape", {})
                atipicos = df_fc.attrs.get("meses_atipicos", {})
                excluidos = df_fc.attrs.get("grupos_excluidos", [])

                grupos = sorted(df_fc["grupo"].unique().tolist())
                grupo_sel = (st.selectbox("Grupo", grupos, key="fc_grupo")
                             if len(grupos) > 1 else grupos[0])

                g = df_fc[df_fc["grupo"] == grupo_sel]
                reales = g[g["tipo"] == "real"]
                fc = g[g["tipo"] == "forecast"]

                k1, k2, k3 = st.columns(3)
                if not fc.empty:
                    prox = fc.iloc[0]
                    k1.metric(f"Forecast {prox['mes']}",
                              f"{prox['unidades']:,.0f} u.",
                              delta=formatear_pesos(prox["valor"]), delta_color="off")
                    k2.metric(f"Total {len(fc)} meses",
                              f"{fc['unidades'].sum():,.0f} u.",
                              delta=formatear_pesos(fc["valor"].sum()), delta_color="off")
                mape_g = mapes.get(str(grupo_sel))
                k3.metric("Error esperado (MAPE backtest)",
                          f"{mape_g:.0f}%" if mape_g is not None else "—",
                          help="Promedio del error porcentual al predecir los "
                               "últimos 3 meses reales con el modelo.")

                at_g = atipicos.get(str(grupo_sel), [])
                if at_g:
                    st.warning(
                        f"Meses atípicos detectados (no dominan el ajuste, "
                        f"conviene investigarlos en Control de merma / con el ERP): "
                        f"{', '.join(at_g)}"
                    )

                # --- gráfico ------------------------------------------------
                fig = go.Figure()
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_sup"],
                    mode="lines", line=dict(width=0), showlegend=False,
                )
                fig.add_scatter(
                    name="Banda 95%", x=fc["mes"], y=fc["banda_inf"],
                    mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(255,169,77,0.15)",
                )
                fig.add_scatter(
                    name="Real", x=reales["mes"], y=reales["unidades"],
                    mode="lines+markers", line=dict(color="#64ffda", width=2),
                    marker=dict(size=6),
                )
                df_at = reales[reales["mes"].isin(at_g)]
                if not df_at.empty:
                    fig.add_scatter(
                        name="Atípico", x=df_at["mes"], y=df_at["unidades"],
                        mode="markers",
                        marker=dict(size=11, color="#ff6b6b", symbol="x"),
                    )
                if superponer and "df_fc_otra" in st.session_state:
                    df_o = st.session_state["df_fc_otra"]
                    if not df_o.empty:
                        df_og = (df_o[df_o["grupo"] == grupo_sel]
                                 .sort_values("mes"))
                        if not df_og.empty:
                            fig.add_scatter(
                                name=st.session_state.get("fc_otra_nombre", "Otra métrica"),
                                x=df_og["mes"], y=df_og["unidades"],
                                mode="lines",
                                line=dict(color="#748ffc", width=1.5, dash="dot"),
                            )
                fig.add_scatter(
                    name="Forecast", x=fc["mes"], y=fc["unidades"],
                    mode="lines+markers",
                    line=dict(color="#ffa94d", width=2, dash="dash"),
                    marker=dict(size=7),
                )
                fig.update_layout(
                    height=440,
                    plot_bgcolor="#0f1117", paper_bgcolor="#0f1117",
                    font=dict(color="#ccd6f6", family="IBM Plex Mono"),
                    xaxis=dict(gridcolor="#1e2130"),
                    yaxis=dict(title="Unidades / mes", gridcolor="#1e2130"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, width="stretch")

                # --- tabla de proyección (todos los grupos) -------------------
                st.markdown("#### Proyección por grupo")
                df_t = df_fc[df_fc["tipo"] == "forecast"].pivot_table(
                    index="grupo", columns="mes", values="unidades", aggfunc="sum",
                ).round(0)
                st.dataframe(
                    df_t.style.format("{:,.0f}"), width="stretch",
                )
                botones_descarga(
                    df_fc[df_fc["tipo"] == "forecast"], "forecast_ventas", "fc")

                meta_f = {
                    "proyecto": proyecto,
                    "metrica": ETIQUETA_METRICA.get(
                        df_fc.attrs.get("metrica", "ventas"), "Ventas"),
                    "nivel": ETIQUETA_NIVEL.get(nivel, nivel),
                    "sucursal": ("Todas" if (suc_sel == TODAS_F or nivel == "sucursal")
                                 else f"{suc_sel} — {suc_label.get(suc_sel, '')}".strip(" —")),
                    "horizonte": horizonte,
                }
                html_f = generar_reporte_forecast(
                    proyecto, meta_f, df_fc, grupo_detalle=grupo_sel)
                st.download_button(
                    "🖨 Descargar reporte (HTML imprimible)", html_f.encode("utf-8"),
                    f"forecast_{meta_f['horizonte']}m.html",
                    "text/html", key="dl_rep_fc",
                )

                if excluidos:
                    st.caption(
                        f"{len(excluidos)} grupos sin proyección por historia "
                        f"insuficiente (< 12 meses): {', '.join(excluidos[:8])}"
                        + ("…" if len(excluidos) > 8 else "")
                    )


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

            sin_log = st.checkbox(
                "Excluir depósitos logísticos (CD)", value=True, key="suc_sinlog",
                help="Los CD no venden: distorsionan el % de merma. "
                     "Marcalos en Ingesta → Depósitos.",
            )
            if sin_log and "es_logistica" in df_res.columns:
                df_res = df_res[~df_res["es_logistica"].fillna(False)]

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
