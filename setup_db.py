"""
setup_db.py — Inicialización del schema de MermaIQ en DuckDB

Uso:
    python setup_db.py --project cliente_alfa
    python setup_db.py --project cliente_alfa --reset   # borra y recrea todo

Idempotente: se puede correr múltiples veces sin romper datos existentes.
Cada proyecto tiene su propio archivo .duckdb en projects/<nombre>/data.duckdb
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
CREATE SEQUENCE IF NOT EXISTS seq_conteos START 1;
CREATE SEQUENCE IF NOT EXISTS seq_costo_sku START 1;
CREATE SEQUENCE IF NOT EXISTS seq_periodos_ingesta START 1;

-- ============================================================
-- MOVIMIENTOS
-- Exportación del ERP. Cubre VTA (ventas), AJU (ajustes) y
-- REM (remitos: RE/RI). Es la fuente de movimientos diarios
-- por sucursal y SKU.
-- ============================================================
CREATE TABLE IF NOT EXISTS movimientos (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_movimientos'),
    fecha            DATE        NOT NULL,
    tipomov          VARCHAR     NOT NULL,   -- VTA, AJU, REM
    tipo             VARCHAR,               -- subtipo interno del ERP
    numero           VARCHAR     NOT NULL,  -- número de comprobante
    codigodepo       VARCHAR     NOT NULL,  -- código de sucursal
    nombre           VARCHAR,               -- nombre del cliente/proveedor (del ERP)
    codigo           VARCHAR     NOT NULL,  -- SKU
    costo            DECIMAL(18,4),         -- costo unitario (formato AR: coma decimal)
    ingreso          DECIMAL(18,4),
    egreso           DECIMAL(18,4),
    diferencia       DECIMAL(18,4),
    usuario          VARCHAR,               -- campo USER del ERP
    tipo_aj          VARCHAR,               -- campo TIPO AJ del ERP

    -- Campos de auditoría generados en ingesta
    periodo          VARCHAR     NOT NULL,  -- 'YYYY-MM' o 'YYYY-QQ' según granularidad
    archivo_origen   VARCHAR     NOT NULL,  -- nombre del archivo fuente
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

-- Índices para los joins más frecuentes
CREATE INDEX IF NOT EXISTS idx_mov_sku_depo_fecha
    ON movimientos (codigo, codigodepo, fecha);

CREATE INDEX IF NOT EXISTS idx_mov_periodo
    ON movimientos (periodo, codigodepo);


-- ============================================================
-- VENTAS
-- Reporte de ventas por período y sucursal. Granularidad:
-- quincena o mes. No hay fila por operación — es un agregado.
-- Cod.Dep. mapea directamente a codigodepo de movimientos.
-- ============================================================
CREATE TABLE IF NOT EXISTS ventas (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_ventas'),
    codigodepo       VARCHAR     NOT NULL,  -- sucursal
    codigo           VARCHAR     NOT NULL,  -- SKU
    descripcion      VARCHAR,
    fecha_desde      DATE        NOT NULL,  -- inicio del período
    fecha_hasta      DATE        NOT NULL,  -- fin del período
    unidades         DECIMAL(18,4),         -- unidades vendidas en el período
    venta_neta       DECIMAL(18,4),         -- importe neto (con descuentos aplicados)

    -- Auditoría
    archivo_origen   VARCHAR     NOT NULL,
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_ventas_sku_depo_periodo
    ON ventas (codigo, codigodepo, fecha_desde, fecha_hasta);


-- ============================================================
-- CONTEOS
-- Planilla de inventario físico exportada del ERP.
-- Stock Sistema vs Total Contado → diferencia = merma en unidades.
-- El costo unitario aquí es la fuente primaria de valorización
-- al momento del conteo.
-- ============================================================
CREATE TABLE IF NOT EXISTS conteos (
    id                INTEGER PRIMARY KEY DEFAULT nextval('seq_conteos'),
    codigodepo        VARCHAR     NOT NULL,
    fecha_conteo      DATE        NOT NULL,
    numero_inventario VARCHAR,              -- ID del inventario en el ERP
    codigo            VARCHAR     NOT NULL, -- SKU
    descripcion       VARCHAR,
    stock_sistema     DECIMAL(18,4),
    stock_real        DECIMAL(18,4),        -- total contado
    diferencia        DECIMAL(18,4),        -- recalculada en ingesta: stock_real - stock_sistema
    costo_unitario    DECIMAL(18,4),        -- costo del ERP al momento del conteo

    -- Auditoría
    archivo_origen    VARCHAR     NOT NULL,
    fecha_ingesta     TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_conteos_sku_depo_fecha
    ON conteos (codigo, codigodepo, fecha_conteo);


-- ============================================================
-- COSTO SKU HISTORIAL
-- Calendario de costos por SKU. Se auto-puebla durante la
-- ingesta de movimientos a partir de remitos (RE/RI) con
-- costo > 0. Es la fuente complementaria de costo para
-- análisis inter-conteo.
-- ============================================================
CREATE TABLE IF NOT EXISTS costo_sku_historial (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_costo_sku'),
    codigo           VARCHAR     NOT NULL,  -- SKU
    codigodepo       VARCHAR     NOT NULL,  -- sucursal del remito
    fecha            DATE        NOT NULL,  -- fecha del remito
    costo            DECIMAL(18,4) NOT NULL,
    numero_remito    VARCHAR,               -- trazabilidad al movimiento origen
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_costo_sku_fecha
    ON costo_sku_historial (codigo, codigodepo, fecha);


-- ============================================================
-- DEPOSITOS
-- Catálogo de sucursales/depósitos. Upsert por codigodepo.
-- ============================================================
CREATE TABLE IF NOT EXISTS depositos (
    codigodepo   VARCHAR PRIMARY KEY,
    nombre       VARCHAR,
    direccion    VARCHAR,
    abreviacion  VARCHAR,
    fecha_ingesta TIMESTAMP
);


-- ============================================================
-- ESTRUCTURA
-- Catálogo de rubros con jerarquía. Upsert por rubro.
-- Una fila por Rubro — no por SKU.
-- Join: articulos.rubro → estructura.rubro → jerarquía completa.
-- ============================================================
CREATE TABLE IF NOT EXISTS estructura (
    rubro            VARCHAR PRIMARY KEY,
    super_rubro      VARCHAR,
    gran_super_rubro VARCHAR,
    fecha_ingesta    TIMESTAMP
);


-- ============================================================
-- ARTICULOS
-- Catálogo de SKUs. Upsert por codigo — nunca se borra.
-- rubro es FK hacia estructura.rubro.
-- EAN: código de barras. Clase: A/B/C para análisis Pareto
-- (en desuso actualmente, se instrumentará próximamente).
-- ============================================================
CREATE TABLE IF NOT EXISTS articulos (
    codigo        VARCHAR PRIMARY KEY,
    descripcion   VARCHAR,
    rubro         VARCHAR,               -- FK → estructura.rubro
    marca         VARCHAR,
    ean           VARCHAR,               -- código de barras
    clase         VARCHAR,               -- A, B, C (Pareto)
    activo        BOOLEAN,
    fecha_ingesta TIMESTAMP
);


-- ============================================================
-- PERIODOS INGESTA
-- Registro de qué archivos/períodos fueron procesados.
-- Permite detectar duplicados y ejecutar reemplazos explícitos.
-- ============================================================
CREATE TABLE IF NOT EXISTS periodos_ingesta (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_periodos_ingesta'),
    tabla            VARCHAR     NOT NULL,  -- 'movimientos', 'ventas', 'conteos'
    periodo          VARCHAR     NOT NULL,  -- identificador del período
    codigodepo       VARCHAR,               -- NULL = aplica a todos
    archivo_origen   VARCHAR     NOT NULL,
    fecha_ingesta    TIMESTAMP   NOT NULL DEFAULT current_timestamp,
    estado           VARCHAR     NOT NULL DEFAULT 'ok',  -- 'ok', 'reemplazado'
    registros_cargados INTEGER
);

"""


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(project_name: str, reset: bool = False) -> None:
    db_path = get_db_path(project_name)
    conn = duckdb.connect(str(db_path))

    if reset:
        print(f"  [RESET] Eliminando tablas existentes en '{project_name}'...")
        tables = [
            "movimientos", "ventas", "conteos", "costo_sku_historial",
            "periodos_ingesta", "depositos", "estructura", "articulos"
        ]
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    print(f"  Creando schema en: {db_path}")
    conn.execute(SCHEMA_SQL)

    # Verificación
    result = conn.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
    """).fetchall()

    tablas = [r[0] for r in result]
    print(f"  Tablas creadas: {', '.join(tablas)}")
    conn.close()
    print(f"  Setup completo para proyecto '{project_name}'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializa el schema de MermaIQ para un proyecto.")
    parser.add_argument("--project", required=True, help="Nombre del proyecto/cliente (ej: cliente_alfa)")
    parser.add_argument("--reset", action="store_true", help="Elimina y recrea todas las tablas (borra datos)")
    args = parser.parse_args()

    if args.reset:
        confirm = input(f"  ADVERTENCIA: --reset borrará todos los datos de '{args.project}'. Confirmás? (s/N): ")
        if confirm.lower() != "s":
            print("  Cancelado.")
            exit(0)

    setup(args.project, reset=args.reset)
