"""
ingesta/referencias.py — Carga de archivos de referencia

Lógica común: upsert por clave.
  - Si el registro existe y cambió algo → actualizar
  - Si no existe → insertar
  - Si ya no aparece en el archivo → no tocar (conservar histórico)

Funciones públicas:
    cargar_depositos(filepath, proyecto)
    marcar_logistica(proyecto, codigos, es_logistica=True)
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

# Obligatorias (nombres reales del ERP, normalizados a upper)
DEPOSITOS_COLS = {"CD", "NOMBRE DEPOSITO", "ABREVIATURA DEPOSITO"}
DEPOSITOS_RENAME = {
    "CD":                   "codigodepo",
    "NOMBRE DEPOSITO":      "nombre",
    "ABREVIATURA DEPOSITO": "abreviacion",
    "UBICACIÓN":            "direccion",
    "UBICACION":            "direccion",   # por si viene sin tilde
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
    # Asegurar columnas opcionales (ej: direccion si no vino Ubicación)
    for col in ["nombre", "direccion", "abreviacion"]:
        if col not in df.columns:
            df[col] = None
    for col in df.columns:
        df[col] = df[col].str.strip().replace("nan", None)

    now = datetime.now(timezone.utc)
    df["fecha_ingesta"] = now

    conn = _get_connection(proyecto)
    n_antes = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]

    # Preservar es_logistica: se marca manualmente (ver marcar_logistica) y el
    # INSERT OR REPLACE la resetearía a FALSE en cada recarga del archivo.
    existentes = conn.execute("SELECT codigodepo, es_logistica FROM depositos").df()
    df = df.merge(existentes, on="codigodepo", how="left")
    df["es_logistica"] = df["es_logistica"].fillna(False)

    conn.execute("""
        INSERT OR REPLACE INTO depositos
            (codigodepo, nombre, direccion, abreviacion, es_logistica, fecha_ingesta)
        SELECT codigodepo, nombre, direccion, abreviacion, es_logistica, fecha_ingesta
        FROM df
    """)

    n_despues = conn.execute("SELECT COUNT(*) FROM depositos").fetchone()[0]
    conn.close()

    insertados   = n_despues - n_antes
    actualizados = len(df) - insertados
    _print_resumen(insertados, actualizados, 0, "depositos")
    return {"insertados": insertados, "actualizados": actualizados, "sin_cambios": 0}


def marcar_logistica(
    proyecto: str,
    codigos: str | list[str],
    es_logistica: bool = True,
) -> dict:
    """
    Marca (o desmarca) manualmente depósitos como centros de logística.

    El flag `es_logistica` distingue los centros de distribución (CDC, CR2)
    de las sucursales de venta. Se preserva en las recargas de
    `cargar_depositos`. El stock logístico se computa sumando los depósitos
    marcados — nunca se hardcodea un código.

    Args:
        proyecto:     Nombre del proyecto
        codigos:      codigodepo o lista de códigos (ej: "CDC" o ["CDC", "CR2"])
        es_logistica: True para marcar como logística, False para desmarcar

    Returns:
        dict con keys: actualizados, no_encontrados
    """
    if isinstance(codigos, str):
        codigos = [codigos]

    conn = _get_connection(proyecto)
    actualizados, no_encontrados = [], []
    for c in codigos:
        existe = conn.execute(
            "SELECT COUNT(*) FROM depositos WHERE codigodepo = ?", [c]
        ).fetchone()[0]
        if existe:
            conn.execute(
                "UPDATE depositos SET es_logistica = ? WHERE codigodepo = ?",
                [es_logistica, c],
            )
            actualizados.append(c)
        else:
            no_encontrados.append(c)
    conn.close()

    msg = f"  ✓ es_logistica={es_logistica}: {len(actualizados)} depósitos actualizados"
    if no_encontrados:
        msg += f", {len(no_encontrados)} no encontrados: {no_encontrados}"
    print(msg)
    return {"actualizados": actualizados, "no_encontrados": no_encontrados}


# ---------------------------------------------------------------------------
# estructura
# ---------------------------------------------------------------------------

# Jerarquía real del ERP: códigos (CGSR/CSR/CR) + descripciones. Se guarda el
# código de rubro (CR) como clave y las descripciones de cada nivel. Los
# encabezados vienen con ":" y variaciones de tilde — se contemplan variantes.
ESTRUCTURA_RENAME = {
    "CR":                  "rubro_cod",
    "DESCRIPCIÓN RUBRO":   "rubro",
    "DESCRIPCION RUBRO":   "rubro",
    "SUPER RUBRO:":        "super_rubro",
    "SUPER RUBRO":         "super_rubro",
    "GRUPO SUPER RUBRO:":  "gran_super_rubro",
    "GRUPO SUPER RUBRO":   "gran_super_rubro",
}


def cargar_estructura(filepath: str | Path, proyecto: str) -> dict:
    """
    Upsert de estructura de rubros.
    PK: rubro_cod (código de rubro CR). Una fila por rubro.
    Guarda código + descripciones para que el join con articulos funcione
    tanto por código como por nombre.
    """
    filepath = Path(filepath)
    print(f"\n{'='*55}")
    print(f"  Carga de estructura: {filepath.name}")
    print(f"{'='*55}")

    df = _leer_excel(filepath)

    # Seleccionar y renombrar; validar contra los nombres ya normalizados
    df = df[[c for c in ESTRUCTURA_RENAME if c in df.columns]].copy()
    df = df.rename(columns=ESTRUCTURA_RENAME)
    # Si un nivel apareció con dos variantes de nombre, quedarse con una
    df = df.loc[:, ~df.columns.duplicated()]

    faltan = {"rubro_cod", "rubro"} - set(df.columns)
    if faltan:
        raise ValueError(
            "Columnas faltantes en estructura (esperaba 'CR' y 'Descripción Rubro'): "
            f"{sorted(faltan)}"
        )

    for col in ["super_rubro", "gran_super_rubro"]:
        if col not in df.columns:
            df[col] = None

    df = df.dropna(subset=["rubro_cod"])
    for col in ["rubro_cod", "rubro", "super_rubro", "gran_super_rubro"]:
        df[col] = df[col].str.strip().replace("nan", None)
    df = df.drop_duplicates(subset=["rubro_cod"])

    now = datetime.now(timezone.utc)
    df["fecha_ingesta"] = now

    conn = _get_connection(proyecto)
    n_antes = conn.execute("SELECT COUNT(*) FROM estructura").fetchone()[0]

    conn.execute("""
        INSERT OR REPLACE INTO estructura
            (rubro_cod, rubro, super_rubro, gran_super_rubro, fecha_ingesta)
        SELECT rubro_cod, rubro, super_rubro, gran_super_rubro, fecha_ingesta
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
    # El ERP exporta el header como "Activo?" — quitar el signo de pregunta
    df.columns = [c[:-1].strip() if c.endswith("?") else c for c in df.columns]
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