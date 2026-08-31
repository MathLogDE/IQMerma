"""
ingesta/movimientos.py — Carga de planillas de movimientos del ERP

Responsabilidades:
  1. Leer el Excel del ERP (tolerando variaciones de formato)
  2. Normalizar y validar los datos
  3. Detectar el período cubierto por el archivo
  4. Verificar duplicados contra periodos_ingesta
  5. Insertar en DuckDB (movimientos)
  6. Registrar la ingesta en periodos_ingesta

Uso desde Python:
    from ingesta.movimientos import ingestar_movimientos, ingestar_libro_movimientos

    # Un archivo = un período (una pestaña):
    ingestar_movimientos("archivo.xlsx", proyecto="cliente_alfa")

    # Un archivo con varias pestañas (una por mes) — una ingesta por hoja:
    ingestar_libro_movimientos("2025.xlsx", proyecto="cliente_alfa")
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


def _leer_excel(source, sheet=0) -> pd.DataFrame:
    """
    Lee el Excel del ERP y retorna un DataFrame crudo.

    Args:
        source: ruta al archivo, o un pd.ExcelFile ya abierto (para carga
                multi-pestaña eficiente — no reabre el libro por cada hoja).
        sheet:  índice (0 por defecto) o nombre de la pestaña a leer.

    Maneja:
      - Archivos con filas vacías al inicio
      - COSTO con coma decimal (formato argentino)
      - Columnas extra que el ERP pueda agregar en el futuro
    """
    try:
        if isinstance(source, pd.ExcelFile):
            df = pd.read_excel(source, dtype=str, sheet_name=sheet)
        else:
            filepath = Path(source)
            if not filepath.exists():
                raise FileNotFoundError(f"Archivo no encontrado: {filepath}")
            df = pd.read_excel(filepath, dtype=str, engine="openpyxl", sheet_name=sheet)
    except FileNotFoundError:
        raise
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


def _periodo_de_hoja(sheet) -> tuple[int, int] | None:
    """
    Deduce (año, mes) del nombre de la pestaña: '11-25', '11-2025',
    '2025-11', '01_26', etc. Retorna None si no matchea.
    """
    import re
    if not isinstance(sheet, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,4})[-/_](\d{1,4})\s*", sheet)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if 1 <= a <= 12:            # MM-YY / MM-YYYY
        mes, anio = a, b
    elif 1 <= b <= 12:          # YYYY-MM / YY-MM
        mes, anio = b, a
    else:
        return None
    if anio < 100:
        anio += 2000
    if not (2000 <= anio <= 2100):
        return None
    return anio, mes


def _parsear_fechas(serie: pd.Series, esperado: tuple[int, int] | None):
    """
    Parsea FECHA tolerando formatos mixtos y corrigiendo swaps día/mes.

    El caso real que motiva esto: un Excel "trabajado" en locale US guarda
    '3/11/2025' (3-nov) como datetime 11-mar (swap), mientras los días >12
    quedan como texto DD/MM. Un parseo ingenuo desparrama el mes por todo el
    año y descarta filas.

    Estrategia por celda, en orden de prioridad:
      1. ISO (celdas datetime nativas: 'YYYY-MM-DD ...')
      2. texto DD/MM/YYYY
      3. texto MM/DD/YYYY
      4. swap del ISO (día<->mes) — solo si cae en el período esperado
    Si hay período esperado (nombre de la pestaña o mes modal de las fechas
    inequívocas, día>12), toda celda con un candidato dentro del período lo
    usa; el swap solo se acepta con período esperado.

    Returns:
        (fechas: Series[datetime], corregidas: int) — corregidas = celdas
        donde se aplicó el des-swap.
    """
    s = serie.astype("string").str.strip()

    iso  = pd.to_datetime(s, format="ISO8601", errors="coerce")
    ddmm = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    mmdd = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")

    # candidato "des-swapeado" del ISO (11-mar → 3-nov)
    iso_swap = pd.to_datetime(
        pd.DataFrame({
            "year": iso.dt.year, "month": iso.dt.day, "day": iso.dt.month,
        }),
        errors="coerce",
    )

    fechas = iso.fillna(ddmm).fillna(mmdd)

    # período esperado: pestaña, o mes modal de las fechas inequívocas (día>12)
    if esperado is None:
        inequivocas = fechas[fechas.dt.day > 12]
        if len(inequivocas) >= 10:
            modal = inequivocas.dt.to_period("M").mode()
            if len(modal):
                esperado = (modal[0].year, modal[0].month)

    corregidas = 0
    if esperado is not None:
        anio, mes = esperado

        def en_periodo(cand):
            return cand.notna() & (cand.dt.year == anio) & (cand.dt.month == mes)

        eleccion = fechas.copy()
        ok = en_periodo(eleccion)
        for cand, es_swap in ((iso, False), (ddmm, False), (mmdd, False), (iso_swap, True)):
            mejora = ~ok & en_periodo(cand)
            if mejora.any():
                eleccion[mejora] = cand[mejora]
                if es_swap:
                    corregidas += int(mejora.sum())
                ok |= mejora
        fechas = eleccion

    return fechas, corregidas


def _normalizar(df: pd.DataFrame,
                periodo_esperado: tuple[int, int] | None = None) -> pd.DataFrame:
    """
    Transforma el DataFrame crudo en el formato interno de MermaIQ:
      - Renombra columnas
      - Parsea fechas (formatos mixtos, con corrección de swap día/mes)
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

    # Parsear fecha — formatos mixtos + corrección de swap día/mes
    df["fecha"], corregidas = _parsear_fechas(df["fecha"], periodo_esperado)
    if corregidas > 0:
        print(f"  [warn] {corregidas} fechas corregidas por swap día/mes "
              f"(datetimes de Excel en locale US)")

    # Eliminar filas sin fecha (encabezados repetidos, filas vacías, etc.)
    filas_antes = len(df)
    df = df.dropna(subset=["fecha"])
    filas_descartadas = filas_antes - len(df)
    if filas_descartadas > 0:
        print(f"  [warn] {filas_descartadas} filas descartadas (sin fecha válida)")

    # Sanidad: las filas deberían concentrarse en un solo mes
    if len(df):
        meses = df["fecha"].dt.to_period("M")
        share_modal = (meses == meses.mode()[0]).mean()
        if share_modal < 0.95:
            print(f"  [ALERTA] Solo {share_modal:.0%} de las filas caen en el mes "
                  f"principal ({meses.mode()[0]}) — revisar formato de fechas del archivo.")

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

    # Bug del ERP: los movimientos MD (Dif. de camión) exportan el egreso en
    # negativo. Como diferencia = ingreso - egreso, un egreso negativo se
    # resta como si fuera un ingreso — la pata de origen del remito MD queda
    # con signo invertido (ver core/distribucion/diferencias.py, que ya
    # esquivaba esto calculando aparte). Se corrige acá para que quede bien
    # en cualquier cálculo que use `diferencia` o `egreso`, no solo ahí.
    if "tipo" in df.columns and "egreso" in df.columns:
        es_md_negativo = (df["tipo"] == "MD") & (df["egreso"] < 0)
        if es_md_negativo.any():
            df.loc[es_md_negativo, "egreso"] = df.loc[es_md_negativo, "egreso"].abs()
            print(f"  [warn] {es_md_negativo.sum()} egresos MD negativos corregidos "
                  f"(bug de exportación del ERP, no del archivo)")

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
    sheet=0,
    libro: "pd.ExcelFile | None" = None,
) -> dict:
    """
    Carga una planilla de movimientos del ERP en DuckDB.

    Args:
        filepath:  Ruta al archivo .xlsx del ERP
        proyecto:  Nombre del proyecto (carpeta bajo projects/)
        forzar:    Si True, reemplaza el período aunque ya exista
        sheet:     Pestaña a leer (índice o nombre). Default: la primera.
        libro:     pd.ExcelFile ya abierto (uso interno de
                   ingestar_libro_movimientos; evita reabrir por hoja).

    Returns:
        dict con keys: periodo, registros, sucursales, tipomov, advertencias
    """
    filepath = Path(filepath)
    # archivo_origen incluye la pestaña cuando no es la primera (para auditoría)
    archivo_origen = filepath.name if sheet in (0, None) else f"{filepath.name}#{sheet}"
    advertencias = []

    print(f"\n{'='*55}")
    print(f"  Ingesta de movimientos: {archivo_origen}")
    print(f"  Proyecto: {proyecto}")
    print(f"{'='*55}")

    # 1. Leer
    print("  Leyendo archivo...")
    df_crudo = _leer_excel(libro if libro is not None else filepath, sheet=sheet)
    print(f"  Filas leídas: {len(df_crudo)}")

    # 2. Validar columnas
    faltantes = _validar_columnas(df_crudo)
    if faltantes:
        raise ValueError(f"Columnas obligatorias faltantes: {faltantes}")

    # 3. Normalizar (el nombre de la pestaña define el período esperado
    #    para validar/corregir las fechas)
    print("  Normalizando datos...")
    periodo_esperado = _periodo_de_hoja(sheet)
    if periodo_esperado:
        print(f"  Período esperado (pestaña): {periodo_esperado[0]}-{periodo_esperado[1]:02d}")
    df = _normalizar(df_crudo, periodo_esperado)
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

    # 8. Registrar ingesta
    _registrar_ingesta(conn, periodo, archivo_origen, registros)
    conn.close()

    print(f"\n  Ingesta completada: {registros} movimientos, período {periodo}")
    print(f"{'='*55}\n")

    return {
        "periodo": periodo,
        "registros": registros,
        "sucursales": sucursales,
        "tipomov": tipomov_counts,
        "advertencias": advertencias,
    }


def ingestar_libro_movimientos(
    filepath: str | Path,
    proyecto: str,
    forzar: bool = False,
) -> dict:
    """
    Carga un Excel de movimientos con VARIAS pestañas (una por período/mes),
    haciendo una ingesta independiente por cada hoja.

    Cada pestaña se detecta y deduplica por su propio período — respeta el
    modelo mensual. El libro se abre una sola vez (eficiente para archivos
    pesados). Una pestaña que falle (ej: período ya cargado sin forzar, o una
    hoja que no es de datos) no corta la carga de las demás: se reporta.

    Args:
        filepath:  Ruta al .xlsx con múltiples pestañas
        proyecto:  Nombre del proyecto
        forzar:    Si True, reemplaza los períodos que ya existan

    Returns:
        dict con keys: archivo, hojas, cargadas, registros_total, detalle, errores
    """
    filepath = Path(filepath)
    archivo_origen = filepath.name

    print(f"\n{'#'*55}")
    print(f"  Ingesta de LIBRO de movimientos: {archivo_origen}")
    print(f"  Proyecto: {proyecto}")
    print(f"{'#'*55}")

    libro = pd.ExcelFile(filepath, engine="openpyxl")
    hojas = list(libro.sheet_names)
    print(f"  Pestañas encontradas ({len(hojas)}): {hojas}")

    resultados, errores = [], []
    try:
        for hoja in hojas:
            try:
                r = ingestar_movimientos(
                    filepath, proyecto, forzar=forzar, sheet=hoja, libro=libro
                )
                resultados.append({"hoja": hoja, **r})
            except Exception as e:
                print(f"  [error] pestaña '{hoja}': {e}")
                errores.append({"hoja": hoja, "error": str(e)})
    finally:
        libro.close()

    total = sum(r["registros"] for r in resultados)
    print(f"\n{'#'*55}")
    print(f"  Libro completo: {len(resultados)}/{len(hojas)} pestañas OK — "
          f"{total} movimientos")
    if errores:
        print(f"  Pestañas con error: {[e['hoja'] for e in errores]}")
    print(f"{'#'*55}\n")

    return {
        "archivo": archivo_origen,
        "hojas": len(hojas),
        "cargadas": len(resultados),
        "registros_total": total,
        "detalle": resultados,
        "errores": errores,
    }
