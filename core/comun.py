"""
core/comun.py — Utilidades compartidas por todos los dominios de análisis

Centraliza lo que antes estaba duplicado en cada módulo: conexión a la base,
valorización desde el snapshot de stock, jerarquía de rubros y catálogos.
Todos los motores (merma, inventario, distribución, comercial, forecasting)
importan desde acá.
"""

import duckdb
import pandas as pd

# Modos de valorización disponibles (columna del snapshot de stock)
MODOS_VALORIZACION = ("costo", "lista_1")


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def conectar(proyecto: str) -> duckdb.DuckDBPyConnection:
    """Conexión al DuckDB del proyecto (falla claro si no existe)."""
    from setup_db import get_db_path
    db_path = get_db_path(proyecto)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos para '{proyecto}'. "
            f"Corré: python setup_db.py --project {proyecto}"
        )
    return duckdb.connect(str(db_path))


# ---------------------------------------------------------------------------
# Valorización desde stock
# ---------------------------------------------------------------------------

def precios_snapshot(conn: duckdb.DuckDBPyConnection,
                     fecha_valorizacion: str | None = None) -> pd.DataFrame:
    """
    Precio por SKU (costo y lista_1) del snapshot elegido, o el más reciente.

    El precio es global por SKU (una sola columna en el ERP), así que no se
    filtra por sucursal. Con `fecha_valorizacion` se toma el snapshot más
    reciente <= esa fecha; sin ella, el último disponible (valor actual).
    """
    if fecha_valorizacion:
        fv = str(pd.to_datetime(fecha_valorizacion).date())
        return conn.execute("""
            SELECT codigo, costo, lista_1 FROM stock_sucursal
            WHERE fecha_snapshot <= $fv::DATE
            QUALIFY row_number() OVER (
                PARTITION BY codigo
                ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
            ) = 1
        """, {"fv": fv}).df()
    return conn.execute("""
        SELECT codigo, costo, lista_1 FROM stock_sucursal
        QUALIFY row_number() OVER (
            PARTITION BY codigo
            ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
        ) = 1
    """).df()


def snapshot_usado(conn: duckdb.DuckDBPyConnection,
                   fecha_valorizacion: str | None = None):
    """Fecha del snapshot efectivamente usado por precios_snapshot (str o None)."""
    r = conn.execute(
        "SELECT MAX(fecha_snapshot) FROM stock_sucursal"
        + (" WHERE fecha_snapshot <= ?" if fecha_valorizacion else ""),
        [str(pd.to_datetime(fecha_valorizacion).date())] if fecha_valorizacion else [],
    ).fetchone()[0]
    return str(r) if r else None


# ---------------------------------------------------------------------------
# Jerarquía de rubros
# ---------------------------------------------------------------------------

def mapa_estructura(df_est: pd.DataFrame) -> pd.DataFrame:
    """
    Lookup de estructura indexable por código de rubro O por nombre
    (articulos.rubro puede traer cualquiera de los dos).
    """
    registros = []
    for _, r in df_est.iterrows():
        for clave in (r["rubro_cod"], r["rubro"]):
            if pd.notna(clave):
                registros.append({
                    "_clave": str(clave).strip(),
                    "rubro_desc": r["rubro"],
                    "super_rubro": r["super_rubro"],
                    "gran_super_rubro": r["gran_super_rubro"],
                })
    if not registros:
        return pd.DataFrame(columns=["_clave", "rubro_desc", "super_rubro", "gran_super_rubro"])
    return pd.DataFrame(registros).drop_duplicates("_clave")


def adjuntar_rubros(df: pd.DataFrame, df_est: pd.DataFrame) -> pd.DataFrame:
    """
    Resuelve df['rubro'] (código o nombre) a la descripción y agrega
    super_rubro / gran_super_rubro. Requiere columna 'rubro' en df.
    """
    mapa = mapa_estructura(df_est)
    df = df.copy()
    df["_clave"] = df["rubro"].astype("string").str.strip()
    df = df.merge(mapa, on="_clave", how="left").drop(columns=["_clave"])
    df["rubro"] = df["rubro_desc"].fillna(df["rubro"])
    return df.drop(columns=["rubro_desc"])


# ---------------------------------------------------------------------------
# Catálogos (para la UI)
# ---------------------------------------------------------------------------

def listar_categorias(proyecto: str) -> list[str]:
    """Categorías ordenadas según `tipos_categoria`."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT categoria FROM tipos_categoria
            GROUP BY categoria ORDER BY MIN(orden), categoria
        """).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def categorias_merma(proyecto: str) -> list[str]:
    """Categorías marcadas es_merma, en orden de catálogo."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT categoria FROM tipos_categoria
            WHERE es_merma GROUP BY categoria ORDER BY MIN(orden), categoria
        """).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def listar_fechas_valorizacion(proyecto: str) -> list[str]:
    """Fechas de snapshot de stock disponibles, de la más reciente a la más vieja."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT DISTINCT fecha_snapshot FROM stock_sucursal
            ORDER BY fecha_snapshot DESC
        """).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]
