"""
ingesta/stock.py — Carga de planillas de stock por sucursal

Formato esperado del ERP (columnas pivotadas):
    CODIGO | Lista 1 | Costo | UxB | SUC1 | SUC2 | SUC3 | ...

Las primeras columnas son atributos del SKU.
Las columnas restantes son sucursales — se detectan dinámicamente
comparando contra depositos.abreviacion (case-insensitive).

La ingesta despivotea el archivo: una fila por (codigo, codigodepo).
Los snapshots son acumulativos — nunca se sobrescriben.
La vista `stock_actual` en DuckDB siempre apunta al más reciente.

Columnas fijas reconocidas (nombres normalizados a uppercase):
    CODIGO    — SKU (obligatorio)
    LISTA 1   — precio de lista
    COSTO     — costo unitario del ERP
    UXB       — unidades por bulto

Todo lo que no sea columna fija se trata como sucursal.
Las columnas sin mapeo en depositos se reportan como advertencia
pero no interrumpen la carga de las que sí mapearon.

Uso:
    from ingesta.stock import ingestar_stock

    resultado = ingestar_stock(
        "stock_2026-06-09.xlsx",
        proyecto="cliente_alfa",
        fecha_snapshot="2026-06-09",
    )
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Columnas fijas del ERP (no son sucursales)
# El ERP puede variar mayúsculas/minúsculas y espacios — se normalizan.
# ---------------------------------------------------------------------------

COLUMNAS_FIJAS = {"CODIGO", "CÓDIGO", "LISTA 1", "COSTO", "UXB", "CANT X BULTO"}

RENAME_FIJAS = {
    "CODIGO":       "codigo",
    "CÓDIGO":       "codigo",
    "LISTA 1":      "lista_1",
    "COSTO":        "costo",
    "UXB":          "uxb",
    "CANT X BULTO": "uxb",
}

# Columnas derivadas/agregadas del ERP que NO son sucursales — se ignoran.
# LOG = CDC + CR2 (stock logístico ya derivado); no debe cargarse como depósito.
COLUMNAS_EXCLUIDAS = {"LOG"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(proyecto: str) -> duckdb.DuckDBPyConnection:
    from setup_db import get_db_path
    db_path = get_db_path(proyecto)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos para '{proyecto}'. "
            f"Corré: python setup_db.py --project {proyecto}"
        )
    return duckdb.connect(str(db_path))


def _leer_excel(filepath: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(filepath, dtype=str, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo: {e}")
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df


def _parsear_numerico(serie: pd.Series) -> pd.Series:
    """Convierte coma decimal argentina a float."""
    return (
        serie
        .str.replace(",", ".", regex=False)
        .str.replace(" ", "", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
    )


def _obtener_mapa_abreviaciones(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """
    Retorna {ABREVIACION_UPPER: codigodepo} desde la tabla depositos.
    El mapeo es case-insensitive en la clave.
    """
    rows = conn.execute("""
        SELECT UPPER(TRIM(abreviacion)), codigodepo
        FROM depositos
        WHERE abreviacion IS NOT NULL AND TRIM(abreviacion) != ''
    """).fetchall()
    return {r[0]: r[1] for r in rows}


def _detectar_columnas(
    columnas: list[str],
    mapa_abrev: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Clasifica las columnas del Excel en tres grupos:
      - fijas:          columnas de atributos del SKU (CODIGO, LISTA 1, etc.)
      - sucursales:     columnas que mapearon a un codigodepo
      - no_mapeadas:    columnas que no son fijas ni están en depositos

    Retorna (fijas_presentes, sucursales_mapeadas, no_mapeadas)
    """
    fijas_presentes = [c for c in columnas if c in COLUMNAS_FIJAS]
    sucursales_mapeadas = []
    no_mapeadas = []

    for col in columnas:
        if col in COLUMNAS_FIJAS or col in COLUMNAS_EXCLUIDAS:
            continue
        if col in mapa_abrev:
            sucursales_mapeadas.append(col)
        else:
            no_mapeadas.append(col)

    return fijas_presentes, sucursales_mapeadas, no_mapeadas


def _despivotear(
    df: pd.DataFrame,
    mapa_abrev: dict[str, str],
    fijas_presentes: list[str],
    sucursales_mapeadas: list[str],
) -> pd.DataFrame:
    """
    Transforma el formato pivotado del ERP en formato relacional.

    Entrada (una fila por SKU, una columna por sucursal):
        CODIGO | LISTA 1 | COSTO | UXB | RDB | SJN | CDC | ...

    Salida (una fila por SKU × sucursal):
        codigo | lista_1 | costo | uxb | codigodepo | stock

    Solo genera filas para sucursales que están en sucursales_mapeadas.
    """
    # Atributos fijos del SKU
    df_fijas = df[fijas_presentes].rename(columns=RENAME_FIJAS).copy()

    # Convertir fijas numéricas
    for col in ["lista_1", "costo", "uxb"]:
        if col in df_fijas.columns:
            df_fijas[col] = _parsear_numerico(df_fijas[col])

    # Limpiar codigo
    df_fijas["codigo"] = df_fijas["codigo"].str.strip().replace("nan", None)

    # Eliminar filas sin código válido (totales, separadores, etc.)
    filas_antes = len(df_fijas)
    df_fijas = df_fijas.dropna(subset=["codigo"])
    descartadas = filas_antes - len(df_fijas)
    if descartadas > 0:
        print(f"  [info] {descartadas} filas descartadas (sin código válido)")

    # Despivotear: una fila por sucursal
    bloques = []
    for col_abrev in sucursales_mapeadas:
        codigodepo = mapa_abrev[col_abrev]
        bloque = df_fijas.copy()
        bloque["codigodepo"] = codigodepo
        bloque["stock"] = _parsear_numerico(df.loc[df_fijas.index, col_abrev]).fillna(0)
        bloques.append(bloque)

    df_long = pd.concat(bloques, ignore_index=True)

    # Reordenar columnas de forma consistente
    cols_orden = ["codigo", "codigodepo", "stock"]
    for col in ["lista_1", "costo", "uxb"]:
        if col in df_long.columns:
            cols_orden.append(col)
    df_long = df_long[cols_orden]

    return df_long


def _verificar_duplicado(conn: duckdb.DuckDBPyConnection, fecha_snapshot: str) -> bool:
    result = conn.execute("""
        SELECT COUNT(*) FROM periodos_ingesta
        WHERE tabla = 'stock_sucursal'
          AND periodo = ?
          AND estado = 'ok'
    """, [fecha_snapshot]).fetchone()
    return result[0] > 0


def _insertar_stock(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    fecha_snapshot: str,
    archivo_origen: str,
) -> int:
    now = datetime.now(timezone.utc)
    df = df.copy()
    df["fecha_snapshot"] = pd.to_datetime(fecha_snapshot)
    df["archivo_origen"] = archivo_origen
    df["fecha_ingesta"] = now

    # Asegurar columnas opcionales
    for col in ["lista_1", "costo", "uxb"]:
        if col not in df.columns:
            df[col] = None

    conn.execute("""
        INSERT INTO stock_sucursal
            (codigo, codigodepo, fecha_snapshot, stock,
             lista_1, costo, uxb, archivo_origen, fecha_ingesta)
        SELECT codigo, codigodepo, fecha_snapshot, stock,
               lista_1, costo, uxb, archivo_origen, fecha_ingesta
        FROM df
    """)
    return len(df)


def _registrar_ingesta(
    conn: duckdb.DuckDBPyConnection,
    fecha_snapshot: str,
    archivo_origen: str,
    registros: int,
) -> None:
    conn.execute("""
        INSERT INTO periodos_ingesta
            (tabla, periodo, archivo_origen, fecha_ingesta, estado, registros_cargados)
        VALUES ('stock_sucursal', ?, ?, ?, 'ok', ?)
    """, [fecha_snapshot, archivo_origen, datetime.now(timezone.utc), registros])


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def ingestar_stock(
    filepath: str | Path,
    proyecto: str,
    fecha_snapshot: str,
    forzar: bool = False,
) -> dict:
    """
    Carga una planilla de stock por sucursal en DuckDB.

    El archivo viene pivotado (una columna por sucursal). La función
    detecta las sucursales dinámicamente usando depositos.abreviacion
    y almacena el resultado en formato relacional como snapshot histórico.

    Args:
        filepath:       Ruta al archivo .xlsx
        proyecto:       Nombre del proyecto (carpeta bajo projects/)
        fecha_snapshot: Fecha del stock en formato "YYYY-MM-DD"
        forzar:         Si True, reemplaza el snapshot aunque ya exista

    Returns:
        dict con keys:
            fecha_snapshot, registros, sucursales_mapeadas,
            columnas_no_mapeadas, skus, advertencias
    """
    filepath = Path(filepath)
    archivo_origen = filepath.name
    advertencias = []

    print(f"\n{'='*55}")
    print(f"  Ingesta de stock: {archivo_origen}")
    print(f"  Proyecto:         {proyecto}")
    print(f"  Snapshot:         {fecha_snapshot}")
    print(f"{'='*55}")

    # 1. Leer
    print("  Leyendo archivo...")
    df_crudo = _leer_excel(filepath)
    print(f"  Filas leídas: {len(df_crudo)}, columnas: {len(df_crudo.columns)}")

    if "CODIGO" not in df_crudo.columns and "CÓDIGO" not in df_crudo.columns:
        raise ValueError(
            "Columna 'CODIGO' no encontrada. "
            f"Columnas presentes: {list(df_crudo.columns)}"
        )

    # 2. Conectar y obtener mapa de abreviaciones
    conn = _get_connection(proyecto)
    mapa_abrev = _obtener_mapa_abreviaciones(conn)

    if not mapa_abrev:
        conn.close()
        raise ValueError(
            "La tabla depositos no tiene abreviaciones cargadas. "
            "Cargá primero el archivo de depósitos con la columna 'Abreviación'."
        )

    # 3. Clasificar columnas
    fijas_presentes, sucursales_mapeadas, cols_no_mapeadas = _detectar_columnas(
        list(df_crudo.columns), mapa_abrev
    )

    print(f"\n  Columnas fijas encontradas:   {fijas_presentes}")
    print(f"  Sucursales mapeadas ({len(sucursales_mapeadas)}): {sucursales_mapeadas}")

    if cols_no_mapeadas:
        msg = f"Columnas sin mapeo en depositos (ignoradas): {cols_no_mapeadas}"
        print(f"  [warn] {msg}")
        advertencias.append(msg)

    if not sucursales_mapeadas:
        conn.close()
        raise ValueError(
            "Ninguna columna pudo mapearse a un codigodepo. "
            "Verificá que las abreviaciones en depositos coincidan con "
            f"los encabezados del archivo. "
            f"Abreviaciones conocidas: {list(mapa_abrev.keys())}"
        )

    # 4. Despivotear y normalizar
    print("\n  Despivoteando...")
    df_long = _despivotear(df_crudo, mapa_abrev, fijas_presentes, sucursales_mapeadas)

    sucursales_cargadas = sorted(df_long["codigodepo"].unique().tolist())
    skus_unicos = df_long["codigo"].nunique()

    print(f"  Filas tras despivoteo: {len(df_long):,}")
    print(f"  SKUs únicos:           {skus_unicos:,}")
    print(f"  Sucursales:            {sucursales_cargadas}")
    print(f"  Stock total (todas):   {df_long['stock'].sum():,.0f} unidades")

    # Resumen por sucursal
    resumen = (
        df_long.groupby("codigodepo")
        .agg(skus=("codigo", "count"), stock_total=("stock", "sum"))
        .reset_index()
    )
    for _, r in resumen.iterrows():
        print(f"    {r['codigodepo']}: {int(r['skus']):>6} SKUs | {r['stock_total']:>10,.0f} u.")

    # 5. Verificar duplicado
    if _verificar_duplicado(conn, fecha_snapshot):
        if not forzar:
            conn.close()
            raise ValueError(
                f"Ya existe un snapshot de stock para '{fecha_snapshot}'. "
                f"Usá forzar=True para reemplazarlo."
            )
        else:
            print(f"\n  [warn] Snapshot ya existe — reemplazando...")
            advertencias.append(f"Snapshot {fecha_snapshot} reemplazado")
            conn.execute(
                "DELETE FROM stock_sucursal WHERE fecha_snapshot = ?",
                [fecha_snapshot]
            )
            conn.execute("""
                UPDATE periodos_ingesta SET estado = 'reemplazado'
                WHERE tabla = 'stock_sucursal' AND periodo = ?
            """, [fecha_snapshot])

    # 6. Insertar
    print("\n  Insertando stock...")
    registros = _insertar_stock(conn, df_long, fecha_snapshot, archivo_origen)
    print(f"  ✓ {registros:,} registros insertados")

    # 7. Registrar ingesta
    _registrar_ingesta(conn, fecha_snapshot, archivo_origen, registros)
    conn.close()

    print(f"\n  Ingesta completada: {registros:,} registros, {len(sucursales_cargadas)} sucursales")
    print(f"{'='*55}\n")

    return {
        "fecha_snapshot":      fecha_snapshot,
        "registros":           registros,
        "skus":                skus_unicos,
        "sucursales_mapeadas": sucursales_cargadas,
        "columnas_no_mapeadas": cols_no_mapeadas,
        "advertencias":        advertencias,
    }


# ---------------------------------------------------------------------------
# Utilidades de consulta (para uso desde core/ y ui/)
# ---------------------------------------------------------------------------

def listar_snapshots(proyecto: str) -> pd.DataFrame:
    """
    Retorna los snapshots de stock cargados, ordenados del más reciente al más antiguo.
    Útil para poblar selectboxes en la UI.
    """
    from setup_db import get_connection
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT
            periodo          AS fecha_snapshot,
            archivo_origen,
            registros_cargados,
            fecha_ingesta,
            estado
        FROM periodos_ingesta
        WHERE tabla = 'stock_sucursal'
        ORDER BY periodo DESC
    """).df()
    conn.close()
    return df


def obtener_snapshot(proyecto: str, fecha_snapshot: str) -> pd.DataFrame:
    """
    Retorna el stock completo de un snapshot específico con nombre de sucursal.
    """
    from setup_db import get_connection
    conn = get_connection(proyecto)
    df = conn.execute("""
        SELECT
            s.codigo,
            a.descripcion,
            s.codigodepo,
            d.nombre     AS sucursal,
            d.abreviacion,
            s.stock,
            s.lista_1,
            s.costo,
            s.uxb,
            s.fecha_snapshot
        FROM stock_sucursal s
        LEFT JOIN depositos d  ON s.codigodepo = d.codigodepo
        LEFT JOIN articulos a  ON s.codigo = a.codigo
        WHERE s.fecha_snapshot = ?
        ORDER BY s.codigodepo, s.codigo
    """, [fecha_snapshot]).df()
    conn.close()
    return df
