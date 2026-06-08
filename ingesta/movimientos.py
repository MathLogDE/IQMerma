"""
ingesta/movimientos.py — Carga de planillas de movimientos del ERP

Responsabilidades:
  1. Leer el Excel del ERP (tolerando variaciones de formato)
  2. Normalizar y validar los datos
  3. Detectar el período cubierto por el archivo
  4. Verificar duplicados contra periodos_ingesta
  5. Insertar en DuckDB (movimientos + costo_sku_historial)
  6. Registrar la ingesta en periodos_ingesta

Uso desde Python:
    from ingesta.movimientos import ingestar_movimientos
    resultado = ingestar_movimientos("archivo.xlsx", proyecto="cliente_alfa")
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Columnas esperadas del ERP (nombres originales)
# ---------------------------------------------------------------------------

COLUMNAS_ERP = [
    "FECHA", "TIPOMOV", "TIPO", "NUMERO", "CODIGODEPO",
    "NOMBRE", "CODIGO", "COSTO", "INGRESO", "EGRESO",
    "DIFERENCIA", "USER", "TIPO AJ"
]

# Mapeo a nombres internos de MermaIQ
RENAME_MAP = {
    "FECHA":      "fecha",
    "TIPOMOV":    "tipomov",
    "TIPO":       "tipo",
    "NUMERO":     "numero",
    "CODIGODEPO": "codigodepo",
    "NOMBRE":     "nombre",
    "CODIGO":     "codigo",
    "COSTO":      "costo",
    "INGRESO":    "ingreso",
    "EGRESO":     "egreso",
    "DIFERENCIA": "diferencia_erp",   # renombrada para distinguirla del cálculo propio
    "USER":       "usuario",
    "TIPO AJ":    "tipo_aj",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(proyecto: str) -> duckdb.DuckDBPyConnection:
    """Retorna conexión al DuckDB del proyecto."""
    from setup_db import get_db_path
    db_path = get_db_path(proyecto)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos para '{proyecto}'. "
            f"Corré: python setup_db.py --project {proyecto}"
        )
    return duckdb.connect(str(db_path))


def _leer_excel(filepath: str | Path) -> pd.DataFrame:
    """
    Lee el Excel del ERP y retorna un DataFrame crudo.
    Maneja:
      - Archivos con filas vacías al inicio
      - COSTO con coma decimal (formato argentino)
      - Columnas extra que el ERP pueda agregar en el futuro
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {filepath}")

    # Intentar leer — openpyxl para .xlsx, xlrd para .xls legacy
    try:
        df = pd.read_excel(
            filepath,
            dtype=str,          # leer todo como string para normalizar después
            engine="openpyxl",
        )
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo: {e}")

    # Limpiar nombres de columna (strip espacios y upper)
    df.columns = [str(c).strip().upper() for c in df.columns]

    return df


def _validar_columnas(df: pd.DataFrame) -> list[str]:
    """
    Verifica que estén las columnas obligatorias.
    Retorna lista de columnas faltantes (vacía = OK).
    """
    obligatorias = {"FECHA", "TIPOMOV", "TIPO", "NUMERO", "CODIGODEPO", "CODIGO"}
    presentes = set(df.columns)
    return sorted(obligatorias - presentes)


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transforma el DataFrame crudo en el formato interno de MermaIQ:
      - Renombra columnas
      - Parsea fechas (formato DD/M/YYYY del ERP)
      - Convierte COSTO de coma decimal a float
      - Convierte INGRESO/EGRESO a numérico
      - Recalcula DIFERENCIA internamente (no confiar en el ERP)
      - Limpia strings
    """
    # Quedarse solo con las columnas conocidas (ignorar extras del ERP)
    columnas_presentes = [c for c in COLUMNAS_ERP if c in df.columns]
    df = df[columnas_presentes].copy()

    # Renombrar
    df = df.rename(columns=RENAME_MAP)

    # Limpiar strings
    for col in ["tipomov", "tipo", "numero", "codigodepo", "nombre", "codigo", "usuario", "tipo_aj"]:
        if col in df.columns:
            df[col] = df[col].str.strip().replace("nan", None)

    # Parsear fecha — formato DD/M/YYYY o DD/MM/YYYY
    df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce")

    # Eliminar filas sin fecha (encabezados repetidos, filas vacías, etc.)
    filas_antes = len(df)
    df = df.dropna(subset=["fecha"])
    filas_descartadas = filas_antes - len(df)
    if filas_descartadas > 0:
        print(f"  [info] {filas_descartadas} filas descartadas (sin fecha válida)")

    # Convertir COSTO: coma decimal argentina → float
    # "7437,933884" → 7437.933884
    if "costo" in df.columns:
        df["costo"] = (
            df["costo"]
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )

    # Convertir INGRESO / EGRESO a numérico
    for col in ["ingreso", "egreso"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Recalcular diferencia internamente (no confiar en el ERP)
    df["diferencia"] = df["ingreso"] - df["egreso"]

    # Eliminar columna diferencia_erp (ya no la necesitamos)
    if "diferencia_erp" in df.columns:
        df = df.drop(columns=["diferencia_erp"])

    return df


def _detectar_periodo(df: pd.DataFrame) -> str:
    """
    Detecta el período principal del archivo en formato 'YYYY-MM'.
    Si hay múltiples meses, usa el más frecuente.
    """
    periodos = df["fecha"].dt.to_period("M").value_counts()
    periodo_principal = str(periodos.index[0])
    if len(periodos) > 1:
        print(f"  [info] Múltiples períodos detectados: {list(periodos.index)}")
        print(f"  [info] Período principal (más frecuente): {periodo_principal}")
    return periodo_principal


def _verificar_duplicado(conn: duckdb.DuckDBPyConnection, periodo: str) -> bool:
    """
    Retorna True si el período ya fue cargado previamente.
    """
    result = conn.execute("""
        SELECT COUNT(*) FROM periodos_ingesta
        WHERE tabla = 'movimientos'
        AND periodo = ?
        AND estado = 'ok'
    """, [periodo]).fetchone()
    return result[0] > 0


def _actualizar_costo_historial(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame
) -> int:
    """
    Extrae remitos RE/RI con costo > 0 y los inserta en costo_sku_historial.
    Retorna cantidad de registros insertados.
    """
    remitos = df[
        (df["tipomov"] == "REM") &
        (df["tipo"].isin(["RE", "RI"])) &
        (df["costo"].notna()) &
        (df["costo"] > 0)
    ][["codigo", "codigodepo", "fecha", "costo", "numero"]].copy()

    if remitos.empty:
        return 0

    remitos = remitos.rename(columns={"numero": "numero_remito"})
    remitos["fecha_ingesta"] = datetime.now(timezone.utc)

    conn.execute("""
        INSERT INTO costo_sku_historial (codigo, codigodepo, fecha, costo, numero_remito, fecha_ingesta)
        SELECT codigo, codigodepo, fecha, costo, numero_remito, fecha_ingesta
        FROM remitos
    """)

    return len(remitos)


def _insertar_movimientos(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    periodo: str,
    archivo_origen: str,
) -> int:
    """
    Inserta los movimientos normalizados en DuckDB.
    Retorna cantidad de registros insertados.
    """
    now = datetime.now(timezone.utc)
    df = df.copy()
    df["periodo"] = periodo
    df["archivo_origen"] = archivo_origen
    df["fecha_ingesta"] = now

    # Columnas en el orden del schema
    columnas = [
        "fecha", "tipomov", "tipo", "numero", "codigodepo", "nombre",
        "codigo", "costo", "ingreso", "egreso", "diferencia",
        "usuario", "tipo_aj", "periodo", "archivo_origen", "fecha_ingesta"
    ]
    # Asegurarse de que todas las columnas existen (algunas pueden faltar si el ERP las omitió)
    for col in columnas:
        if col not in df.columns:
            df[col] = None

    df_insert = df[columnas]

    conn.execute("""
        INSERT INTO movimientos
            (fecha, tipomov, tipo, numero, codigodepo, nombre, codigo,
             costo, ingreso, egreso, diferencia, usuario, tipo_aj,
             periodo, archivo_origen, fecha_ingesta)
        SELECT fecha, tipomov, tipo, numero, codigodepo, nombre, codigo,
               costo, ingreso, egreso, diferencia, usuario, tipo_aj,
               periodo, archivo_origen, fecha_ingesta
        FROM df_insert
    """)

    return len(df_insert)


def _registrar_ingesta(
    conn: duckdb.DuckDBPyConnection,
    periodo: str,
    archivo_origen: str,
    registros: int,
) -> None:
    conn.execute("""
        INSERT INTO periodos_ingesta (tabla, periodo, archivo_origen, fecha_ingesta, estado, registros_cargados)
        VALUES ('movimientos', ?, ?, ?, 'ok', ?)
    """, [periodo, archivo_origen, datetime.now(timezone.utc), registros])


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def ingestar_movimientos(
    filepath: str | Path,
    proyecto: str,
    forzar: bool = False,
) -> dict:
    """
    Carga una planilla de movimientos del ERP en DuckDB.

    Args:
        filepath:  Ruta al archivo .xlsx del ERP
        proyecto:  Nombre del proyecto (carpeta bajo projects/)
        forzar:    Si True, reemplaza el período aunque ya exista

    Returns:
        dict con keys: periodo, registros, remitos_historial, advertencias
    """
    filepath = Path(filepath)
    archivo_origen = filepath.name
    advertencias = []

    print(f"\n{'='*55}")
    print(f"  Ingesta de movimientos: {archivo_origen}")
    print(f"  Proyecto: {proyecto}")
    print(f"{'='*55}")

    # 1. Leer
    print("  Leyendo archivo...")
    df_crudo = _leer_excel(filepath)
    print(f"  Filas leídas: {len(df_crudo)}")

    # 2. Validar columnas
    faltantes = _validar_columnas(df_crudo)
    if faltantes:
        raise ValueError(f"Columnas obligatorias faltantes: {faltantes}")

    # 3. Normalizar
    print("  Normalizando datos...")
    df = _normalizar(df_crudo)
    print(f"  Filas válidas: {len(df)}")

    # 4. Resumen previo a la carga
    tipomov_counts = df["tipomov"].value_counts().to_dict()
    sucursales = sorted(df["codigodepo"].dropna().unique().tolist())
    print(f"\n  Movimientos por tipo: {tipomov_counts}")
    print(f"  Sucursales: {sucursales}")
    print(f"  Rango de fechas: {df['fecha'].min().date()} → {df['fecha'].max().date()}")

    # 5. Detectar período
    periodo = _detectar_periodo(df)
    print(f"  Período detectado: {periodo}")

    # 6. Verificar duplicado
    conn = _get_connection(proyecto)

    if _verificar_duplicado(conn, periodo):
        if not forzar:
            conn.close()
            raise ValueError(
                f"El período '{periodo}' ya fue cargado. "
                f"Usá forzar=True para reemplazarlo."
            )
        else:
            print(f"  [warn] Período '{periodo}' ya existe — reemplazando...")
            advertencias.append(f"Período {periodo} reemplazado")
            conn.execute("""
                DELETE FROM movimientos WHERE periodo = ?
            """, [periodo])
            conn.execute("""
                UPDATE periodos_ingesta SET estado = 'reemplazado'
                WHERE tabla = 'movimientos' AND periodo = ?
            """, [periodo])

    # 7. Insertar movimientos
    print("\n  Insertando movimientos...")
    registros = _insertar_movimientos(conn, df, periodo, archivo_origen)
    print(f"  ✓ {registros} registros insertados")

    # 8. Actualizar costo_sku_historial
    print("  Actualizando historial de costos (remitos RE/RI)...")
    remitos_n = _actualizar_costo_historial(conn, df)
    print(f"  ✓ {remitos_n} costos registrados en historial")

    # 9. Registrar ingesta
    _registrar_ingesta(conn, periodo, archivo_origen, registros)
    conn.close()

    print(f"\n  Ingesta completada: {registros} movimientos, período {periodo}")
    print(f"{'='*55}\n")

    return {
        "periodo": periodo,
        "registros": registros,
        "remitos_historial": remitos_n,
        "sucursales": sucursales,
        "tipomov": tipomov_counts,
        "advertencias": advertencias,
    }
