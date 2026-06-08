"""
core/merma.py — Motor de cálculo de merma

Tres componentes de merma calculados por separado:

  - merma_inv:  diferencia del conteo físico (stock_sistema vs stock_real)
  - merma_rem:  suma de remitos del período (todos los subtipos: RI, RE, RDC, MD, etc.)
  - merma_cs:   suma de ajustes CS del período

Cada componente se valoriza al costo del movimiento/conteo.
Fallback de costo: movimiento/conteo → costo_sku_historial → None

Dos modos de ventana temporal:
  - conteo_anterior: desde el conteo anterior de ese SKU (fallback: 1 año)
  - periodo_fijo:    N días hacia atrás desde la fecha del conteo

Retorna DataFrame a nivel SKU. La agregación se hace en capas superiores.

Uso:
    from core.merma import calcular_merma

    df = calcular_merma(
        proyecto="cliente_alfa",
        codigodepo="002",
        fecha_conteo="2026-03-31",
        modo="conteo_anterior",   # o "periodo_fijo"
        dias=90,                  # solo para modo periodo_fijo
    )
"""

import duckdb
import pandas as pd
from pathlib import Path
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

FALLBACK_DIAS = 365


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


def _obtener_conteo(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_conteo: str,
) -> pd.DataFrame:
    df = conn.execute("""
        SELECT codigo, descripcion, stock_sistema, stock_real,
               diferencia, costo_unitario
        FROM conteos
        WHERE codigodepo = ? AND fecha_conteo = ?
    """, [codigodepo, fecha_conteo]).df()

    if df.empty:
        raise ValueError(
            f"No existe conteo para sucursal '{codigodepo}' en fecha '{fecha_conteo}'. "
            f"Ingresá primero el conteo con ingestar_conteo()."
        )
    return df


def _obtener_fecha_conteo_anterior(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_conteo: str,
    codigo: str,
) -> date | None:
    result = conn.execute("""
        SELECT MAX(fecha_conteo)
        FROM conteos
        WHERE codigodepo = ? AND codigo = ? AND fecha_conteo < ?
    """, [codigodepo, codigo, fecha_conteo]).fetchone()
    return result[0] if result and result[0] else None


def _obtener_costo_historial(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    codigo: str,
    fecha_hasta: str,
) -> float | None:
    """Costo más reciente del SKU en remitos, anterior o igual a fecha_hasta."""
    result = conn.execute("""
        SELECT costo
        FROM costo_sku_historial
        WHERE codigodepo = ? AND codigo = ? AND fecha <= ?
        ORDER BY fecha DESC
        LIMIT 1
    """, [codigodepo, codigo, fecha_hasta]).fetchone()
    return float(result[0]) if result else None


def _resolver_costo(
    costo_directo,
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    codigo: str,
    fecha_hasta: str,
) -> tuple[float | None, str]:
    """
    Resuelve el costo a usar y su fuente.
    Retorna (costo, fuente) donde fuente es:
      'directo', 'historial_remitos', o 'sin_costo'
    """
    if costo_directo and not pd.isna(costo_directo) and float(costo_directo) > 0:
        return float(costo_directo), "directo"

    costo_hist = _obtener_costo_historial(conn, codigodepo, codigo, fecha_hasta)
    if costo_hist is not None:
        return costo_hist, "historial_remitos"

    return None, "sin_costo"


def _obtener_movimientos_periodo(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_desde: str,
    fecha_hasta: str,
    tipomov: str,
    tipo: str | None = None,
) -> pd.DataFrame:
    """
    Retorna movimientos del período filtrados por tipomov y opcionalmente tipo.
    Columnas: codigo, diferencia, costo (por fila individual — se agrega afuera)
    """
    if tipo:
        return conn.execute("""
            SELECT codigo, diferencia, costo
            FROM movimientos
            WHERE codigodepo = ?
              AND fecha >= ? AND fecha <= ?
              AND tipomov = ?
              AND tipo = ?
        """, [codigodepo, fecha_desde, fecha_hasta, tipomov, tipo]).df()
    else:
        return conn.execute("""
            SELECT codigo, diferencia, costo
            FROM movimientos
            WHERE codigodepo = ?
              AND fecha >= ? AND fecha <= ?
              AND tipomov = ?
        """, [codigodepo, fecha_desde, fecha_hasta, tipomov]).df()


def _obtener_ventas(
    conn: duckdb.DuckDBPyConnection,
    codigodepo: str,
    fecha_desde: str,
    fecha_hasta: str,
) -> pd.DataFrame:
    return conn.execute("""
        SELECT codigo,
               SUM(unidades)   AS unidades_vendidas,
               SUM(venta_neta) AS venta_neta
        FROM ventas
        WHERE codigodepo = ?
          AND fecha_desde >= ? AND fecha_hasta <= ?
        GROUP BY codigo
    """, [codigodepo, fecha_desde, fecha_hasta]).df()


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def calcular_merma(
    proyecto: str,
    codigodepo: str,
    fecha_conteo: str,
    modo: str = "conteo_anterior",
    dias: int = 90,
) -> pd.DataFrame:
    """
    Calcula merma por SKU con tres componentes separados.

    Args:
        proyecto:     Nombre del proyecto
        codigodepo:   Código de sucursal
        fecha_conteo: Fecha del conteo (YYYY-MM-DD)
        modo:         "conteo_anterior" o "periodo_fijo"
        dias:         Días hacia atrás para modo "periodo_fijo"

    Returns:
        DataFrame con una fila por SKU. Columnas principales:

        Identificación:
            codigo, descripcion

        Ventana temporal:
            fecha_desde, fecha_hasta, dias_ventana, ventana_origen

        Conteo (merma_inv):
            stock_sistema, stock_real, diferencia_inv,
            costo_inv, fuente_costo_inv,
            merma_inv_unidades, merma_inv_valorizada

        Remitos (merma_rem):
            diferencia_rem,
            costo_rem, fuente_costo_rem,
            merma_rem_unidades, merma_rem_valorizada

        Ajustes CS (merma_cs):
            diferencia_cs,
            costo_cs, fuente_costo_cs,
            merma_cs_unidades, merma_cs_valorizada

        Totales:
            merma_total_unidades, merma_total_valorizada

        Ventas:
            unidades_vendidas, venta_neta,
            pct_merma_sobre_ventas
    """
    if modo not in ("conteo_anterior", "periodo_fijo"):
        raise ValueError(f"modo debe ser 'conteo_anterior' o 'periodo_fijo', no '{modo}'")

    conn = _get_connection(proyecto)
    fecha_conteo_dt = pd.to_datetime(fecha_conteo).date()

    # 1. Obtener conteo
    df_conteo = _obtener_conteo(conn, codigodepo, fecha_conteo)

    # 2. Calcular por SKU
    registros = []

    for _, row in df_conteo.iterrows():
        codigo = row["codigo"]

        # --- Ventana temporal ---
        if modo == "conteo_anterior":
            fecha_anterior = _obtener_fecha_conteo_anterior(
                conn, codigodepo, fecha_conteo, codigo
            )
            if fecha_anterior:
                fecha_desde = fecha_anterior
                ventana_origen = "conteo_anterior"
            else:
                fecha_desde = fecha_conteo_dt - timedelta(days=FALLBACK_DIAS)
                ventana_origen = f"fallback_{FALLBACK_DIAS}d"
        else:
            fecha_desde = fecha_conteo_dt - timedelta(days=dias)
            ventana_origen = f"fijo_{dias}d"

        dias_ventana = (fecha_conteo_dt - fecha_desde).days
        fecha_desde_str = str(fecha_desde)

        # ================================================================
        # COMPONENTE 1 — INV (conteo físico)
        # ================================================================
        diferencia_inv = row["diferencia"]   # stock_real - stock_sistema (negativo = faltante)
        merma_inv_unidades = abs(diferencia_inv) if diferencia_inv < 0 else 0.0

        costo_inv, fuente_costo_inv = _resolver_costo(
            row["costo_unitario"], conn, codigodepo, codigo, fecha_conteo
        )
        merma_inv_valorizada = (
            merma_inv_unidades * costo_inv if costo_inv is not None else None
        )

        # ================================================================
        # COMPONENTE 2 — REM (todos los remitos del período)
        # ================================================================
        df_rem = _obtener_movimientos_periodo(
            conn, codigodepo, fecha_desde_str, fecha_conteo,
            tipomov="REM"
        )
        df_rem_sku = df_rem[df_rem["codigo"] == codigo]

        diferencia_rem = float(df_rem_sku["diferencia"].sum()) if not df_rem_sku.empty else 0.0
        merma_rem_unidades = abs(diferencia_rem) if diferencia_rem < 0 else 0.0

        # Costo promedio ponderado de los remitos del SKU en el período
        costo_rem_directo = None
        if not df_rem_sku.empty:
            costos_validos = df_rem_sku["costo"].dropna()
            costos_validos = costos_validos[costos_validos > 0]
            if not costos_validos.empty:
                costo_rem_directo = float(costos_validos.mean())

        costo_rem, fuente_costo_rem = _resolver_costo(
            costo_rem_directo, conn, codigodepo, codigo, fecha_conteo
        )
        merma_rem_valorizada = (
            merma_rem_unidades * costo_rem if costo_rem is not None else None
        )

        # ================================================================
        # COMPONENTE 3 — CS (ajustes del período)
        # ================================================================
        df_cs = _obtener_movimientos_periodo(
            conn, codigodepo, fecha_desde_str, fecha_conteo,
            tipomov="AJU", tipo="CS"
        )
        df_cs_sku = df_cs[df_cs["codigo"] == codigo]

        diferencia_cs = float(df_cs_sku["diferencia"].sum()) if not df_cs_sku.empty else 0.0
        merma_cs_unidades = abs(diferencia_cs) if diferencia_cs < 0 else 0.0

        costo_cs_directo = None
        if not df_cs_sku.empty:
            costos_validos = df_cs_sku["costo"].dropna()
            costos_validos = costos_validos[costos_validos > 0]
            if not costos_validos.empty:
                costo_cs_directo = float(costos_validos.mean())

        costo_cs, fuente_costo_cs = _resolver_costo(
            costo_cs_directo, conn, codigodepo, codigo, fecha_conteo
        )
        merma_cs_valorizada = (
            merma_cs_unidades * costo_cs if costo_cs is not None else None
        )

        # ================================================================
        # TOTALES
        # ================================================================
        merma_total_unidades = merma_inv_unidades + merma_rem_unidades + merma_cs_unidades

        componentes_val = [
            v for v in [merma_inv_valorizada, merma_rem_valorizada, merma_cs_valorizada]
            if v is not None
        ]
        merma_total_valorizada = sum(componentes_val) if componentes_val else None

        registros.append({
            # Identificación
            "codigo":                 codigo,
            "descripcion":            row["descripcion"],
            # Ventana
            "fecha_desde":            fecha_desde,
            "fecha_hasta":            fecha_conteo_dt,
            "dias_ventana":           dias_ventana,
            "ventana_origen":         ventana_origen,
            # INV
            "stock_sistema":          row["stock_sistema"],
            "stock_real":             row["stock_real"],
            "diferencia_inv":         diferencia_inv,
            "costo_inv":              costo_inv,
            "fuente_costo_inv":       fuente_costo_inv,
            "merma_inv_unidades":     merma_inv_unidades,
            "merma_inv_valorizada":   merma_inv_valorizada,
            # REM
            "diferencia_rem":         diferencia_rem,
            "costo_rem":              costo_rem,
            "fuente_costo_rem":       fuente_costo_rem,
            "merma_rem_unidades":     merma_rem_unidades,
            "merma_rem_valorizada":   merma_rem_valorizada,
            # CS
            "diferencia_cs":          diferencia_cs,
            "costo_cs":               costo_cs,
            "fuente_costo_cs":        fuente_costo_cs,
            "merma_cs_unidades":      merma_cs_unidades,
            "merma_cs_valorizada":    merma_cs_valorizada,
            # Totales
            "merma_total_unidades":   merma_total_unidades,
            "merma_total_valorizada": merma_total_valorizada,
        })

    df = pd.DataFrame(registros)

    # 3. Ventas del período
    fecha_desde_min = str(df["fecha_desde"].min())
    df_ventas = _obtener_ventas(conn, codigodepo, fecha_desde_min, fecha_conteo)
    conn.close()

    # 4. Join ventas
    df = df.merge(df_ventas, on="codigo", how="left")
    df["unidades_vendidas"] = df["unidades_vendidas"].fillna(0)
    df["venta_neta"] = df["venta_neta"].fillna(0)

    # 5. % merma total sobre ventas
    df["pct_merma_sobre_ventas"] = None
    mask = df["merma_total_valorizada"].notna() & (df["venta_neta"] > 0)
    df.loc[mask, "pct_merma_sobre_ventas"] = (
        df.loc[mask, "merma_total_valorizada"] / df.loc[mask, "venta_neta"] * 100
    ).round(4)

    # 6. Ordenar por merma total valorizada descendente
    df = df.sort_values("merma_total_valorizada", ascending=False, na_position="last")
    df = df.reset_index(drop=True)

    return df
