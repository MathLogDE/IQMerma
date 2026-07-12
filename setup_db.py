"""
setup_db.py — Inicialización del schema de MermaIQ en DuckDB

Uso:
    python setup_db.py --project cliente_alfa
    python setup_db.py --project cliente_alfa --reset   # borra y recrea todo

Idempotente: se puede correr múltiples veces sin romper datos existentes.
Cada proyecto tiene su propio archivo .duckdb en projects/<nombre>/data.duckdb

Cambios v2 (rediseño análisis por selección):
  - Se elimina la tabla `conteos`: el inventario físico ahora entra como
    TIPOMOV='INV' dentro de `movimientos`.
  - Se elimina `costo_sku_historial`: la valorización sale del snapshot de
    stock (columnas costo / lista_1), con fallback hacia atrás por fecha.
  - `depositos` gana la columna `es_logistica` para distinguir los centros
    de distribución (CDC, CR2) de las sucursales de venta. El stock logístico
    se computa sumando los depósitos marcados; nunca se hardcodea un código.
"""

import argparse
import duckdb
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
PROJECTS_DIR = ROOT / "projects"


def get_db_path(project_name: str) -> Path:
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir / "data.duckdb"


def get_connection(project_name: str) -> duckdb.DuckDBPyConnection:
    db_path = get_db_path(project_name)
    return duckdb.connect(str(db_path))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """

-- Secuencias para IDs autoincrementales
CREATE SEQUENCE IF NOT EXISTS seq_movimientos START 1;
CREATE SEQUENCE IF NOT EXISTS seq_ventas START 1;
CREATE SEQUENCE IF NOT EXISTS seq_periodos_ingesta START 1;
CREATE SEQUENCE IF NOT EXISTS seq_stock_sucursal START 1;

-- ============================================================
-- MOVIMIENTOS
-- Exportación del ERP. Única fuente de flujos por sucursal y SKU.
-- TIPOMOV:
--   VTA — ventas (informativo; la venta valorizada viene de `ventas`)
--   REM — remitos (RE/RI)
--   AJU — ajustes (incluye subtipo CS)
--   INV — inventario físico (reemplaza a la antigua tabla `conteos`)
-- `diferencia` se recalcula en ingesta como ingreso - egreso
-- (negativo = faltante = merma).
-- ============================================================
CREATE TABLE IF NOT EXISTS movimientos (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_movimientos'),
    fecha            DATE        NOT NULL,
    tipomov          VARCHAR     NOT NULL,   -- VTA, REM, AJU, INV
    tipo             VARCHAR,               -- subtipo interno del ERP (RE, RI, CS, ...)
    numero           VARCHAR     NOT NULL,
    codigodepo       VARCHAR     NOT NULL,
    nombre           VARCHAR,
    codigo           VARCHAR     NOT NULL,  -- SKU
    costo            DECIMAL(18,4),         -- costo del movimiento (fallback de valorización)
    ingreso          DECIMAL(18,4),
    egreso           DECIMAL(18,4),
    diferencia       DECIMAL(18,4),         -- ingreso - egreso, neto con signo
    usuario          VARCHAR,
    tipo_aj          VARCHAR,

    periodo          VARCHAR     NOT NULL,  -- 'YYYY-MM'
    archivo_origen   VARCHAR     NOT NULL,
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_mov_sku_depo_fecha
    ON movimientos (codigo, codigodepo, fecha);

CREATE INDEX IF NOT EXISTS idx_mov_tipomov_fecha
    ON movimientos (tipomov, fecha, codigodepo);


-- ============================================================
-- VENTAS
-- Reporte de ventas mensual por sucursal y SKU. Agregado (no por
-- operación). `venta_neta` es el importe a PRECIO de venta — es la
-- valorización de venta cuando se analiza en modo "lista_1".
-- En modo "costo" la venta se revaloriza como unidades * costo_stock.
-- ============================================================
CREATE TABLE IF NOT EXISTS ventas (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_ventas'),
    codigodepo       VARCHAR     NOT NULL,
    codigo           VARCHAR     NOT NULL,
    descripcion      VARCHAR,
    fecha_desde      DATE        NOT NULL,  -- primer día del mes
    fecha_hasta      DATE        NOT NULL,  -- último día del mes
    unidades         DECIMAL(18,4),
    venta_neta       DECIMAL(18,4),         -- importe neto a precio de venta

    archivo_origen   VARCHAR     NOT NULL,
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_ventas_sku_depo_periodo
    ON ventas (codigo, codigodepo, fecha_desde, fecha_hasta);


-- ============================================================
-- STOCK SUCURSAL
-- Snapshots de stock del ERP, despivoteados a formato relacional
-- (una fila por codigo, codigodepo, fecha_snapshot).
-- Historial completo — nunca se sobrescribe.
-- Fuente primaria de valorización: columnas `costo` y `lista_1`.
-- La vista `stock_actual` apunta siempre al snapshot más reciente.
--
-- NOTA: la columna derivada LOG del archivo del ERP (= CDC + CR2) NO se
-- carga; el stock logístico se computa sumando los depósitos marcados
-- es_logistica en `depositos`.
-- ============================================================
CREATE TABLE IF NOT EXISTS stock_sucursal (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_stock_sucursal'),
    codigo           VARCHAR       NOT NULL,
    codigodepo       VARCHAR       NOT NULL,
    fecha_snapshot   DATE          NOT NULL,
    stock            DECIMAL(18,4),
    lista_1          DECIMAL(18,4),          -- precio de lista (valorización a venta)
    costo            DECIMAL(18,4),          -- costo ERP (valorización a costo)
    uxb              DECIMAL(18,4),

    archivo_origen   VARCHAR       NOT NULL,
    fecha_ingesta    TIMESTAMP     NOT NULL DEFAULT current_timestamp,

    UNIQUE (codigo, codigodepo, fecha_snapshot)
);

CREATE INDEX IF NOT EXISTS idx_stock_sku_depo_fecha
    ON stock_sucursal (codigo, codigodepo, fecha_snapshot);

CREATE INDEX IF NOT EXISTS idx_stock_snapshot
    ON stock_sucursal (fecha_snapshot);


-- ============================================================
-- DEPOSITOS
-- Catálogo de sucursales/depósitos. Upsert por codigodepo.
-- es_logistica = TRUE para centros de distribución (CDC, CR2).
-- ============================================================
CREATE TABLE IF NOT EXISTS depositos (
    codigodepo    VARCHAR PRIMARY KEY,
    nombre        VARCHAR,
    direccion     VARCHAR,
    abreviacion   VARCHAR,
    es_logistica  BOOLEAN DEFAULT FALSE,
    fecha_ingesta TIMESTAMP
);


-- ============================================================
-- ESTRUCTURA
-- Catálogo de rubros con jerarquía. Upsert por código de rubro (CR).
-- Guarda el código y la descripción de cada nivel, para que el join con
-- articulos.rubro funcione tanto si trae el código como el nombre.
-- ============================================================
CREATE TABLE IF NOT EXISTS estructura (
    rubro_cod        VARCHAR PRIMARY KEY,   -- código de rubro (CR del ERP)
    rubro            VARCHAR,               -- descripción de rubro (join por nombre)
    super_rubro      VARCHAR,               -- descripción super rubro
    gran_super_rubro VARCHAR,               -- descripción gran super rubro
    fecha_ingesta    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_estructura_rubro ON estructura (rubro);


-- ============================================================
-- ARTICULOS
-- Catálogo de SKUs. Upsert por codigo — nunca se borra.
-- ============================================================
CREATE TABLE IF NOT EXISTS articulos (
    codigo        VARCHAR PRIMARY KEY,
    descripcion   VARCHAR,
    rubro         VARCHAR,               -- FK -> estructura.rubro
    marca         VARCHAR,
    ean           VARCHAR,
    clase         VARCHAR,               -- A, B, C (Pareto)
    activo        BOOLEAN,
    fecha_ingesta TIMESTAMP
);


-- ============================================================
-- TIPOS CATEGORIA
-- Mapeo configurable: subtipo del ERP (`tipo`) -> categoría de
-- negocio. Define el pivoteo del análisis de merma y qué
-- categorías suman al numerador. Editable sin tocar código.
--   es_merma = TRUE  -> la parte negativa suma a la merma total
--   es_merma = FALSE -> categoría informativa (ej: Ventas)
-- ============================================================
CREATE TABLE IF NOT EXISTS tipos_categoria (
    tipo          VARCHAR PRIMARY KEY,   -- subtipo del ERP: FA, INV, RI, MD, CS, ...
    tipomov       VARCHAR,               -- tipomov asociado (informativo)
    categoria     VARCHAR     NOT NULL,  -- Ventas, Inventario, Remitido, Dif. de camión, ...
    es_merma      BOOLEAN     DEFAULT TRUE,
    orden         INTEGER,               -- orden de despliegue de columnas
    fecha_ingesta TIMESTAMP
);


-- ============================================================
-- PERIODOS INGESTA
-- Registro de qué archivos/períodos fueron procesados.
-- ============================================================
CREATE TABLE IF NOT EXISTS periodos_ingesta (
    id                 INTEGER PRIMARY KEY DEFAULT nextval('seq_periodos_ingesta'),
    tabla              VARCHAR     NOT NULL,
    periodo            VARCHAR     NOT NULL,
    codigodepo         VARCHAR,
    archivo_origen     VARCHAR     NOT NULL,
    fecha_ingesta      TIMESTAMP   NOT NULL DEFAULT current_timestamp,
    estado             VARCHAR     NOT NULL DEFAULT 'ok',  -- 'ok', 'reemplazado'
    registros_cargados INTEGER
);


-- ============================================================
-- VISTA: stock_actual
-- Snapshot más reciente por (codigo, codigodepo).
-- ============================================================
CREATE OR REPLACE VIEW stock_actual AS
SELECT s.*
FROM stock_sucursal s
INNER JOIN (
    SELECT codigo, codigodepo, MAX(fecha_snapshot) AS ultima_fecha
    FROM stock_sucursal
    GROUP BY codigo, codigodepo
) ult
    ON  s.codigo         = ult.codigo
    AND s.codigodepo     = ult.codigodepo
    AND s.fecha_snapshot = ult.ultima_fecha;

"""


# ---------------------------------------------------------------------------
# Seed: catálogo por defecto de tipos -> categoría
# ---------------------------------------------------------------------------
# Catálogo del ERP. El cliente puede editar/extender esta tabla sin tocar
# código. Merma (es_merma=TRUE) = pérdida no explicada: Inventario, Dif. de
# camión y Ajustes. Ventas, Compras y Remitos son flujos legítimos (no merma).
DEFAULT_TIPOS_CATEGORIA = [
    # (tipo,  tipomov, categoria,         es_merma, orden)
    ("FA",   "VTA", "Ventas",         False, 1),  # Factura A
    ("FB",   "VTA", "Ventas",         False, 1),  # Factura B
    ("NCA",  "VTA", "Ventas",         False, 1),  # Nota crédito A
    ("NCB",  "VTA", "Ventas",         False, 1),  # Nota crédito B
    ("FCA",  "VTA", "Ventas",         False, 1),  # Factura compra A
    ("NCCA", "VTA", "Ventas",         False, 1),  # Nota crédito compra A
    ("INV",  "INV", "Inventario",     True,  2),  # Inventario físico
    ("MD",   "REM", "Dif. de camión", True,  3),  # Movimiento directo
    ("CS",   "AJU", "Ajustes",        True,  4),  # Control de stock
    ("RE",   "REM", "Remitido",       False, 5),  # Remito externo (recepción)
    ("RI",   "REM", "Remitido",       False, 5),  # Remito interno (transferencia)
    ("RDC",  "REM", "Remitido",       False, 5),  # Remito devolución compra
]


def _sembrar_tipos_categoria(conn: duckdb.DuckDBPyConnection) -> None:
    """Inserta el catálogo por defecto solo si la tabla está vacía (idempotente)."""
    n = conn.execute("SELECT COUNT(*) FROM tipos_categoria").fetchone()[0]
    if n > 0:
        return
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    conn.executemany(
        "INSERT INTO tipos_categoria "
        "(tipo, tipomov, categoria, es_merma, orden, fecha_ingesta) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(t, tm, cat, em, o, now) for (t, tm, cat, em, o) in DEFAULT_TIPOS_CATEGORIA],
    )
    print(f"  Catálogo tipos_categoria sembrado ({len(DEFAULT_TIPOS_CATEGORIA)} subtipos por defecto)")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(project_name: str, reset: bool = False) -> None:
    db_path = get_db_path(project_name)
    conn = duckdb.connect(str(db_path))

    if reset:
        print(f"  [RESET] Eliminando objetos existentes en '{project_name}'...")
        conn.execute("DROP VIEW IF EXISTS stock_actual")
        tables = [
            "movimientos", "ventas", "stock_sucursal",
            "periodos_ingesta", "depositos", "estructura", "articulos",
            "tipos_categoria",
            # legacy v1 — se eliminan si existen
            "conteos", "costo_sku_historial",
        ]
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        sequences = [
            "seq_movimientos", "seq_ventas", "seq_periodos_ingesta",
            "seq_stock_sucursal",
            # legacy v1
            "seq_conteos", "seq_costo_sku",
        ]
        for seq in sequences:
            conn.execute(f"DROP SEQUENCE IF EXISTS {seq}")

    print(f"  Creando schema en: {db_path}")
    conn.execute(SCHEMA_SQL)
    _sembrar_tipos_categoria(conn)

    tablas = [
        r[0] for r in conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """).fetchall()
    ]
    vistas = [
        r[0] for r in conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'VIEW'
            ORDER BY table_name
        """).fetchall()
    ]

    print(f"  Tablas ({len(tablas)}): {', '.join(tablas)}")
    print(f"  Vistas ({len(vistas)}): {', '.join(vistas)}")
    conn.close()
    print(f"  Setup completo para proyecto '{project_name}'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inicializa el schema de MermaIQ para un proyecto."
    )
    parser.add_argument(
        "--project", required=True,
        help="Nombre del proyecto/cliente (ej: cliente_alfa)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Elimina y recrea todas las tablas (borra datos)"
    )
    args = parser.parse_args()

    if args.reset:
        confirm = input(
            f"  ADVERTENCIA: --reset borrará todos los datos de '{args.project}'. "
            f"Confirmás? (s/N): "
        )
        if confirm.lower() != "s":
            print("  Cancelado.")
            exit(0)

    setup(args.project, reset=args.reset)
