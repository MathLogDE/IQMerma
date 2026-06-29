"""
ingesta/referencias.py — Carga de archivos de referencia

Lógica común: upsert por clave.
  - Si el registro existe y cambió algo → actualizar
  - Si no existe → insertar
  - Si ya no aparece en el archivo → no tocar (conservar histórico)

Tres funciones públicas:
    cargar_depositos(filepath, proyecto)
    cargar_estructura(filepath, proyecto)
    cargar_articulos(filepath, proyecto)

Formatos esperados:

depositos.xlsx:
    Cod. Dep. | Nombre | Dirección | Abreviación

estructura.xlsx:
    Rubro (PK) | Super Rubro | Gran Super Rubro

articulos.xlsx:
    Código | Descripción | Rubro | Marca | EAN | Clase | Activo
    (Activo: "S"/"N", "SI"/"NO", "1"/"0", True/False — se normaliza)
    (Clase: "A"/"B"/"C" — nullable, en instrumentación)

Performance: cargar_articulos usa upsert vectorizado (INSERT OR REPLACE)
para manejar archivos de 100k+ filas en segundos en lugar de minutos.
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers compartidos
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


def _normalizar_activo(serie: pd.Series) -> pd.Series:
    """Convierte distintas representaciones de activo/inactivo a booleano."""
    return serie.str.strip().str.upper().map({
        "S": True, "SI": True, "YES": True, "1": True, "TRUE": True, "ACTIVO": True,
        "N": False, "NO": False, "0": False, "FALSE": False, "INACTIVO": False,
    }).fillna(True)


def _print_resumen(insertados: int, actualizados: int, sin_cambios: int, tabla: str) -> None:
    print(f"  ✓ {tabla}: {insertados} nuevos, {actualizados} actualizados, {sin_cambios} sin cambios")


# ---------------------------------------------------------------------------
# depositos
# ---------------------------------------------------------------------------

DEPOSITOS_COLS = {"COD. DEP.", "NOMBRE", "DIRECCIÓN", "ABREVIACIÓN"}
DEPOSITOS_RENAME = {
    "COD. DEP.":   "codigodepo",
    "NOMBRE":      "nombre",
    "DIRECCIÓN":   "direccion",
    "ABREVIACIÓN": "abreviacion",
}


def cargar_depositos(filepath: str | Path, proyecto: str) -> dict:
    """Upsert de depósitos/sucursales desde archivo de referencia."""
    filepath = Path(filepath)
    print(f"\n{'='*55}")
    print(f"  Carga de depósitos: {filepath.name}")
    print(f"{'='*55}")

    df = _leer_excel(filepath)

    faltantes = sorted(DEPOSITOS_COLS - set(df.columns))
    if faltantes:
        raise ValueError(f"Columnas faltantes en depósitos: {faltantes}")

    df = df[[c for c in DEPOSITOS_RENAME if c in df.columns]].copy()
    df = df.rename(columns=DEPOSITOS_RENAME)
    df = df.dropna(subset=["codigodepo"])
    for col in df.columns:
        df[col] = df[col].str.strip().replace("nan", None)

    now = datetime.now(timezone.utc)
    df["fecha_ingesta"] = now

    conn = _get_connection(proyecto)
    n_antes = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]

    conn.execute("""
        INSERT OR REPLACE INTO depositos
            (codigodepo, nombre, direccion, abreviacion, fecha_ingesta)
        SELECT codigodepo, nombre, direccion, abreviacion, fecha_ingesta
        FROM df
    """)

    n_despues = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]
    conn.close()

    insertados   = n_despues - n_antes
    actualizados = len(df) - insertados
    _print_resumen(insertados, actualizados, 0, "depositos")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": 0}


# ---------------------------------------------------------------------------
# estructura
# ---------------------------------------------------------------------------

ESTRUCTURA_COLS = {"RUBRO", "SUPER RUBRO", "GRAN SUPER RUBRO"}
ESTRUCTURA_RENAME = {
    "RUBRO":            "rubro",
    "SUPER RUBRO":      "super_rubro",
    "GRAN SUPER RUBRO": "gran_super_rubro",
}


def cargar_estructura(filepath: str | Path, proyecto: str) -> dict:
    """
    Upsert de estructura de rubros.
    PK: rubro. Una fila por rubro — no por SKU.
    Join posterior: articulos.rubro → estructura.rubro
    """
    filepath = Path(filepath)
    print(f"\n{'='*55}")
    print(f"  Carga de estructura: {filepath.name}")
    print(f"{'='*55}")

    df = _leer_excel(filepath)

    faltantes = sorted(ESTRUCTURA_COLS - set(df.columns))
    if faltantes:
        raise ValueError(f"Columnas faltantes en estructura: {faltantes}")

    df = df[[c for c in ESTRUCTURA_RENAME if c in df.columns]].copy()
    df = df.rename(columns=ESTRUCTURA_RENAME)
    df = df.dropna(subset=["rubro"])
    for col in df.columns:
        df[col] = df[col].str.strip().replace("nan", None)

    now = datetime.now(timezone.utc)
    df["fecha_ingesta"] = now

    conn = _get_connection(proyecto)
    n_antes = conn.execute("SELECT COUNT(*) FROM estructura").fetchone()[0]

    conn.execute("""
        INSERT OR REPLACE INTO estructura
            (rubro, super_rubro, gran_super_rubro, fecha_ingesta)
        SELECT rubro, super_rubro, gran_super_rubro, fecha_ingesta
        FROM df
    """)

    n_despues = conn.execute("SELECT COUNT(*) FROM estructura").fetchone()[0]
    conn.close()

    insertados   = n_despues - n_antes
    actualizados = len(df) - insertados
    _print_resumen(insertados, actualizados, 0, "estructura")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": 0}


# ---------------------------------------------------------------------------
# articulos
# ---------------------------------------------------------------------------

ARTICULOS_COLS = {"CÓDIGO", "DESCRIPCIÓN", "ACTIVO"}
ARTICULOS_RENAME = {
    "CÓDIGO":      "codigo",
    "DESCRIPCIÓN": "descripcion",
    "RUBRO":       "rubro",
    "MARCA":       "marca",
    "EAN":         "ean",
    "CLASE":       "clase",
    "ACTIVO":      "activo",
}


def cargar_articulos(filepath: str | Path, proyecto: str) -> dict:
    """
    Upsert de artículos. PK: codigo.
    Nunca elimina — conserva histórico de SKUs discontinuados.
    Campos opcionales: rubro, marca, ean, clase.

    Optimizado para archivos grandes (100k+ filas):
    usa INSERT OR REPLACE vectorizado en lugar de loop fila por fila.
    120k filas: ~5 segundos vs ~10 minutos con loop.
    """
    filepath = Path(filepath)
    print(f"\n{'='*55}")
    print(f"  Carga de artículos: {filepath.name}")
    print(f"{'='*55}")

    print("  Leyendo archivo...")
    df = _leer_excel(filepath)
    print(f"  Filas leídas: {len(df)}")

    faltantes = sorted(ARTICULOS_COLS - set(df.columns))
    if faltantes:
        raise ValueError(f"Columnas faltantes en artículos: {faltantes}")

    print("  Normalizando...")
    df = df[[c for c in ARTICULOS_RENAME if c in df.columns]].copy()
    df = df.rename(columns=ARTICULOS_RENAME)
    df = df.dropna(subset=["codigo"])

    for col in ["codigo", "descripcion", "rubro", "marca", "ean", "clase"]:
        if col in df.columns:
            df[col] = df[col].str.strip().replace("nan", None)

    df["activo"] = _normalizar_activo(df["activo"])

    if "clase" in df.columns:
        df["clase"] = df["clase"].str.upper().where(df["clase"].str.upper().isin(["A", "B", "C"]))

    # Asegurar columnas opcionales existen
    for col in ["rubro", "marca", "ean", "clase"]:
        if col not in df.columns:
            df[col] = None

    now = datetime.now(timezone.utc)
    df["fecha_ingesta"] = now

    conn = _get_connection(proyecto)
    n_antes = conn.execute("SELECT COUNT(*) FROM articulos").fetchone()[0]

    print("  Ejecutando upsert vectorizado...")
    conn.execute("""
        INSERT OR REPLACE INTO articulos
            (codigo, descripcion, rubro, marca, ean, clase, activo, fecha_ingesta)
        SELECT codigo, descripcion, rubro, marca, ean, clase, activo, fecha_ingesta
        FROM df
    """)

    n_despues = conn.execute("SELECT COUNT(*) FROM articulos").fetchone()[0]
    conn.close()

    insertados   = n_despues - n_antes
    actualizados = len(df) - insertados
    print(f"  Filas procesadas: {len(df)}")
    _print_resumen(insertados, actualizados, 0, "articulos")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": 0}