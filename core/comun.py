"""
core/comun.py — Utilidades compartidas por todos los dominios de análisis

Centraliza lo que antes estaba duplicado en cada módulo: conexión a la base,
valorización desde el snapshot de stock, jerarquía de rubros y catálogos.
Todos los motores (merma, inventario, distribución, comercial, forecasting)
importan desde acá.
"""

import duckdb
import pandas as pd

# Modos de valorización disponibles (columna del snapshot de stock)
MODOS_VALORIZACION = ("costo", "lista_1")

# Sólo para Merma: además de costo/lista_1 uniforme, "mixto" valoriza cada
# fila según si es una venta o no (ver precio_unitario_fila). No se expone en
# otros dominios (Comercial, Inventario, Distribución) porque no tienen la
# semántica de es_venta por fila lista para reusar.
MODOS_VALORIZACION_MERMA = MODOS_VALORIZACION + ("mixto",)


def validar_modo_valorizacion(modo: str, modos: tuple = MODOS_VALORIZACION) -> None:
    """Levanta ValueError si `modo` no es uno de `modos` (MODOS_VALORIZACION
    por defecto; Merma pasa MODOS_VALORIZACION_MERMA para admitir "mixto")."""
    if modo not in modos:
        raise ValueError(
            f"modo_valorizacion debe ser uno de {modos}, no '{modo}'"
        )

# Centros de distribución: la mercadería entra acá desde el proveedor y de acá
# se reparte a las bocas. Varios análisis los tratan aparte para no mezclar el
# ingreso al CD con la reposición que efectivamente llega a la sucursal.
DEPOSITOS_DISTRIBUCION = ("001", "002")

# Abreviaturas en español, fijas — no depender del locale del SO (%b de
# strftime da nombres en inglés en este entorno).
MESES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
         7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def conectar(proyecto: str) -> duckdb.DuckDBPyConnection:
    """Conexión al DuckDB del proyecto (falla claro si no existe). Solo
    lectura si el proceso activó setup_db.activar_modo_solo_lectura()."""
    from setup_db import get_db_path, modo_solo_lectura
    db_path = get_db_path(proyecto)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No existe la base de datos para '{proyecto}'. "
            f"Corré: python setup_db.py --project {proyecto}"
        )
    return duckdb.connect(str(db_path), read_only=modo_solo_lectura())


# ---------------------------------------------------------------------------
# Valorización desde stock
# ---------------------------------------------------------------------------

def precios_snapshot(conn: duckdb.DuckDBPyConnection,
                     fecha_valorizacion: str | None = None) -> pd.DataFrame:
    """
    Precio por SKU (costo y lista_1) del snapshot elegido, o el más reciente.

    El precio es global por SKU (una sola columna en el ERP), así que no se
    filtra por sucursal. Con `fecha_valorizacion` se toma el snapshot más
    reciente <= esa fecha; sin ella, el último disponible (valor actual).
    """
    if fecha_valorizacion:
        fv = str(pd.to_datetime(fecha_valorizacion).date())
        return conn.execute("""
            SELECT codigo, costo, lista_1 FROM stock_sucursal
            WHERE fecha_snapshot <= $fv::DATE
            QUALIFY row_number() OVER (
                PARTITION BY codigo
                ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
            ) = 1
        """, {"fv": fv}).df()
    return conn.execute("""
        SELECT codigo, costo, lista_1 FROM stock_sucursal
        QUALIFY row_number() OVER (
            PARTITION BY codigo
            ORDER BY fecha_snapshot DESC, fecha_ingesta DESC, id DESC
        ) = 1
    """).df()


def precio_unitario_fila(codigo: pd.Series, modo: str, df_val: pd.DataFrame,
                         es_venta: pd.Series | None = None) -> pd.Series:
    """
    Precio unitario por fila desde el snapshot de precios (`df_val`, salida
    de `precios_snapshot`), según el modo:
      - "costo" / "lista_1": esa columna, uniforme para todas las filas.
      - "mixto" (sólo Merma): `lista_1` en las filas de venta (`es_venta`),
        `costo` en el resto (remitos, ajustes, inventario...). Sin `es_venta`
        (funciones que sólo ven movimientos de merma, nunca de venta),
        "mixto" resuelve siempre a costo.
    """
    costo_map = df_val.set_index("codigo")["costo"] if not df_val.empty else pd.Series(dtype="float64")
    if modo == "costo":
        return codigo.map(costo_map)
    lista_map = df_val.set_index("codigo")["lista_1"] if not df_val.empty else pd.Series(dtype="float64")
    if modo == "lista_1":
        return codigo.map(lista_map)
    # mixto
    u_costo = codigo.map(costo_map)
    if es_venta is None:
        return u_costo
    return codigo.map(lista_map).where(es_venta, u_costo)


def snapshot_usado(conn: duckdb.DuckDBPyConnection,
                   fecha_valorizacion: str | None = None):
    """Fecha del snapshot efectivamente usado por precios_snapshot (str o None)."""
    r = conn.execute(
        "SELECT MAX(fecha_snapshot) FROM stock_sucursal"
        + (" WHERE fecha_snapshot <= ?" if fecha_valorizacion else ""),
        [str(pd.to_datetime(fecha_valorizacion).date())] if fecha_valorizacion else [],
    ).fetchone()[0]
    return str(r) if r else None


# ---------------------------------------------------------------------------
# Jerarquía de rubros
# ---------------------------------------------------------------------------

def mapa_estructura(df_est: pd.DataFrame) -> pd.DataFrame:
    """
    Lookup de estructura indexable por código de rubro O por nombre
    (articulos.rubro puede traer cualquiera de los dos).
    """
    registros = []
    for _, r in df_est.iterrows():
        for clave in (r["rubro_cod"], r["rubro"]):
            if pd.notna(clave):
                registros.append({
                    "_clave": str(clave).strip(),
                    "rubro_desc": r["rubro"],
                    "super_rubro": r["super_rubro"],
                    "gran_super_rubro": r["gran_super_rubro"],
                })
    if not registros:
        return pd.DataFrame(columns=["_clave", "rubro_desc", "super_rubro", "gran_super_rubro"])
    return pd.DataFrame(registros).drop_duplicates("_clave")


def adjuntar_rubros(df: pd.DataFrame, df_est: pd.DataFrame) -> pd.DataFrame:
    """
    Resuelve df['rubro'] (código o nombre) a la descripción y agrega
    super_rubro / gran_super_rubro. Requiere columna 'rubro' en df.
    """
    mapa = mapa_estructura(df_est)
    df = df.copy()
    df["_clave"] = df["rubro"].astype("string").str.strip()
    df = df.merge(mapa, on="_clave", how="left").drop(columns=["_clave"])
    df["rubro"] = df["rubro_desc"].fillna(df["rubro"])
    return df.drop(columns=["rubro_desc"])


# ---------------------------------------------------------------------------
# Catálogos (para la UI)
# ---------------------------------------------------------------------------

def listar_categorias(proyecto: str) -> list[str]:
    """Categorías ordenadas según `tipos_categoria`."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT categoria FROM tipos_categoria
            GROUP BY categoria ORDER BY MIN(orden), categoria
        """).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def categorias_merma(proyecto: str) -> list[str]:
    """Categorías marcadas es_merma, en orden de catálogo."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT categoria FROM tipos_categoria
            WHERE es_merma GROUP BY categoria ORDER BY MIN(orden), categoria
        """).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def catalogo_articulos(proyecto: str) -> pd.DataFrame:
    """
    Catálogo de SKUs con la jerarquía ya resuelta: codigo, descripcion, rubro
    (descripción, no código), marca, ean, activo, super_rubro, gran_super_rubro.
    Sirve para armar los filtros de la UI y para resolver qué SKUs analizar.

    `activo` viaja siempre (puede ser NA si el ERP no lo informó); no se
    filtra acá — quien quiera ocultar inactivos usa `filtrar_catalogo(...,
    solo_activos=True)` explícitamente, porque un SKU inactivo puede haber
    tenido ventas hasta hace poco y conviene no ocultarlo por defecto.
    """
    conn = conectar(proyecto)
    try:
        df_art = conn.execute(
            "SELECT codigo, descripcion, rubro, marca, ean, activo FROM articulos").df()
        df_est = conn.execute(
            "SELECT rubro_cod, rubro, super_rubro, gran_super_rubro FROM estructura").df()
    finally:
        conn.close()
    return adjuntar_rubros(df_art, df_est)


def filtrar_catalogo(cat: pd.DataFrame, texto: str | None = None,
                     rubros: list[str] | None = None,
                     marcas: list[str] | None = None,
                     gran_super_rubros: list[str] | None = None,
                     super_rubros: list[str] | None = None,
                     solo_activos: bool = False) -> pd.DataFrame:
    """
    Aplica los filtros de la UI sobre el catálogo. `texto` busca en código y
    descripción (sin distinguir mayúsculas); el resto son listas de valores
    exactos. Los filtros se combinan con AND; los vacíos no filtran.

    `solo_activos=True` descarta los SKUs con `activo` explícitamente False
    (deja pasar True y nulos, para no perder SKUs sin el flag cargado). Por
    defecto no filtra nada.
    """
    df = cat
    for col, valores in (("rubro", rubros), ("marca", marcas),
                         ("gran_super_rubro", gran_super_rubros),
                         ("super_rubro", super_rubros)):
        if valores and col in df.columns:
            df = df[df[col].isin(valores)]
    if texto and texto.strip():
        t = texto.strip().lower()
        df = df[df["codigo"].astype("string").str.lower().str.contains(t, na=False)
                | df["descripcion"].astype("string").str.lower().str.contains(t, na=False)]
    if solo_activos and "activo" in df.columns:
        df = df[df["activo"] != False]  # noqa: E712 (deja pasar True y NA)
    return df


def listar_fechas_valorizacion(proyecto: str) -> list[str]:
    """Fechas de snapshot de stock disponibles, de la más reciente a la más vieja."""
    conn = conectar(proyecto)
    try:
        rows = conn.execute("""
            SELECT DISTINCT fecha_snapshot FROM stock_sucursal
            ORDER BY fecha_snapshot DESC
        """).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Movimientos crudos (comprobantes)
# ---------------------------------------------------------------------------

def tipos_movimiento(proyecto: str) -> pd.DataFrame:
    """
    Catálogo completo de `tipos_categoria` (tipo, tipomov, categoria), en
    orden de catálogo — a diferencia de `comercial.comprobantes.tipos_disponibles`,
    no se restringe a Ventas/Remitido: incluye también Inventario (INV) y
    Ajustes (CS), para selectores que necesitan cualquier tipo de comprobante.
    """
    conn = conectar(proyecto)
    try:
        df = conn.execute("""
            SELECT tipo, tipomov, categoria FROM tipos_categoria
            ORDER BY orden, tipo
        """).df()
    finally:
        conn.close()
    return df


def movimientos_detalle(proyecto: str, codigo: str, fecha_desde: str,
                        fecha_hasta: str,
                        codigodepo: list[str] | str | None = None) -> pd.DataFrame:
    """
    Líneas de movimiento crudas de UN SKU en una ventana de fechas — vista de
    auditoría/drill-down (no agrega ni valoriza).

    Returns:
        DataFrame: fecha, tipomov, tipo, numero, codigodepo, diferencia,
        ingreso, egreso, usuario. Ordenado por fecha.
    """
    desde = str(pd.to_datetime(fecha_desde).date())
    hasta = str(pd.to_datetime(fecha_hasta).date())
    depos = [codigodepo] if isinstance(codigodepo, str) else (list(codigodepo) if codigodepo else None)

    filtro_depo = "AND codigodepo IN (SELECT unnest($depos))" if depos else ""
    params = {"codigo": codigo, "desde": desde, "hasta": hasta}
    if depos:
        params["depos"] = depos

    conn = conectar(proyecto)
    try:
        df = conn.execute(f"""
            SELECT fecha, tipomov, tipo, numero, codigodepo,
                   diferencia, ingreso, egreso, usuario
            FROM movimientos
            WHERE codigo = $codigo
              AND fecha >= $desde::DATE AND fecha <= $hasta::DATE
              {filtro_depo}
            ORDER BY fecha, codigodepo, numero
        """, params).df()
    finally:
        conn.close()
    return df
