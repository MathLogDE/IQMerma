"""
ingesta/ventas.py — Carga de planillas de ventas

Lógica: acumulativo por período (Fecha Desde / Fecha Hasta).
Cada carga agrega el período nuevo. Bloquea duplicados.
forzar=True para reemplazar un período ya cargado.

Uso:
    from ingesta.ventas import ingestar_ventas
    resultado = ingestar_ventas("ventas_semana.xlsx", proyecto="cliente_alfa")
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Columnas esperadas
# ---------------------------------------------------------------------------

COLUMNAS_OBLIGATORIAS = {"FECHA DESDE", "FECHA HASTA", "CÓDIGO", "COD. DEP.", "TOTAL VTA.", "TOTAL UNID."}

RENAME_MAP = {
    "FECHA DESDE":  "fecha_desde",
    "FECHA HASTA":  "fecha_hasta",
    "CÓDIGO":       "codigo",
    "COD. DEP.":    "codigodepo",
    "TOTAL VTA.":   "venta_neta",
    "TOTAL UNID.":  "unidades",
}


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


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    columnas_presentes = [c for c in RENAME_MAP if c in df.columns]
    df = df[columnas_presentes].copy()
    df = df.rename(columns=RENAME_MAP)

    # Parsear fechas DD/MM/YYYY
    for col_fecha in ["fecha_desde", "fecha_hasta"]:
        df[col_fecha] = pd.to_datetime(df[col_fecha], dayfirst=True, errors="coerce")

    filas_antes = len(df)
    df = df.dropna(subset=["fecha_desde", "fecha_hasta"])
    descartadas = filas_antes - len(df)
    if descartadas > 0:
        print(f"  [info] {descartadas} filas descartadas (sin fechas válidas)")

    # Coma decimal argentina → float
    for col in ["venta_neta", "unidades"]:
        df[col] = (
            df[col]
            .str.replace(",", ".", regex=False)
            .str.replace(" ", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0)
        )

    # Limpiar strings
    for col in ["codigo", "codigodepo"]:
        df[col] = df[col].str.strip().replace("nan", None)

    return df


def _detectar_periodo(df: pd.DataFrame) -> str:
    """
    El período es la combinación única de fecha_desde / fecha_hasta.
    Si el archivo tiene múltiples rangos (no debería), usa el más frecuente.
    """
    combos = df.groupby(["fecha_desde", "fecha_hasta"]).size().reset_index(name="n")
    combos = combos.sort_values("n", ascending=False)

    if len(combos) > 1:
        print(f"  [warn] Múltiples rangos de período en el archivo:")
        for _, row in combos.iterrows():
            print(f"    {row['fecha_desde'].date()} → {row['fecha_hasta'].date()} ({row['n']} filas)")

    fecha_desde = combos.iloc[0]["fecha_desde"]
    fecha_hasta = combos.iloc[0]["fecha_hasta"]

    # Período como string para periodos_ingesta: "2026-03-01/2026-03-15"
    periodo = f"{fecha_desde.date()}/{fecha_hasta.date()}"
    return periodo, fecha_desde, fecha_hasta


def _verificar_duplicado(conn: duckdb.DuckDBPyConnection, periodo: str) -> bool:
    result = conn.execute("""
        SELECT COUNT(*) FROM periodos_ingesta
        WHERE tabla = 'ventas'
        AND periodo = ?
        AND estado = 'ok'
    """, [periodo]).fetchone()
    return result[0] > 0


def _insertar_ventas(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    archivo_origen: str,
) -> int:
    now = datetime.now(timezone.utc)
    df = df.copy()
    df["archivo_origen"] = archivo_origen
    df["fecha_ingesta"] = now

    conn.execute("""
        INSERT INTO ventas
            (codigodepo, codigo, fecha_desde, fecha_hasta,
             unidades, venta_neta, archivo_origen, fecha_ingesta)
        SELECT codigodepo, codigo, fecha_desde, fecha_hasta,
               unidades, venta_neta, archivo_origen, fecha_ingesta
        FROM df
    """)
    return len(df)


def _registrar_ingesta(
    conn: duckdb.DuckDBPyConnection,
    periodo: str,
    archivo_origen: str,
    registros: int,
) -> None:
    conn.execute("""
        INSERT INTO periodos_ingesta
            (tabla, periodo, archivo_origen, fecha_ingesta, estado, registros_cargados)
        VALUES ('ventas', ?, ?, ?, 'ok', ?)
    """, [periodo, archivo_origen, datetime.now(timezone.utc), registros])


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def ingestar_ventas(
    filepath: str | Path,
    proyecto: str,
    forzar: bool = False,
) -> dict:
    filepath = Path(filepath)
    archivo_origen = filepath.name
    advertencias = []

    print(f"\n{'='*55}")
    print(f"  Ingesta de ventas: {archivo_origen}")
    print(f"  Proyecto: {proyecto}")
    print(f"{'='*55}")

    print("  Leyendo archivo...")
    df_crudo = _leer_excel(filepath)
    print(f"  Filas leídas: {len(df_crudo)}")

    faltantes = _validar_columnas(df_crudo)
    if faltantes:
        raise ValueError(f"Columnas obligatorias faltantes: {faltantes}")

    print("  Normalizando datos...")
    df = _normalizar(df_crudo)
    print(f"  Filas válidas: {len(df)}")

    periodo, fecha_desde, fecha_hasta = _detectar_periodo(df)
    sucursales = sorted(df["codigodepo"].dropna().unique().tolist())

    print(f"\n  Período: {fecha_desde.date()} → {fecha_hasta.date()}")
    print(f"  Sucursales: {sucursales}")
    print(f"  SKUs únicos: {df['codigo'].nunique()}")
    print(f"  Venta total: {df['venta_neta'].sum():,.2f}")

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
                DELETE FROM ventas
                WHERE fecha_desde = ? AND fecha_hasta = ?
            """, [fecha_desde, fecha_hasta])
            conn.execute("""
                UPDATE periodos_ingesta SET estado = 'reemplazado'
                WHERE tabla = 'ventas' AND periodo = ?
            """, [periodo])

    print("\n  Insertando ventas...")
    registros = _insertar_ventas(conn, df, archivo_origen)
    print(f"  ✓ {registros} registros insertados")

    _registrar_ingesta(conn, periodo, archivo_origen, registros)
    conn.close()

    print(f"\n  Ingesta completada: {registros} ventas, período {periodo}")
    print(f"{'='*55}\n")

    return {
        "periodo": periodo,
        "registros": registros,
        "sucursales": sucursales,
        "skus": int(df["codigo"].nunique()),
        "venta_total": float(df["venta_neta"].sum()),
        "advertencias": advertencias,
    }
