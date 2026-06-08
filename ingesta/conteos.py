"""
ingesta/conteos.py — Carga de planillas de conteo físico del ERP

La planilla de conteo no incluye sucursal — se pasa como parámetro.
El costo unitario del conteo es la fuente primaria de valorización.

Lógica: un conteo por sucursal por fecha. Bloquea duplicados.
forzar=True para reemplazar un conteo ya cargado.

Uso:
    from ingesta.conteos import ingestar_conteo
    resultado = ingestar_conteo(
        "conteo_marzo.xlsx",
        proyecto="cliente_alfa",
        codigodepo="002",
    )
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Columnas esperadas del ERP
# ---------------------------------------------------------------------------

COLUMNAS_ERP = [
    "INVENTARIO", "ORDEN", "CÓDIGO", "DESCRIPCIÓN",
    "STOCK SISTEMA", "TOTAL CONTADO", "DIFERENCIA",
    "COSTO UNITARIO", "COSTO INVENTARIO", "COSTO DIFERENCIA",
    "VENTA PERIODO",
]

# Solo las que importan para MermaIQ
RENAME_MAP = {
    "INVENTARIO":      "numero_inventario",
    "CÓDIGO":          "codigo",
    "DESCRIPCIÓN":     "descripcion",
    "STOCK SISTEMA":   "stock_sistema",
    "TOTAL CONTADO":   "stock_real",
    "COSTO UNITARIO":  "costo_unitario",
}

# Columnas mínimas obligatorias
COLUMNAS_OBLIGATORIAS = {"CÓDIGO", "STOCK SISTEMA", "TOTAL CONTADO", "COSTO UNITARIO"}


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


def _validar_columnas(df: pd.DataFrame) -> list[str]:
    presentes = set(df.columns)
    return sorted(COLUMNAS_OBLIGATORIAS - presentes)


def _validar_codigodepo(conn: duckdb.DuckDBPyConnection, codigodepo: str) -> bool:
    """Verifica que la sucursal exista en la tabla depositos."""
    result = conn.execute(
        "SELECT COUNT(*) FROM depositos WHERE codigodepo = ?",
        [codigodepo]
    ).fetchone()
    return result[0] > 0


def _normalizar(df: pd.DataFrame, codigodepo: str, fecha_conteo: str) -> pd.DataFrame:
    """
    Normaliza el DataFrame crudo:
      - Selecciona columnas conocidas
      - Renombra a nombres internos
      - Parsea numéricos (coma decimal argentina)
      - Recalcula diferencia internamente
      - Agrega codigodepo y fecha_conteo
      - Elimina filas sin código válido
    """
    columnas_presentes = [c for c in RENAME_MAP if c in df.columns]
    df = df[columnas_presentes].copy()
    df = df.rename(columns=RENAME_MAP)

    # Limpiar strings
    for col in ["numero_inventario", "codigo", "descripcion"]:
        if col in df.columns:
            df[col] = df[col].str.strip().replace("nan", None)

    # Eliminar filas sin código (totales, separadores, filas vacías del ERP)
    filas_antes = len(df)
    df = df.dropna(subset=["codigo"])
    descartadas = filas_antes - len(df)
    if descartadas > 0:
        print(f"  [info] {descartadas} filas descartadas (sin código válido)")

    # Convertir numéricos — coma decimal argentina
    for col in ["stock_sistema", "stock_real", "costo_unitario"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
                .pipe(pd.to_numeric, errors="coerce")
            )

    # Recalcular diferencia internamente (stock_real - stock_sistema)
    # Negativo = faltante = merma
    df["diferencia"] = df["stock_real"] - df["stock_sistema"]

    # Agregar contexto
    df["codigodepo"] = codigodepo
    df["fecha_conteo"] = pd.to_datetime(fecha_conteo)

    return df


def _verificar_duplicado(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_conteo: str,
) -> bool:
    """Retorna True si ya existe un conteo para esta sucursal y fecha."""
    result = conn.execute("""
        SELECT COUNT(*) FROM periodos_ingesta
        WHERE tabla = 'conteos'
        AND periodo = ?
        AND estado = 'ok'
    """, [f"{codigodepo}/{fecha_conteo}"]).fetchone()
    return result[0] > 0


def _insertar_conteo(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    archivo_origen: str,
) -> int:
    now = datetime.now(timezone.utc)
    df = df.copy()
    df["archivo_origen"] = archivo_origen
    df["fecha_ingesta"] = now

    columnas = [
        "codigodepo", "fecha_conteo", "numero_inventario", "codigo", "descripcion",
        "stock_sistema", "stock_real", "diferencia", "costo_unitario",
        "archivo_origen", "fecha_ingesta"
    ]
    for col in columnas:
        if col not in df.columns:
            df[col] = None

    df_insert = df[columnas]

    conn.execute("""
        INSERT INTO conteos
            (codigodepo, fecha_conteo, numero_inventario, codigo, descripcion,
             stock_sistema, stock_real, diferencia, costo_unitario,
             archivo_origen, fecha_ingesta)
        SELECT codigodepo, fecha_conteo, numero_inventario, codigo, descripcion,
               stock_sistema, stock_real, diferencia, costo_unitario,
               archivo_origen, fecha_ingesta
        FROM df_insert
    """)
    return len(df_insert)


def _registrar_ingesta(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_conteo: str,
    archivo_origen: str,
    registros: int,
) -> None:
    periodo = f"{codigodepo}/{fecha_conteo}"
    conn.execute("""
        INSERT INTO periodos_ingesta
            (tabla, periodo, codigodepo, archivo_origen, fecha_ingesta, estado, registros_cargados)
        VALUES ('conteos', ?, ?, ?, ?, 'ok', ?)
    """, [periodo, codigodepo, archivo_origen, datetime.now(timezone.utc), registros])


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def ingestar_conteo(
    filepath: str | Path,
    proyecto: str,
    codigodepo: str,
    fecha_conteo: str,
    forzar: bool = False,
) -> dict:
    """
    Carga una planilla de conteo físico del ERP en DuckDB.

    Args:
        filepath:     Ruta al archivo .xlsx del ERP
        proyecto:     Nombre del proyecto (carpeta bajo projects/)
        codigodepo:   Código de sucursal (ej: "002") — no viene en el archivo
        fecha_conteo: Fecha del conteo en formato "YYYY-MM-DD" (ej: "2026-03-31")
        forzar:       Si True, reemplaza el conteo aunque ya exista

    Returns:
        dict con keys: codigodepo, fecha_conteo, registros, skus_con_diferencia,
                       merma_unidades, advertencias
    """
    filepath = Path(filepath)
    archivo_origen = filepath.name
    advertencias = []

    print(f"\n{'='*55}")
    print(f"  Ingesta de conteo: {archivo_origen}")
    print(f"  Proyecto:   {proyecto}")
    print(f"  Sucursal:   {codigodepo}")
    print(f"  Fecha:      {fecha_conteo}")
    print(f"{'='*55}")

    # 1. Leer
    print("  Leyendo archivo...")
    df_crudo = _leer_excel(filepath)
    print(f"  Filas leídas: {len(df_crudo)}")

    # 2. Validar columnas
    faltantes = _validar_columnas(df_crudo)
    if faltantes:
        raise ValueError(f"Columnas obligatorias faltantes: {faltantes}")

    # 3. Conectar y validar sucursal
    conn = _get_connection(proyecto)
    if not _validar_codigodepo(conn, codigodepo):
        conn.close()
        raise ValueError(
            f"La sucursal '{codigodepo}' no existe en la tabla depositos. "
            f"Cargá primero el archivo de depósitos con cargar_depositos()."
        )

    # 4. Normalizar
    print("  Normalizando datos...")
    df = _normalizar(df_crudo, codigodepo, fecha_conteo)
    print(f"  Filas válidas: {len(df)}")

    # 5. Resumen previo
    skus_con_diferencia = int((df["diferencia"] != 0).sum())
    merma_unidades = float(df[df["diferencia"] < 0]["diferencia"].abs().sum())
    merma_valorizada = float(
        (df[df["diferencia"] < 0]["diferencia"].abs() *
         df[df["diferencia"] < 0]["costo_unitario"].fillna(0)).sum()
    )

    print(f"\n  SKUs en conteo:         {len(df)}")
    print(f"  SKUs con diferencia:    {skus_con_diferencia}")
    print(f"  Merma en unidades:      {merma_unidades:,.0f}")
    print(f"  Merma valorizada:       $ {merma_valorizada:,.2f}")

    # 6. Verificar duplicado
    if _verificar_duplicado(conn, codigodepo, fecha_conteo):
        if not forzar:
            conn.close()
            raise ValueError(
                f"Ya existe un conteo para sucursal '{codigodepo}' en fecha '{fecha_conteo}'. "
                f"Usá forzar=True para reemplazarlo."
            )
        else:
            print(f"  [warn] Conteo ya existe — reemplazando...")
            advertencias.append(f"Conteo {codigodepo}/{fecha_conteo} reemplazado")
            conn.execute("""
                DELETE FROM conteos
                WHERE codigodepo = ? AND fecha_conteo = ?
            """, [codigodepo, fecha_conteo])
            conn.execute("""
                UPDATE periodos_ingesta SET estado = 'reemplazado'
                WHERE tabla = 'conteos' AND periodo = ?
            """, [f"{codigodepo}/{fecha_conteo}"])

    # 7. Insertar
    print("\n  Insertando conteo...")
    registros = _insertar_conteo(conn, df, archivo_origen)
    print(f"  ✓ {registros} registros insertados")

    # 8. Registrar ingesta
    _registrar_ingesta(conn, codigodepo, fecha_conteo, archivo_origen, registros)
    conn.close()

    print(f"\n  Ingesta completada: {registros} SKUs, sucursal {codigodepo}, {fecha_conteo}")
    print(f"{'='*55}\n")

    return {
        "codigodepo": codigodepo,
        "fecha_conteo": fecha_conteo,
        "registros": registros,
        "skus_con_diferencia": skus_con_diferencia,
        "merma_unidades": merma_unidades,
        "merma_valorizada": merma_valorizada,
        "advertencias": advertencias,
    }
