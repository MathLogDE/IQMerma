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
    }).fillna(True)  # si no se reconoce, asume activo


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
        if col != "fecha_ingesta":
            df[col] = df[col].str.strip().replace("nan", None)

    conn = _get_connection(proyecto)
    insertados = actualizados = sin_cambios = 0
    now = datetime.now(timezone.utc)

    for _, row in df.iterrows():
        existing = conn.execute(
            "SELECT nombre, direccion, abreviacion FROM depositos WHERE codigodepo = ?",
            [row["codigodepo"]]
        ).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO depositos (codigodepo, nombre, direccion, abreviacion, fecha_ingesta)
                VALUES (?, ?, ?, ?, ?)
            """, [row["codigodepo"], row.get("nombre"), row.get("direccion"), row.get("abreviacion"), now])
            insertados += 1
        else:
            nuevo = (row.get("nombre"), row.get("direccion"), row.get("abreviacion"))
            if existing != nuevo:
                conn.execute("""
                    UPDATE depositos
                    SET nombre = ?, direccion = ?, abreviacion = ?, fecha_ingesta = ?
                    WHERE codigodepo = ?
                """, [*nuevo, now, row["codigodepo"]])
                actualizados += 1
            else:
                sin_cambios += 1

    conn.close()
    _print_resumen(insertados, actualizados, sin_cambios, "depositos")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": sin_cambios}


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

    conn = _get_connection(proyecto)
    insertados = actualizados = sin_cambios = 0
    now = datetime.now(timezone.utc)

    for _, row in df.iterrows():
        existing = conn.execute(
            "SELECT super_rubro, gran_super_rubro FROM estructura WHERE rubro = ?",
            [row["rubro"]]
        ).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO estructura (rubro, super_rubro, gran_super_rubro, fecha_ingesta)
                VALUES (?, ?, ?, ?)
            """, [row["rubro"], row.get("super_rubro"), row.get("gran_super_rubro"), now])
            insertados += 1
        else:
            nuevo = (row.get("super_rubro"), row.get("gran_super_rubro"))
            if existing != nuevo:
                conn.execute("""
                    UPDATE estructura
                    SET super_rubro = ?, gran_super_rubro = ?, fecha_ingesta = ?
                    WHERE rubro = ?
                """, [*nuevo, now, row["rubro"]])
                actualizados += 1
            else:
                sin_cambios += 1

    conn.close()
    _print_resumen(insertados, actualizados, sin_cambios, "estructura")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": sin_cambios}


# ---------------------------------------------------------------------------
# articulos
# ---------------------------------------------------------------------------

ARTICULOS_COLS = {"CÓDIGO", "DESCRIPCIÓN", "ACTIVO"}  # mínimas obligatorias
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
    """
    filepath = Path(filepath)
    print(f"\n{'='*55}")
    print(f"  Carga de artículos: {filepath.name}")
    print(f"{'='*55}")

    df = _leer_excel(filepath)

    faltantes = sorted(ARTICULOS_COLS - set(df.columns))
    if faltantes:
        raise ValueError(f"Columnas faltantes en artículos: {faltantes}")

    df = df[[c for c in ARTICULOS_RENAME if c in df.columns]].copy()
    df = df.rename(columns=ARTICULOS_RENAME)
    df = df.dropna(subset=["codigo"])

    for col in ["codigo", "descripcion", "rubro", "marca", "ean", "clase"]:
        if col in df.columns:
            df[col] = df[col].str.strip().replace("nan", None)

    df["activo"] = _normalizar_activo(df["activo"])

    # Normalizar clase — solo A, B, C válidos
    if "clase" in df.columns:
        df["clase"] = df["clase"].str.upper().where(df["clase"].str.upper().isin(["A", "B", "C"]))

    conn = _get_connection(proyecto)
    insertados = actualizados = sin_cambios = 0
    now = datetime.now(timezone.utc)

    for _, row in df.iterrows():
        existing = conn.execute(
            "SELECT descripcion, rubro, marca, ean, clase, activo FROM articulos WHERE codigo = ?",
            [row["codigo"]]
        ).fetchone()

        nuevo = (
            row.get("descripcion"),
            row.get("rubro") if "rubro" in df.columns else None,
            row.get("marca") if "marca" in df.columns else None,
            row.get("ean") if "ean" in df.columns else None,
            row.get("clase") if "clase" in df.columns else None,
            bool(row["activo"]),
        )

        if existing is None:
            conn.execute("""
                INSERT INTO articulos
                    (codigo, descripcion, rubro, marca, ean, clase, activo, fecha_ingesta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [row["codigo"], *nuevo, now])
            insertados += 1
        else:
            if existing != nuevo:
                conn.execute("""
                    UPDATE articulos
                    SET descripcion = ?, rubro = ?, marca = ?, ean = ?, clase = ?, activo = ?, fecha_ingesta = ?
                    WHERE codigo = ?
                """, [*nuevo, now, row["codigo"]])
                actualizados += 1
            else:
                sin_cambios += 1

    conn.close()
    _print_resumen(insertados, actualizados, sin_cambios, "articulos")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": sin_cambios}
