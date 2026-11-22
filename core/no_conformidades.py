"""
core/no_conformidades.py — No Conformidades (NC) y Observaciones, ciclo ISO 9001 §10.2

Persistencia en un SQLite propio por proyecto (projects/<p>/no_conformidades.sqlite),
separado del .duckdb: ui/app_cliente.py abre DuckDB en solo lectura y las
ingestas lo lockean, mientras que las NC necesitan escrituras de varios usuarios.
WAL + busy_timeout; el archivo tiene que vivir en disco local (nunca en SMB).

Ciclo de una NC:
    BORRADOR → EMITIDA → ENVIADA → RESPONDIDA → ACEPTADA → CERRADA_SUCURSAL → CERRADA
                            ↑           │                                   ↘ CERRADA_NO_EFICAZ (+ NC nueva)
                            └───────────┘ descargo observado
    EMITIDA / ENVIADA / RESPONDIDA / ACEPTADA → ANULADA

  - ACEPTADA: descargo aceptado, acciones correctivas en implementación.
  - CERRADA_SUCURSAL: todas las acciones implementadas; el caso se cierra con la
    sucursal con un acta (HTML congelado con hash, exportable y enviable por mail).
    La verificación de eficacia (ISO 9001 §10.2.1 d) queda PROGRAMADA (fecha y
    criterio en `nc_cierre`) y se registra después: no bloquea el cierre con la
    sucursal, pero la NC no está cerrada para el SGC hasta verificarla.
  - EN_VERIFICACION: estado heredado (antes de 2026-10). Equivale a ACEPTADA; las
    NC que quedaron ahí siguen por el camino nuevo.

Auditabilidad:
  - Número `NNDDDMMAAAA` (orden por sucursal y mes, depósito, mes, año), asignado
    al emitir con BEGIN IMMEDIATE; nunca se reutiliza.
  - Al emitir se congela el contenido y el HTML (sha256_html, también dentro del
    evento EMITIDA). Reimprimir valida contra el hash del evento.
  - Plazos concretos recién en ENVIADA (desde la recepción): la NC impresa solo
    muestra plazos relativos.
  - nc_eventos (y respuestas, acciones, verificaciones, adjuntos, observaciones)
    son append-only por trigger; cada evento encadena el hash del anterior.

Los permisos se pasan en un `Actor` (username, nombre, roles). La UI arma el
actor desde auth/usuarios.yaml (cliente) o con todos los roles (admin).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import holidays

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

ROLES_NC = ("nc_emitir", "nc_responder", "nc_verificar", "nc_anular")

ESTADOS = ("BORRADOR", "EMITIDA", "ENVIADA", "RESPONDIDA", "ACEPTADA", "EN_VERIFICACION",
           "CERRADA_SUCURSAL", "CERRADA", "CERRADA_NO_EFICAZ", "ANULADA")
# Descargo aceptado, acciones en implementación (EN_VERIFICACION es el nombre heredado).
ESTADOS_ACEPTADA = ("ACEPTADA", "EN_VERIFICACION")
ESTADOS_ABIERTOS = ("EMITIDA", "ENVIADA", "RESPONDIDA") + ESTADOS_ACEPTADA
# Cerrada con la sucursal, falta verificar la eficacia.
ESTADOS_VERIFICACION_PENDIENTE = ("CERRADA_SUCURSAL",)
ESTADOS_FINALES = ("CERRADA", "CERRADA_NO_EFICAZ", "ANULADA")
# Ya notificada: admite reenviar la NC por mail.
ESTADOS_REENVIO = ESTADOS_ABIERTOS[1:] + ESTADOS_VERIFICACION_PENDIENTE
# Tienen acta de cierre (se puede descargar y enviar).
ESTADOS_CON_ACTA = ESTADOS_VERIFICACION_PENDIENTE + ("CERRADA", "CERRADA_NO_EFICAZ")

# Transiciones válidas (también las controla un trigger, como segunda barrera).
TRANSICIONES = {
    ("BORRADOR", "EMITIDA"), ("EMITIDA", "ENVIADA"), ("ENVIADA", "RESPONDIDA"),
    ("RESPONDIDA", "ENVIADA"), ("RESPONDIDA", "ACEPTADA"),
    ("ACEPTADA", "CERRADA_SUCURSAL"), ("EN_VERIFICACION", "CERRADA_SUCURSAL"),
    ("CERRADA_SUCURSAL", "CERRADA"), ("CERRADA_SUCURSAL", "CERRADA_NO_EFICAZ"),
    ("EMITIDA", "ANULADA"), ("ENVIADA", "ANULADA"), ("RESPONDIDA", "ANULADA"),
    ("ACEPTADA", "ANULADA"), ("EN_VERIFICACION", "ANULADA"),
}

# Versión del esquema del SQLite de NC (PRAGMA user_version), ver `_migrar`.
VERSION_ESQUEMA = 1

CATEGORIAS_6M = ["Método", "Mano de obra", "Material", "Máquina", "Medición", "Medio"]

CONFIG_DEFAULT = {
    # Severidad de menor a mayor. Plazos contados desde la recepción (ENVIADA).
    #   contencion_horas: horas corridas; contencion_con_descargo: vence con el descargo
    #   implementacion_max_dias: días corridos; None = la propone la sucursal
    #   verificacion_dias: días corridos desde el cierre con la sucursal que se sugieren
    #                      para verificar la eficacia (editable al cerrar)
    "tipos": {
        "AMARILLO": {"severidad": 1, "color": "#f1c40f", "contencion_horas": None,
                     "contencion_con_descargo": False, "descargo_dias_habiles": 10,
                     "implementacion_max_dias": None, "verificacion_dias": 90},
        "NARANJA":  {"severidad": 2, "color": "#e67e22", "contencion_horas": None,
                     "contencion_con_descargo": True, "descargo_dias_habiles": 5,
                     "implementacion_max_dias": 60, "verificacion_dias": 60},
        "ROJO":     {"severidad": 3, "color": "#e74c3c", "contencion_horas": 48,
                     "contencion_con_descargo": False, "descargo_dias_habiles": 3,
                     "implementacion_max_dias": 30, "verificacion_dias": 30},
    },
    "areas": ["CONTROL DE INVENTARIO", "RECEPCIÓN DE MERCADERÍA", "DISTRIBUCIÓN",
              "FACTURACIÓN"],
    "impactos": ["PERDIDA DE TRAZABILIDAD", "PERDIDAS ECONOMICAS",
                 "RETRASOS EN LA LOGÍSTICA", "INSATISFACCIÓN DEL CLIENTE"],
    "responsables_accion": ["Encargado", "Subencargado", "Coordinador de calidad"],
    "acciones_plantilla": [
        "Se pide realizar un nuevo conteo para subsanar las discrepancias",
        "Se informa que la sucursal se encuentra en observación y se pide la máxima "
        "profesionalidad a la hora de realizar conteos futuros",
        "Se recuerda que los conteos deben ser siempre verídicos y que los mismos revisten "
        "carácter de declaración jurada, intimando a su gestión profesional",
    ],
    "intro_plantilla": (
        "Durante el control de cumplimiento de los procedimientos de la empresa realizado "
        "el día {fecha}, se identificó una desviación respecto a los procedimientos "
        "establecidos en los Procedimientos Estandarizados de Operaciones de la empresa. "
        "Se detectó lo siguiente:"
    ),
    "sabado_habil": False,
    # codigodepo -> subdivisión de `holidays` (AR: G = Santiago del Estero, J = San Juan…)
    "provincia_por_deposito": {},
    # subdivisión -> fechas ISO de feriados que `holidays` no trae
    "feriados_extra": {},
    "verificador_distinto": True,
    "admin_nombre": "Administrador",
}


class NCError(Exception):
    """Error de negocio: el mensaje se muestra tal cual en la UI."""


@dataclass(frozen=True)
class Actor:
    username: str
    nombre: str
    roles: frozenset = field(default_factory=frozenset)

    def tiene(self, rol: str) -> bool:
        return rol in self.roles


def actor_admin(nombre: str = "Administrador") -> Actor:
    return Actor("admin", nombre, frozenset(ROLES_NC))


# ---------------------------------------------------------------------------
# Rutas y configuración
# ---------------------------------------------------------------------------

def dir_proyecto(proyecto: str | Path) -> Path:
    """`proyecto` puede ser un nombre (projects/<nombre>) o un directorio (tests)."""
    if isinstance(proyecto, Path):
        proyecto.mkdir(parents=True, exist_ok=True)
        return proyecto
    from setup_db import get_db_path
    return get_db_path(proyecto).parent


def ruta_db(proyecto) -> Path:
    return dir_proyecto(proyecto) / "no_conformidades.sqlite"


def dir_adjuntos(proyecto) -> Path:
    return dir_proyecto(proyecto) / "nc_adjuntos"


def cargar_config(proyecto) -> dict:
    cfg = json.loads(json.dumps(CONFIG_DEFAULT))
    f = dir_proyecto(proyecto) / "nc_config.json"
    if f.exists():
        cfg.update(json.loads(f.read_text(encoding="utf-8")))
    return cfg


def guardar_config(proyecto, cfg: dict) -> None:
    (dir_proyecto(proyecto) / "nc_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def tipos_ordenados(cfg: dict) -> list[str]:
    return sorted(cfg["tipos"], key=lambda t: cfg["tipos"][t]["severidad"])


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

# Columnas de `nc` que quedan congeladas al emitir.
_CONGELADAS = (
    "numero", "orden", "codigodepo", "mes", "anio", "fecha", "sucursal_nombre", "area",
    "tipo", "tipo_severidad", "plazo_descargo_dias", "plazo_contencion_horas",
    "contencion_con_descargo", "plazo_impl_max_dias", "merma_linea_base",
    "linea_base_unidad", "linea_base_fuente", "impactos", "responsable_accion",
    "solicitante", "intro", "req_codigo", "req_titulo", "req_texto", "sop_version_id",
    "sop_version", "evidencia_txt", "desvio_txt", "acciones_recomendadas", "html_emitido",
    "sha256_html", "sha256_contenido", "emitida_en", "reemplaza_a", "origen_id",
    "creada_por", "creada_en",
)


def _trigger_inmutable(tabla: str) -> str:
    return f"""
CREATE TRIGGER IF NOT EXISTS {tabla}_no_update BEFORE UPDATE ON {tabla}
BEGIN SELECT RAISE(ABORT, '{tabla} es de solo agregado: no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS {tabla}_no_delete BEFORE DELETE ON {tabla}
BEGIN SELECT RAISE(ABORT, '{tabla} es de solo agregado: no se borra'); END;
"""


_TRANSICIONES_SQL = ", ".join(f"'{a}>{b}'" for a, b in sorted(TRANSICIONES))
_CAMBIA_CONGELADA = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _CONGELADAS)

# Aparte del SCHEMA porque `_migrar` lo recrea cuando cambian las transiciones
# (CREATE ... IF NOT EXISTS no actualiza un trigger existente).
_TRIGGER_TRANSICION = f"""
CREATE TRIGGER IF NOT EXISTS nc_transicion BEFORE UPDATE OF estado ON nc
WHEN NEW.estado IS NOT OLD.estado
 AND (OLD.estado || '>' || NEW.estado) NOT IN ({_TRANSICIONES_SQL})
BEGIN SELECT RAISE(ABORT, 'Transición de estado no permitida'); END;
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS sop_versiones (
    id              INTEGER PRIMARY KEY,
    version         TEXT NOT NULL,
    vigencia_desde  TEXT,
    archivo         TEXT NOT NULL,
    ruta            TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    metodo_parseo   TEXT,
    estado          TEXT NOT NULL DEFAULT 'BORRADOR'
                    CHECK (estado IN ('BORRADOR','ACTIVA','REEMPLAZADA')),
    cargado_por     TEXT NOT NULL,
    cargado_en      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sop_articulos (
    id            INTEGER PRIMARY KEY,
    version_id    INTEGER NOT NULL REFERENCES sop_versiones(id),
    codigo        TEXT NOT NULL,
    titulo        TEXT NOT NULL,
    texto         TEXT,
    padre_codigo  TEXT,
    nivel         INTEGER,
    orden         INTEGER,
    UNIQUE (version_id, codigo)
);

CREATE TABLE IF NOT EXISTS nc (
    id                      INTEGER PRIMARY KEY,
    numero                  TEXT UNIQUE,
    orden                   INTEGER,
    codigodepo              TEXT NOT NULL,
    mes                     INTEGER,
    anio                    INTEGER,
    estado                  TEXT NOT NULL DEFAULT 'BORRADOR',
    fecha                   TEXT NOT NULL,
    sucursal_nombre         TEXT,
    area                    TEXT,
    tipo                    TEXT NOT NULL,
    tipo_severidad          INTEGER,
    plazo_descargo_dias     INTEGER,
    plazo_contencion_horas  INTEGER,
    contencion_con_descargo INTEGER,
    plazo_impl_max_dias     INTEGER,
    recepcion_ts            TEXT,
    fecha_limite_contencion TEXT,
    fecha_limite_descargo   TEXT,
    merma_linea_base        REAL,
    linea_base_unidad       TEXT,
    linea_base_fuente       TEXT,
    impactos                TEXT,
    responsable_accion      TEXT,
    solicitante             TEXT,
    intro                   TEXT,
    req_codigo              TEXT,
    req_titulo              TEXT,
    req_texto               TEXT,
    sop_version_id          INTEGER,
    sop_version             TEXT,
    evidencia_txt           TEXT,
    desvio_txt              TEXT,
    acciones_recomendadas   TEXT,
    html_emitido            TEXT,
    sha256_html             TEXT,
    sha256_contenido        TEXT,
    emitida_en              TEXT,
    reemplaza_a             INTEGER REFERENCES nc(id),
    origen_id               INTEGER REFERENCES nc(id),
    creada_por              TEXT NOT NULL,
    creada_en               TEXT NOT NULL,
    UNIQUE (codigodepo, anio, mes, orden)
);

CREATE TABLE IF NOT EXISTS nc_respuesta (
    id            INTEGER PRIMARY KEY,
    nc_id         INTEGER NOT NULL REFERENCES nc(id),
    intento       INTEGER NOT NULL,
    correccion    TEXT NOT NULL,
    causa_raiz    TEXT NOT NULL,
    categoria_6m  TEXT NOT NULL,
    cargada_por   TEXT NOT NULL,
    cargada_en    TEXT NOT NULL,
    UNIQUE (nc_id, intento)
);

CREATE TABLE IF NOT EXISTS nc_acciones_correctivas (
    id                INTEGER PRIMARY KEY,
    respuesta_id      INTEGER NOT NULL REFERENCES nc_respuesta(id),
    descripcion       TEXT NOT NULL,
    responsable       TEXT NOT NULL,
    fecha_compromiso  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nc_verificacion (
    id             INTEGER PRIMARY KEY,
    nc_id          INTEGER NOT NULL UNIQUE REFERENCES nc(id),
    verificador    TEXT NOT NULL,
    fecha          TEXT NOT NULL,
    resultado      TEXT NOT NULL CHECK (resultado IN ('EFICAZ','NO_EFICAZ')),
    evidencia_txt  TEXT NOT NULL,
    registrada_en  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nc_adjuntos (
    id            INTEGER PRIMARY KEY,
    nc_id         INTEGER NOT NULL REFERENCES nc(id),
    etapa         TEXT NOT NULL CHECK (etapa IN
                  ('hallazgo','envio','respuesta','observacion','verificacion','anulacion')),
    respuesta_id  INTEGER REFERENCES nc_respuesta(id),
    nombre        TEXT NOT NULL,
    ruta          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    subido_por    TEXT NOT NULL,
    subido_en     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observaciones (
    id               INTEGER PRIMARY KEY,
    numero           TEXT NOT NULL UNIQUE,
    orden            INTEGER NOT NULL,
    codigodepo       TEXT NOT NULL,
    mes              INTEGER NOT NULL,
    anio             INTEGER NOT NULL,
    fecha            TEXT NOT NULL,
    sucursal_nombre  TEXT,
    req_codigo       TEXT,
    req_titulo       TEXT,
    sop_version_id   INTEGER,
    texto            TEXT NOT NULL,
    creada_por       TEXT NOT NULL,
    creada_en        TEXT NOT NULL,
    UNIQUE (codigodepo, anio, mes, orden)
);

-- Anexos con datos de IQMerma (core/nc_anexos.py): editables en BORRADOR,
-- congelados desde la emisión. `imprimir` = va dentro del documento emitido.
CREATE TABLE IF NOT EXISTS nc_anexos (
    id            INTEGER PRIMARY KEY,
    nc_id         INTEGER NOT NULL REFERENCES nc(id),
    orden         INTEGER NOT NULL,
    tipo          TEXT NOT NULL,
    titulo        TEXT NOT NULL,
    params        TEXT NOT NULL,
    imprimir      INTEGER NOT NULL DEFAULT 1,
    html          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    resumen       TEXT,
    corte_datos   TEXT,
    generado_por  TEXT NOT NULL,
    generado_en   TEXT NOT NULL
);

-- Cada intento de envío (mail; luego WhatsApp), exitoso o fallido.
CREATE TABLE IF NOT EXISTS nc_envios (
    id            INTEGER PRIMARY KEY,
    nc_id         INTEGER NOT NULL REFERENCES nc(id),
    canal         TEXT NOT NULL,
    remitente     TEXT,
    para          TEXT NOT NULL,
    cc            TEXT,
    cco           TEXT,
    asunto        TEXT,
    cuerpo        TEXT,
    adjunto_id    INTEGER REFERENCES nc_adjuntos(id),
    message_id    TEXT,
    resultado     TEXT NOT NULL CHECK (resultado IN ('OK','ERROR')),
    error         TEXT,
    enviado_por   TEXT NOT NULL,
    enviado_en    TEXT NOT NULL
);

-- Implementación de cada acción correctiva del descargo aceptado (la reporta la
-- sucursal y se transcribe). Las acciones son append-only: esto va aparte.
CREATE TABLE IF NOT EXISTS nc_implementacion (
    id             INTEGER PRIMARY KEY,
    accion_id      INTEGER NOT NULL UNIQUE REFERENCES nc_acciones_correctivas(id),
    fecha_real     TEXT NOT NULL,
    evidencia_txt  TEXT NOT NULL,
    adjunto_id     INTEGER REFERENCES nc_adjuntos(id),
    registrada_por TEXT NOT NULL,
    registrada_en  TEXT NOT NULL
);

-- Cierre con la sucursal: acta congelada + verificación de eficacia programada.
CREATE TABLE IF NOT EXISTS nc_cierre (
    id                             INTEGER PRIMARY KEY,
    nc_id                          INTEGER NOT NULL UNIQUE REFERENCES nc(id),
    respuesta_id                   INTEGER NOT NULL REFERENCES nc_respuesta(id),
    aclaraciones                   TEXT,
    fecha_verificacion_programada  TEXT NOT NULL,
    criterio_verificacion          TEXT NOT NULL,
    html_acta                      TEXT NOT NULL,
    sha256_html                    TEXT NOT NULL,
    sha256_contenido               TEXT NOT NULL,
    cerrada_por                    TEXT NOT NULL,
    cerrada_en                     TEXT NOT NULL
);

-- Cadena de eventos: entidad = 'nc' | 'obs' | 'sop' | 'sistema'
CREATE TABLE IF NOT EXISTS nc_eventos (
    id               INTEGER PRIMARY KEY,
    entidad          TEXT NOT NULL,
    entidad_id       INTEGER NOT NULL,
    ts               TEXT NOT NULL,
    usuario          TEXT NOT NULL,
    tipo             TEXT NOT NULL,
    estado_anterior  TEXT,
    estado_nuevo     TEXT,
    datos            TEXT NOT NULL,
    hash_prev        TEXT,
    hash             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eventos_entidad ON nc_eventos (entidad, entidad_id);

{_trigger_inmutable("nc_eventos")}
{_trigger_inmutable("nc_respuesta")}
{_trigger_inmutable("nc_acciones_correctivas")}
{_trigger_inmutable("nc_verificacion")}
{_trigger_inmutable("nc_adjuntos")}
{_trigger_inmutable("observaciones")}
{_trigger_inmutable("nc_envios")}
{_trigger_inmutable("nc_implementacion")}
{_trigger_inmutable("nc_cierre")}

-- Contenido congelado después de emitir.
CREATE TRIGGER IF NOT EXISTS nc_congelada BEFORE UPDATE ON nc
WHEN OLD.estado <> 'BORRADOR' AND ({_CAMBIA_CONGELADA})
BEGIN SELECT RAISE(ABORT, 'NC emitida: el contenido está congelado'); END;

-- Recepción y contención: se escriben una sola vez, al pasar a ENVIADA.
CREATE TRIGGER IF NOT EXISTS nc_plazos_recepcion BEFORE UPDATE ON nc
WHEN (NEW.recepcion_ts IS NOT OLD.recepcion_ts
      OR NEW.fecha_limite_contencion IS NOT OLD.fecha_limite_contencion)
 AND NOT (OLD.estado = 'EMITIDA' AND NEW.estado = 'ENVIADA'
          AND OLD.recepcion_ts IS NULL AND OLD.fecha_limite_contencion IS NULL)
BEGIN SELECT RAISE(ABORT, 'Recepción y contención solo se fijan al registrar el envío'); END;

-- Descargo: se fija al enviar y se reabre solo con un descargo observado.
CREATE TRIGGER IF NOT EXISTS nc_plazo_descargo BEFORE UPDATE ON nc
WHEN NEW.fecha_limite_descargo IS NOT OLD.fecha_limite_descargo
 AND NOT ((OLD.estado = 'EMITIDA' AND NEW.estado = 'ENVIADA'
           AND OLD.fecha_limite_descargo IS NULL)
          OR (OLD.estado = 'RESPONDIDA' AND NEW.estado = 'ENVIADA'))
BEGIN SELECT RAISE(ABORT, 'El plazo de descargo solo cambia al enviar o al observar el descargo'); END;

{_TRIGGER_TRANSICION}

CREATE TRIGGER IF NOT EXISTS nc_anexos_congelados_upd BEFORE UPDATE ON nc_anexos
WHEN (SELECT estado FROM nc WHERE id = OLD.nc_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'NC emitida: los anexos están congelados'); END;
CREATE TRIGGER IF NOT EXISTS nc_anexos_congelados_del BEFORE DELETE ON nc_anexos
WHEN (SELECT estado FROM nc WHERE id = OLD.nc_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'NC emitida: los anexos están congelados'); END;
CREATE TRIGGER IF NOT EXISTS nc_anexos_congelados_ins BEFORE INSERT ON nc_anexos
WHEN (SELECT estado FROM nc WHERE id = NEW.nc_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'NC emitida: no se agregan anexos'); END;

CREATE TRIGGER IF NOT EXISTS nc_no_delete BEFORE DELETE ON nc
WHEN OLD.estado <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'Solo se pueden borrar borradores'); END;

-- Artículos del SOP: editables mientras la versión sea BORRADOR.
CREATE TRIGGER IF NOT EXISTS sop_articulos_congelados_upd BEFORE UPDATE ON sop_articulos
WHEN (SELECT estado FROM sop_versiones WHERE id = OLD.version_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'Versión de SOP activada: artículos congelados'); END;
CREATE TRIGGER IF NOT EXISTS sop_articulos_congelados_del BEFORE DELETE ON sop_articulos
WHEN (SELECT estado FROM sop_versiones WHERE id = OLD.version_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'Versión de SOP activada: artículos congelados'); END;
CREATE TRIGGER IF NOT EXISTS sop_articulos_congelados_ins BEFORE INSERT ON sop_articulos
WHEN (SELECT estado FROM sop_versiones WHERE id = NEW.version_id) <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'Versión de SOP activada: artículos congelados'); END;
CREATE TRIGGER IF NOT EXISTS sop_versiones_congeladas BEFORE UPDATE ON sop_versiones
WHEN NEW.sha256 IS NOT OLD.sha256 OR NEW.version IS NOT OLD.version
  OR NEW.ruta IS NOT OLD.ruta
  OR (OLD.estado <> 'BORRADOR' AND NEW.vigencia_desde IS NOT OLD.vigencia_desde)
  OR (OLD.estado = 'REEMPLAZADA')
  OR (OLD.estado = 'ACTIVA' AND NEW.estado <> 'REEMPLAZADA')
BEGIN SELECT RAISE(ABORT, 'Versión de SOP congelada'); END;
CREATE TRIGGER IF NOT EXISTS sop_versiones_no_delete BEFORE DELETE ON sop_versiones
WHEN OLD.estado <> 'BORRADOR'
BEGIN SELECT RAISE(ABORT, 'Solo se borran versiones de SOP en borrador'); END;
"""


def conectar(proyecto) -> sqlite3.Connection:
    """Conexión en autocommit: las escrituras van dentro de `_tx` (BEGIN IMMEDIATE)."""
    conn = sqlite3.connect(str(ruta_db(proyecto)), timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    if conn.execute("PRAGMA user_version").fetchone()[0] < VERSION_ESQUEMA:
        _migrar(conn)
    return conn


ACTOR_SISTEMA = Actor("sistema", "Migración de esquema")


def _migrar(conn: sqlite3.Connection) -> None:
    """Lleva una base existente a VERSION_ESQUEMA, dejando constancia en la cadena.

    v1 (2026-10): cierre con la sucursal antes de verificar la eficacia. Recrea el
    trigger de transiciones (ACEPTADA, CERRADA_SUCURSAL); EN_VERIFICACION queda como
    estado heredado y sus filas no se tocan.
    """
    with _tx(conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= VERSION_ESQUEMA:
            return  # otra conexión ya migró
        conn.execute("DROP TRIGGER IF EXISTS nc_transicion")
        conn.execute(_TRIGGER_TRANSICION)
        if conn.execute("SELECT 1 FROM nc_eventos LIMIT 1").fetchone():
            _evento(conn, "sistema", 0, ACTOR_SISTEMA, "ESQUEMA_MIGRADO", None, None, {
                "de": version, "a": VERSION_ESQUEMA,
                "cambios": ["estados ACEPTADA y CERRADA_SUCURSAL; EN_VERIFICACION heredado",
                            "tablas nc_implementacion y nc_cierre (append-only)",
                            "trigger nc_transicion recreado"],
                "nc_en_verificacion": [r[0] for r in conn.execute(
                    "SELECT id FROM nc WHERE estado = 'EN_VERIFICACION' ORDER BY id")]})
        conn.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")


@contextmanager
def _tx(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE: toma el lock de escritura de entrada, así dos emisiones
    simultáneas se serializan en vez de chocar al final (MAX+1 sin carrera)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def numero_impreso(numero: str | None) -> str:
    """'02034092026' -> '02 034 092026'."""
    if not numero or len(numero) != 11:
        return numero or "—"
    return f"{numero[:2]} {numero[2:5]} {numero[5:]}"


def _depo3(codigodepo) -> str:
    s = str(codigodepo).strip()
    if not s.isdigit() or len(s.lstrip("0") or "0") > 3:
        raise NCError(f"El código de depósito '{codigodepo}' no entra en 3 dígitos.")
    return s.zfill(3)


def _exigir(actor: Actor, rol: str) -> None:
    if not actor.tiene(rol):
        raise NCError(f"El usuario {actor.username} no tiene el rol {rol}.")


def _fila(conn, nc_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM nc WHERE id = ?", (nc_id,)).fetchone()
    if row is None:
        raise NCError(f"No existe la NC {nc_id}.")
    return row


def _estado(conn, nc_id: int, esperados: tuple[str, ...]) -> sqlite3.Row:
    row = _fila(conn, nc_id)
    if row["estado"] not in esperados:
        raise NCError(f"La NC está en {row['estado']}; esta acción requiere "
                      f"{' o '.join(esperados)}.")
    return row


# ---------------------------------------------------------------------------
# Cadena de eventos
# ---------------------------------------------------------------------------

def _canon_evento(ev: dict) -> str:
    return _json({k: ev[k] for k in ("entidad", "entidad_id", "ts", "usuario", "tipo",
                                     "estado_anterior", "estado_nuevo", "datos")})


def _hash_evento(hash_prev: str | None, ev: dict) -> str:
    return sha256_bytes(((hash_prev or "") + _canon_evento(ev)).encode("utf-8"))


def _evento(conn, entidad: str, entidad_id: int, actor: Actor, tipo: str,
            estado_anterior: str | None = None, estado_nuevo: str | None = None,
            datos: dict | None = None) -> int:
    """Agrega un evento encadenado. Debe llamarse dentro de `_tx`."""
    prev = conn.execute("SELECT hash FROM nc_eventos ORDER BY id DESC LIMIT 1").fetchone()
    hash_prev = prev["hash"] if prev else None
    ev = {"entidad": entidad, "entidad_id": entidad_id, "ts": _ahora(),
          "usuario": actor.username, "tipo": tipo, "estado_anterior": estado_anterior,
          "estado_nuevo": estado_nuevo, "datos": _json(datos or {})}
    cur = conn.execute(
        "INSERT INTO nc_eventos (entidad, entidad_id, ts, usuario, tipo, estado_anterior,"
        " estado_nuevo, datos, hash_prev, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ev["entidad"], ev["entidad_id"], ev["ts"], ev["usuario"], ev["tipo"],
         estado_anterior, estado_nuevo, ev["datos"], hash_prev, _hash_evento(hash_prev, ev)))
    return cur.lastrowid


def verificar_cadena(proyecto) -> tuple[bool, list[str]]:
    """Recorre la cadena completa. Devuelve (ok, problemas)."""
    conn = conectar(proyecto)
    problemas, prev = [], None
    for r in conn.execute("SELECT * FROM nc_eventos ORDER BY id"):
        ev = dict(r)
        if ev["hash_prev"] != prev:
            problemas.append(f"Evento {ev['id']}: hash_prev no coincide con el evento anterior.")
        if _hash_evento(ev["hash_prev"], ev) != ev["hash"]:
            problemas.append(f"Evento {ev['id']}: el contenido no coincide con su hash.")
        prev = ev["hash"]
    conn.close()
    return (not problemas, problemas)


def ancla(proyecto) -> dict | None:
    """Último eslabón de la cadena, para copiarlo o mandarlo fuera del sistema."""
    conn = conectar(proyecto)
    r = conn.execute("SELECT id, ts, hash FROM nc_eventos ORDER BY id DESC LIMIT 1").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM nc_eventos").fetchone()[0]
    conn.close()
    return {"id": r["id"], "ts": r["ts"], "hash": r["hash"], "eventos": total} if r else None


def eventos(proyecto, entidad: str, entidad_id: int) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM nc_eventos WHERE entidad = ? AND entidad_id = ? ORDER BY id",
        (entidad, entidad_id))]
    conn.close()
    for r in rows:
        r["datos"] = json.loads(r["datos"])
    return rows


# ---------------------------------------------------------------------------
# Plazos
# ---------------------------------------------------------------------------

def feriados(cfg: dict, codigodepo: str, anios: list[int]) -> set[date]:
    subdiv = cfg.get("provincia_por_deposito", {}).get(str(codigodepo).zfill(3)) \
        or cfg.get("provincia_por_deposito", {}).get(str(codigodepo))
    try:
        cal = holidays.country_holidays("AR", subdiv=subdiv, years=anios)
    except NotImplementedError:
        cal = holidays.country_holidays("AR", years=anios)
    dias = set(cal.keys())
    for f in cfg.get("feriados_extra", {}).get(subdiv or "", []):
        dias.add(date.fromisoformat(f))
    return dias


def es_habil(d: date, cfg: dict, feriados_set: set[date]) -> bool:
    if d.weekday() == 6 or (d.weekday() == 5 and not cfg.get("sabado_habil")):
        return False
    return d not in feriados_set


def sumar_dias_habiles(desde: date, n: int, cfg: dict, codigodepo: str) -> date:
    """Día hábil número `n` contado desde el día siguiente a `desde`."""
    fer = feriados(cfg, codigodepo, [desde.year, desde.year + 1])
    d, contados = desde, 0
    while contados < n:
        d += timedelta(days=1)
        if es_habil(d, cfg, fer):
            contados += 1
    return d


def calcular_plazos(nc_row, recepcion: datetime, cfg: dict) -> dict:
    """Fechas concretas a partir de la recepción, con los plazos congelados en la NC."""
    descargo = sumar_dias_habiles(recepcion.date(), nc_row["plazo_descargo_dias"], cfg,
                                  nc_row["codigodepo"])
    contencion = None
    if nc_row["plazo_contencion_horas"]:
        contencion = (recepcion + timedelta(hours=nc_row["plazo_contencion_horas"])
                      ).isoformat(timespec="minutes")
    elif nc_row["contencion_con_descargo"]:
        contencion = f"{descargo.isoformat()}T23:59"
    return {"fecha_limite_descargo": descargo.isoformat(),
            "fecha_limite_contencion": contencion}


def limite_implementacion(nc_row) -> date | None:
    if not nc_row["plazo_impl_max_dias"] or not nc_row["recepcion_ts"]:
        return None
    return (datetime.fromisoformat(nc_row["recepcion_ts"]).date()
            + timedelta(days=nc_row["plazo_impl_max_dias"]))


def vencimientos(nc_row, ahora: datetime | None = None, compromiso: str | None = None,
                 verificacion: str | None = None) -> list[str]:
    """Badges de texto para Seguimiento (sin colores: el color es la severidad).
    `compromiso`: fecha compromiso más próxima entre las acciones sin implementar;
    `verificacion`: fecha programada de la verificación de eficacia."""
    ahora = ahora or datetime.now()
    hoy = ahora.date()
    out = []
    est = nc_row["estado"]
    if est == "ENVIADA":
        if nc_row["fecha_limite_contencion"]:
            lim = datetime.fromisoformat(nc_row["fecha_limite_contencion"])
            if ahora > lim:
                out.append(f"⏰ CONTENCIÓN VENCIDA · {(ahora - lim).days + 1} d")
        if nc_row["fecha_limite_descargo"]:
            lim = date.fromisoformat(nc_row["fecha_limite_descargo"])
            if hoy > lim:
                out.append(f"⏰ DESCARGO VENCIDO · {(hoy - lim).days} d")
            else:
                out.append(f"Descargo en {(lim - hoy).days} d")
    if est in ESTADOS_ACEPTADA and compromiso:
        lim = date.fromisoformat(compromiso)
        if hoy > lim:
            out.append(f"⏰ ACCIÓN VENCIDA · {(hoy - lim).days} d")
        else:
            out.append(f"Próxima acción en {(lim - hoy).days} d")
    if est in ESTADOS_VERIFICACION_PENDIENTE and verificacion:
        lim = date.fromisoformat(verificacion)
        if hoy > lim:
            out.append(f"⏰ VERIFICACIÓN VENCIDA · {(hoy - lim).days} d")
        else:
            out.append(f"Verificación en {(lim - hoy).days} d")
    return out


# ---------------------------------------------------------------------------
# Borradores
# ---------------------------------------------------------------------------

_CAMPOS_BORRADOR = (
    "codigodepo", "fecha", "sucursal_nombre", "area", "tipo", "merma_linea_base",
    "linea_base_unidad", "linea_base_fuente", "impactos", "responsable_accion",
    "solicitante", "intro", "req_codigo", "req_titulo", "req_texto", "sop_version_id",
    "sop_version", "evidencia_txt", "desvio_txt", "acciones_recomendadas",
)


def _normalizar(datos: dict) -> dict:
    d = {k: datos.get(k) for k in _CAMPOS_BORRADOR if k in datos}
    for k in ("impactos", "acciones_recomendadas"):
        if k in d and not isinstance(d[k], str):
            d[k] = _json(list(d[k] or []))
    if "fecha" in d and isinstance(d["fecha"], date):
        d["fecha"] = d["fecha"].isoformat()
    if "codigodepo" in d and d["codigodepo"] is not None:
        d["codigodepo"] = _depo3(d["codigodepo"])
    return d


def crear_borrador(proyecto, actor: Actor, datos: dict, origen_id: int | None = None,
                   reemplaza_a: int | None = None) -> int:
    _exigir(actor, "nc_emitir")
    d = _normalizar(datos)
    d.setdefault("fecha", date.today().isoformat())
    d.setdefault("solicitante", actor.nombre)
    d.setdefault("tipo", "AMARILLO")
    d.setdefault("codigodepo", "000")
    conn = conectar(proyecto)
    with _tx(conn):
        cols = list(d) + ["origen_id", "reemplaza_a", "creada_por", "creada_en"]
        vals = list(d.values()) + [origen_id, reemplaza_a, actor.username, _ahora()]
        nc_id = conn.execute(
            f"INSERT INTO nc ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals).lastrowid
        _evento(conn, "nc", nc_id, actor, "CREADA", None, "BORRADOR",
                {"origen_id": origen_id, "reemplaza_a": reemplaza_a})
    conn.close()
    return nc_id


def actualizar_borrador(proyecto, actor: Actor, nc_id: int, datos: dict) -> None:
    _exigir(actor, "nc_emitir")
    d = _normalizar(datos)
    if not d:
        return
    conn = conectar(proyecto)
    with _tx(conn):
        _estado(conn, nc_id, ("BORRADOR",))
        conn.execute(f"UPDATE nc SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?",
                     [*d.values(), nc_id])
    conn.close()


def eliminar_borrador(proyecto, actor: Actor, nc_id: int) -> None:
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        _estado(conn, nc_id, ("BORRADOR",))
        if conn.execute("SELECT 1 FROM nc_adjuntos WHERE nc_id = ?", (nc_id,)).fetchone():
            raise NCError("El borrador tiene adjuntos registrados: no se puede borrar.")
        conn.execute("DELETE FROM nc_anexos WHERE nc_id = ?", (nc_id,))
        conn.execute("DELETE FROM nc WHERE id = ?", (nc_id,))
        _evento(conn, "nc", nc_id, actor, "BORRADOR_ELIMINADO", "BORRADOR", None)
    conn.close()


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------

def _validar_para_emitir(row, cfg: dict) -> None:
    faltan = [n for n, k in (("sucursal", "codigodepo"), ("área", "area"),
                             ("tipo", "tipo"), ("requisito del SOP", "req_codigo"),
                             ("evidencia", "evidencia_txt"), ("desvío", "desvio_txt"),
                             ("responsable de la acción", "responsable_accion"))
              if not row[k] or row[k] == "000"]
    if faltan:
        raise NCError("Faltan datos para emitir: " + ", ".join(faltan) + ".")
    if row["tipo"] not in cfg["tipos"]:
        raise NCError(f"Tipo '{row['tipo']}' no está en la configuración.")


def contenido_canonico(nc: dict) -> str:
    """Contenido de la NC que identifica al documento (para `sha256_contenido`)."""
    claves = [c for c in _CONGELADAS if c not in
              ("html_emitido", "sha256_html", "sha256_contenido", "creada_en")]
    contenido = {k: nc.get(k) for k in claves}
    contenido["anexos"] = [[a["id"], a["sha256"], a["imprimir"]] for a in nc.get("anexos", [])]
    return _json(contenido)


def emitir(proyecto, actor: Actor, nc_id: int, render=None) -> str:
    """Asigna número, congela y renderiza. `render(nc_dict, cfg) -> html`."""
    _exigir(actor, "nc_emitir")
    if render is None:
        from core.reporte import generar_no_conformidad
        render = lambda nc, cfg: generar_no_conformidad(proyecto, nc, cfg)  # noqa: E731
    cfg = cargar_config(proyecto)
    conn = conectar(proyecto)
    try:
        return _emitir(conn, actor, nc_id, cfg, render)
    finally:
        conn.close()


def _emitir(conn, actor: Actor, nc_id: int, cfg: dict, render) -> str:
    for intento in range(2):
        try:
            with _tx(conn):
                row = _estado(conn, nc_id, ("BORRADOR",))
                _validar_para_emitir(row, cfg)
                f = date.fromisoformat(row["fecha"])
                orden = conn.execute(
                    "SELECT COALESCE(MAX(orden), 0) + 1 FROM nc "
                    "WHERE codigodepo = ? AND anio = ? AND mes = ?",
                    (row["codigodepo"], f.year, f.month)).fetchone()[0]
                if orden > 99:
                    raise NCError(f"La sucursal {row['codigodepo']} ya tiene 99 NC en "
                                  f"{f.month:02d}/{f.year}: el formato no admite más.")
                t = cfg["tipos"][row["tipo"]]
                nc = dict(row)
                nc.update({
                    "orden": orden, "mes": f.month, "anio": f.year,
                    "numero": f"{orden:02d}{row['codigodepo']}{f.month:02d}{f.year}",
                    "tipo_severidad": t["severidad"],
                    "plazo_descargo_dias": t["descargo_dias_habiles"],
                    "plazo_contencion_horas": t.get("contencion_horas"),
                    "contencion_con_descargo": 1 if t.get("contencion_con_descargo") else 0,
                    "plazo_impl_max_dias": t.get("implementacion_max_dias"),
                    "emitida_en": _ahora(),
                })
                nc["anexos"] = _anexos_de(conn, nc_id)
                nc["sha256_contenido"] = sha256_bytes(contenido_canonico(nc).encode("utf-8"))
                html = render(nc, cfg)
                nc["html_emitido"] = html
                nc["sha256_html"] = sha256_bytes(html.encode("utf-8"))
                sets = ("numero", "orden", "mes", "anio", "tipo_severidad",
                        "plazo_descargo_dias", "plazo_contencion_horas",
                        "contencion_con_descargo", "plazo_impl_max_dias", "emitida_en",
                        "sha256_contenido", "html_emitido", "sha256_html")
                conn.execute(
                    f"UPDATE nc SET {', '.join(f'{k} = ?' for k in sets)}, estado = 'EMITIDA'"
                    " WHERE id = ?", [nc[k] for k in sets] + [nc_id])
                _evento(conn, "nc", nc_id, actor, "EMITIDA", "BORRADOR", "EMITIDA",
                        {"numero": nc["numero"], "sha256_html": nc["sha256_html"],
                         "sha256_contenido": nc["sha256_contenido"],
                         "sop_version_id": row["sop_version_id"],
                         "anexos": [{"id": a["id"], "tipo": a["tipo"], "sha256": a["sha256"],
                                     "imprimir": a["imprimir"], "corte_datos": a["corte_datos"]}
                                    for a in nc["anexos"]]})
            return nc["numero"]
        except sqlite3.IntegrityError:
            if intento == 1:
                raise NCError("No se pudo asignar número (choque con otra emisión). "
                              "Reintentá.")
    raise AssertionError("inalcanzable")


def _html_verificado(conn, nc_id: int) -> tuple[sqlite3.Row, str]:
    row = _fila(conn, nc_id)
    if not row["html_emitido"]:
        raise NCError("La NC todavía no fue emitida.")
    ev = conn.execute("SELECT datos FROM nc_eventos WHERE entidad = 'nc' AND entidad_id = ?"
                      " AND tipo = 'EMITIDA' ORDER BY id LIMIT 1", (nc_id,)).fetchone()
    esperado = json.loads(ev["datos"])["sha256_html"] if ev else None
    if sha256_bytes(row["html_emitido"].encode("utf-8")) != esperado:
        raise NCError(f"El HTML guardado de la NC {row['numero']} no coincide con el hash "
                      "registrado al emitir. No se usa: revisar integridad.")
    return row, row["html_emitido"]


def html_verificado(proyecto, nc_id: int) -> str:
    """HTML congelado validado contra el evento EMITIDA (sin registrar reimpresión)."""
    conn = conectar(proyecto)
    try:
        return _html_verificado(conn, nc_id)[1]
    finally:
        conn.close()


def html_para_reimprimir(proyecto, actor: Actor, nc_id: int) -> str:
    """HTML congelado, validado contra el hash guardado en el evento EMITIDA."""
    conn = conectar(proyecto)
    try:
        row, _ = _html_verificado(conn, nc_id)
    except NCError:
        conn.close()
        raise
    with _tx(conn):
        _evento(conn, "nc", nc_id, actor, "REIMPRESA", row["estado"], row["estado"])
    conn.close()
    return row["html_emitido"]


# ---------------------------------------------------------------------------
# Anexos (contenido generado por core/nc_anexos.py)
# ---------------------------------------------------------------------------

def _anexos_de(conn, nc_id: int) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM nc_anexos WHERE nc_id = ? ORDER BY orden, id", (nc_id,))]
    for r in rows:
        r["params"] = json.loads(r["params"])
        r["resumen"] = json.loads(r["resumen"]) if r["resumen"] else {}
    return rows


def anexos(proyecto, nc_id: int) -> list[dict]:
    conn = conectar(proyecto)
    try:
        return _anexos_de(conn, nc_id)
    finally:
        conn.close()


def agregar_anexo(proyecto, actor: Actor, nc_id: int, anexo: dict,
                  imprimir: bool = True) -> int:
    """Guarda un anexo ya generado (dict de `nc_anexos.generar`) en un borrador."""
    _exigir(actor, "nc_emitir")
    h = sha256_bytes(anexo["html"].encode("utf-8"))
    conn = conectar(proyecto)
    with _tx(conn):
        _estado(conn, nc_id, ("BORRADOR",))
        orden = conn.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM nc_anexos WHERE nc_id = ?",
                             (nc_id,)).fetchone()[0]
        aid = conn.execute(
            "INSERT INTO nc_anexos (nc_id, orden, tipo, titulo, params, imprimir, html, sha256,"
            " resumen, corte_datos, generado_por, generado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (nc_id, orden, anexo["tipo"], anexo["titulo"], _json(anexo["params"]),
             1 if imprimir else 0, anexo["html"], h, _json(anexo.get("resumen") or {}),
             anexo.get("corte_datos"), actor.username, _ahora())).lastrowid
        _evento(conn, "nc", nc_id, actor, "ANEXO_AGREGADO", "BORRADOR", "BORRADOR",
                {"anexo_id": aid, "tipo": anexo["tipo"], "sha256": h,
                 "corte_datos": anexo.get("corte_datos")})
    conn.close()
    return aid


def quitar_anexo(proyecto, actor: Actor, anexo_id: int) -> None:
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        r = conn.execute("SELECT nc_id, tipo FROM nc_anexos WHERE id = ?", (anexo_id,)).fetchone()
        if r is None:
            raise NCError("El anexo no existe.")
        _estado(conn, r["nc_id"], ("BORRADOR",))
        conn.execute("DELETE FROM nc_anexos WHERE id = ?", (anexo_id,))
        _evento(conn, "nc", r["nc_id"], actor, "ANEXO_QUITADO", "BORRADOR", "BORRADOR",
                {"anexo_id": anexo_id, "tipo": r["tipo"]})
    conn.close()


def marcar_anexo(proyecto, actor: Actor, anexo_id: int, imprimir: bool) -> None:
    """Imprimir (va en el documento) o interno (solo en el seguimiento)."""
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        r = conn.execute("SELECT nc_id FROM nc_anexos WHERE id = ?", (anexo_id,)).fetchone()
        if r is None:
            raise NCError("El anexo no existe.")
        _estado(conn, r["nc_id"], ("BORRADOR",))
        conn.execute("UPDATE nc_anexos SET imprimir = ? WHERE id = ?",
                     (1 if imprimir else 0, anexo_id))
    conn.close()


def verificar_anexos(proyecto) -> list[str]:
    """Cada anexo de una NC emitida tiene que coincidir con el hash del evento EMITIDA."""
    conn = conectar(proyecto)
    problemas = []
    for e in conn.execute("SELECT entidad_id, datos FROM nc_eventos WHERE entidad = 'nc'"
                          " AND tipo = 'EMITIDA'"):
        esperados = {a["id"]: a["sha256"] for a in json.loads(e["datos"]).get("anexos", [])}
        actuales = {r["id"]: r for r in conn.execute(
            "SELECT id, html, titulo FROM nc_anexos WHERE nc_id = ?", (e["entidad_id"],))}
        for aid, h in esperados.items():
            r = actuales.get(aid)
            if r is None:
                problemas.append(f"NC #{e['entidad_id']}: falta el anexo {aid}.")
            elif sha256_bytes(r["html"].encode("utf-8")) != h:
                problemas.append(f"NC #{e['entidad_id']}: el anexo «{r['titulo']}» fue modificado.")
        for aid in set(actuales) - set(esperados):
            problemas.append(f"NC #{e['entidad_id']}: anexo {aid} agregado después de emitir.")
    conn.close()
    return problemas


# ---------------------------------------------------------------------------
# Adjuntos
# ---------------------------------------------------------------------------

def _guardar_adjunto(conn, proyecto, actor: Actor, nc_row, etapa: str, nombre: str,
                     contenido: bytes, respuesta_id: int | None = None) -> int:
    """Escribe el archivo y lo registra. Llamar dentro de `_tx`."""
    h = sha256_bytes(contenido)
    carpeta = dir_adjuntos(proyecto) / (nc_row["numero"] or f"borrador-{nc_row['id']}")
    carpeta.mkdir(parents=True, exist_ok=True)
    seguro = "".join(c if c.isalnum() or c in "._- " else "_" for c in nombre)[-120:]
    destino = carpeta / f"{etapa}_{h[:12]}_{seguro}"
    if not destino.exists():
        destino.write_bytes(contenido)
    rel = destino.relative_to(dir_proyecto(proyecto)).as_posix()
    return conn.execute(
        "INSERT INTO nc_adjuntos (nc_id, etapa, respuesta_id, nombre, ruta, sha256,"
        " subido_por, subido_en) VALUES (?,?,?,?,?,?,?,?)",
        (nc_row["id"], etapa, respuesta_id, nombre, rel, h, actor.username, _ahora())
    ).lastrowid


def agregar_adjunto(proyecto, actor: Actor, nc_id: int, etapa: str, nombre: str,
                    contenido: bytes) -> int:
    """Adjunto suelto (evidencia del hallazgo, mientras es borrador o emitida)."""
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        row = _fila(conn, nc_id)
        if row["estado"] in ESTADOS_FINALES:
            raise NCError("La NC está cerrada o anulada.")
        aid = _guardar_adjunto(conn, proyecto, actor, row, etapa, nombre, contenido)
        _evento(conn, "nc", nc_id, actor, "ADJUNTO", row["estado"], row["estado"],
                {"adjunto_id": aid, "etapa": etapa, "nombre": nombre,
                 "sha256": sha256_bytes(contenido)})
    conn.close()
    return aid


def adjuntos(proyecto, nc_id: int) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM nc_adjuntos WHERE nc_id = ? ORDER BY id", (nc_id,))]
    conn.close()
    return rows


def leer_adjunto(proyecto, adj: dict) -> bytes:
    return (dir_proyecto(proyecto) / adj["ruta"]).read_bytes()


def verificar_adjuntos(proyecto) -> list[str]:
    conn = conectar(proyecto)
    problemas = []
    for r in conn.execute("SELECT * FROM nc_adjuntos ORDER BY id"):
        p = dir_proyecto(proyecto) / r["ruta"]
        if not p.exists():
            problemas.append(f"Adjunto {r['id']} ({r['nombre']}): falta el archivo.")
        elif sha256_bytes(p.read_bytes()) != r["sha256"]:
            problemas.append(f"Adjunto {r['id']} ({r['nombre']}): el archivo fue modificado.")
    conn.close()
    return problemas


def verificar_html_emitidos(proyecto) -> list[str]:
    conn = conectar(proyecto)
    problemas = []
    for r in conn.execute(
            "SELECT n.id, n.numero, n.html_emitido, e.datos FROM nc n JOIN nc_eventos e"
            " ON e.entidad = 'nc' AND e.entidad_id = n.id AND e.tipo = 'EMITIDA'"):
        if sha256_bytes(r["html_emitido"].encode("utf-8")) != json.loads(r["datos"])["sha256_html"]:
            problemas.append(f"NC {r['numero']}: el HTML no coincide con el hash de emisión.")
    conn.close()
    return problemas


# ---------------------------------------------------------------------------
# Envío, respuesta, observación, verificación, anulación
# ---------------------------------------------------------------------------

def registrar_envio(proyecto, actor: Actor, nc_id: int, medio: str, destinatario: str,
                    recepcion: datetime, adjunto: tuple[str, bytes] | None = None) -> dict:
    """Notificación a la sucursal: fija recepción y plazos concretos."""
    _exigir(actor, "nc_emitir")
    if not medio or not destinatario:
        raise NCError("Medio y destinatario son obligatorios.")
    cfg = cargar_config(proyecto)
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ("EMITIDA",))
        plazos, _ = _registrar_envio(conn, proyecto, actor, row, recepcion, cfg,
                                     {"medio": medio, "destinatario": destinatario}, adjunto)
    conn.close()
    return plazos


def _registrar_envio(conn, proyecto, actor: Actor, row, recepcion: datetime, cfg: dict,
                     datos: dict, adjunto: tuple[str, bytes] | None) -> tuple[dict, int | None]:
    """EMITIDA → ENVIADA con plazos desde `recepcion`. Llamar dentro de `_tx`."""
    if recepcion.date() < date.fromisoformat(row["fecha"]):
        raise NCError("La recepción no puede ser anterior a la fecha de la NC.")
    plazos = calcular_plazos(row, recepcion, cfg)
    conn.execute(
        "UPDATE nc SET estado = 'ENVIADA', recepcion_ts = ?, fecha_limite_contencion = ?,"
        " fecha_limite_descargo = ? WHERE id = ?",
        (recepcion.isoformat(timespec="minutes"), plazos["fecha_limite_contencion"],
         plazos["fecha_limite_descargo"], row["id"]))
    datos = {**datos, "recepcion": recepcion.isoformat(timespec="minutes"), **plazos}
    adj_id = None
    if adjunto:
        adj_id = _guardar_adjunto(conn, proyecto, actor, row, "envio", *adjunto)
        datos["adjunto_id"] = adj_id
        datos["adjunto_sha256"] = sha256_bytes(adjunto[1])
    _evento(conn, "nc", row["id"], actor, "ENVIADA", "EMITIDA", "ENVIADA", datos)
    return plazos, adj_id


def _insertar_envio(conn, nc_id: int, actor: Actor, envio: dict, resultado: str,
                    adjunto_id: int | None = None, error: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO nc_envios (nc_id, canal, remitente, para, cc, cco, asunto, cuerpo,"
        " adjunto_id, message_id, resultado, error, enviado_por, enviado_en)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (nc_id, envio["canal"], envio.get("remitente"), ", ".join(envio["para"]),
         ", ".join(envio.get("cc") or []), ", ".join(envio.get("cco") or []),
         envio.get("asunto"), envio.get("cuerpo"), adjunto_id, envio.get("message_id"),
         resultado, error, actor.username, _ahora())).lastrowid


def registrar_envio_mail(proyecto, actor: Actor, nc_id: int, envio: dict,
                         adjunto: tuple[str, bytes], enviado: datetime) -> dict | None:
    """Mail aceptado por el servidor. Si la NC estaba EMITIDA pasa a ENVIADA con
    los plazos desde el envío; si ya estaba notificada, queda como reenvío."""
    _exigir(actor, "nc_emitir")
    cfg = cargar_config(proyecto)
    resumen = {"canal": "mail", "para": envio["para"], "cc": envio.get("cc") or [],
               "cco": envio.get("cco") or [], "asunto": envio["asunto"],
               "message_id": envio.get("message_id"),
               "sha256_cuerpo": sha256_bytes((envio.get("cuerpo") or "").encode("utf-8"))}
    conn = conectar(proyecto)
    plazos = None
    with _tx(conn):
        row = _fila(conn, nc_id)
        if row["estado"] == "EMITIDA":
            plazos, adj_id = _registrar_envio(conn, proyecto, actor, row, enviado, cfg,
                                              {"medio": "Correo electrónico", **resumen}, adjunto)
            eid = _insertar_envio(conn, nc_id, actor, envio, "OK", adj_id)
        elif row["estado"] in ESTADOS_REENVIO:
            adj_id = _guardar_adjunto(conn, proyecto, actor, row, "envio", *adjunto)
            eid = _insertar_envio(conn, nc_id, actor, envio, "OK", adj_id)
            _evento(conn, "nc", nc_id, actor, "MAIL_REENVIADO", row["estado"], row["estado"],
                    {**resumen, "envio_id": eid, "adjunto_id": adj_id,
                     "adjunto_sha256": sha256_bytes(adjunto[1])})
        else:
            raise NCError(f"La NC está en {row['estado']}: no se registra el envío.")
    conn.close()
    return plazos


def registrar_envio_fallido(proyecto, actor: Actor, nc_id: int, envio: dict, error: str) -> None:
    conn = conectar(proyecto)
    with _tx(conn):
        row = _fila(conn, nc_id)
        eid = _insertar_envio(conn, nc_id, actor, envio, "ERROR", error=error[:500])
        _evento(conn, "nc", nc_id, actor, "ENVIO_FALLIDO", row["estado"], row["estado"],
                {"canal": envio["canal"], "envio_id": eid, "error": error[:500]})
    conn.close()


def envios(proyecto, nc_id: int) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM nc_envios WHERE nc_id = ? ORDER BY id", (nc_id,))]
    conn.close()
    return rows


def registrar_respuesta(proyecto, actor: Actor, nc_id: int, correccion: str,
                        causa_raiz: str, categoria_6m: str, acciones: list[dict],
                        adjunto: tuple[str, bytes] | None = None) -> list[str]:
    """Descargo de la sucursal (transcripto). Devuelve avisos de plazo."""
    _exigir(actor, "nc_responder")
    if not (correccion or "").strip() or not (causa_raiz or "").strip():
        raise NCError("Corrección inmediata y causa raíz son obligatorias.")
    if categoria_6m not in CATEGORIAS_6M:
        raise NCError("Elegí la categoría 6M de la causa raíz.")
    acciones = [a for a in acciones if (a.get("descripcion") or "").strip()]
    if not acciones:
        raise NCError("Cargá al menos una acción correctiva.")
    avisos = []
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ("ENVIADA",))
        lim = limite_implementacion(row)
        for a in acciones:
            if not a.get("responsable") or not a.get("fecha_compromiso"):
                raise NCError("Cada acción necesita responsable y fecha compromiso.")
            fc = a["fecha_compromiso"]
            fc = fc if isinstance(fc, date) else date.fromisoformat(str(fc))
            a["fecha_compromiso"] = fc.isoformat()
            if lim and fc > lim:
                avisos.append(f"La acción «{a['descripcion'][:40]}» vence el {fc:%d/%m/%Y}, "
                              f"después del máximo de implementación ({lim:%d/%m/%Y}).")
        intento = conn.execute("SELECT COALESCE(MAX(intento), 0) + 1 FROM nc_respuesta"
                               " WHERE nc_id = ?", (nc_id,)).fetchone()[0]
        rid = conn.execute(
            "INSERT INTO nc_respuesta (nc_id, intento, correccion, causa_raiz, categoria_6m,"
            " cargada_por, cargada_en) VALUES (?,?,?,?,?,?,?)",
            (nc_id, intento, correccion.strip(), causa_raiz.strip(), categoria_6m,
             actor.username, _ahora())).lastrowid
        for a in acciones:
            conn.execute("INSERT INTO nc_acciones_correctivas (respuesta_id, descripcion,"
                         " responsable, fecha_compromiso) VALUES (?,?,?,?)",
                         (rid, a["descripcion"].strip(), a["responsable"], a["fecha_compromiso"]))
        datos = {"respuesta_id": rid, "intento": intento, "categoria_6m": categoria_6m,
                 "acciones": len(acciones), "avisos": avisos}
        if adjunto:
            datos["adjunto_id"] = _guardar_adjunto(conn, proyecto, actor, row, "respuesta",
                                                   *adjunto, respuesta_id=rid)
        conn.execute("UPDATE nc SET estado = 'RESPONDIDA' WHERE id = ?", (nc_id,))
        _evento(conn, "nc", nc_id, actor, "RESPONDIDA", "ENVIADA", "RESPONDIDA", datos)
    conn.close()
    return avisos


def observar_descargo(proyecto, actor: Actor, nc_id: int, motivo: str,
                      adjunto: tuple[str, bytes] | None = None) -> str:
    """Descargo rechazado: vuelve a ENVIADA con plazo reabierto. El intento queda intacto."""
    _exigir(actor, "nc_emitir")
    if not (motivo or "").strip():
        raise NCError("El motivo de la observación es obligatorio.")
    cfg = cargar_config(proyecto)
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ("RESPONDIDA",))
        ultima = conn.execute("SELECT id, intento FROM nc_respuesta WHERE nc_id = ?"
                              " ORDER BY intento DESC LIMIT 1", (nc_id,)).fetchone()
        nuevo = sumar_dias_habiles(date.today(), row["plazo_descargo_dias"], cfg,
                                   row["codigodepo"]).isoformat()
        conn.execute("UPDATE nc SET estado = 'ENVIADA', fecha_limite_descargo = ? WHERE id = ?",
                     (nuevo, nc_id))
        datos = {"motivo": motivo.strip(), "respuesta_id": ultima["id"],
                 "intento": ultima["intento"],
                 "fecha_limite_descargo_anterior": row["fecha_limite_descargo"],
                 "fecha_limite_descargo_nueva": nuevo}
        if adjunto:
            datos["adjunto_id"] = _guardar_adjunto(conn, proyecto, actor, row, "observacion",
                                                   *adjunto, respuesta_id=ultima["id"])
        _evento(conn, "nc", nc_id, actor, "DESCARGO_OBSERVADO", "RESPONDIDA", "ENVIADA", datos)
    conn.close()
    return nuevo


def aceptar_descargo(proyecto, actor: Actor, nc_id: int) -> None:
    """El auditor acepta el descargo: las acciones quedan en implementación (ACEPTADA)."""
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        _estado(conn, nc_id, ("RESPONDIDA",))
        ultima = conn.execute("SELECT id, intento FROM nc_respuesta WHERE nc_id = ?"
                              " ORDER BY intento DESC LIMIT 1", (nc_id,)).fetchone()
        conn.execute("UPDATE nc SET estado = 'ACEPTADA' WHERE id = ?", (nc_id,))
        _evento(conn, "nc", nc_id, actor, "DESCARGO_ACEPTADO", "RESPONDIDA",
                "ACEPTADA", {"respuesta_id": ultima["id"], "intento": ultima["intento"]})
    conn.close()


# ---------------------------------------------------------------------------
# Implementación de acciones y cierre con la sucursal
# ---------------------------------------------------------------------------

def dias_verificacion(cfg: dict, tipo: str) -> int:
    """Días sugeridos entre el cierre con la sucursal y la verificación de eficacia."""
    t = cfg.get("tipos", {}).get(tipo) or {}
    return int(t.get("verificacion_dias")
               or CONFIG_DEFAULT["tipos"].get(tipo, {}).get("verificacion_dias") or 60)


def criterio_sugerido(nc_row) -> str:
    """Texto inicial del criterio de verificación, con la línea base si la NC la tiene."""
    base = nc_row["merma_linea_base"]
    if base is None:
        return ("Repetir el control que originó la NC y confirmar que el desvío no se "
                "repite.")
    unidad = nc_row["linea_base_unidad"] or ""
    valor = f"$ {base:,.0f}" if unidad == "$" else f"{base:,.2f} {unidad}".strip()
    fuente = f" ({nc_row['linea_base_fuente']})" if nc_row["linea_base_fuente"] else ""
    return (f"Merma de la sucursal en el próximo inventario comparada con la línea base "
            f"{valor}{fuente}: tiene que ser menor.")


def _respuesta_aceptada(conn, nc_id: int) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM nc_respuesta WHERE nc_id = ? ORDER BY intento DESC"
                     " LIMIT 1", (nc_id,)).fetchone()
    if r is None:
        raise NCError("La NC no tiene descargo cargado.")
    return r


def _acciones_impl(conn, respuesta_id: int) -> list[dict]:
    out = []
    for a in conn.execute("SELECT * FROM nc_acciones_correctivas WHERE respuesta_id = ?"
                          " ORDER BY id", (respuesta_id,)):
        d = dict(a)
        imp = conn.execute("SELECT * FROM nc_implementacion WHERE accion_id = ?",
                           (a["id"],)).fetchone()
        d["implementacion"] = dict(imp) if imp else None
        out.append(d)
    return out


def acciones_con_implementacion(proyecto, nc_id: int) -> list[dict]:
    """Acciones del descargo aceptado (último intento) con su implementación o None."""
    conn = conectar(proyecto)
    try:
        return _acciones_impl(conn, _respuesta_aceptada(conn, nc_id)["id"])
    finally:
        conn.close()


def registrar_implementacion(proyecto, actor: Actor, nc_id: int, accion_id: int,
                             fecha_real: date, evidencia_txt: str,
                             adjunto: tuple[str, bytes] | None = None) -> None:
    """La sucursal informa que implementó una acción correctiva (se transcribe)."""
    _exigir(actor, "nc_responder")
    if not (evidencia_txt or "").strip():
        raise NCError("Describí la evidencia de la implementación.")
    if fecha_real > date.today():
        raise NCError("La fecha de implementación no puede ser futura.")
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ESTADOS_ACEPTADA)
        resp = _respuesta_aceptada(conn, nc_id)
        accion = conn.execute("SELECT * FROM nc_acciones_correctivas WHERE id = ?",
                              (accion_id,)).fetchone()
        if accion is None or accion["respuesta_id"] != resp["id"]:
            raise NCError("La acción no pertenece al descargo aceptado de esta NC.")
        if row["recepcion_ts"] and fecha_real < datetime.fromisoformat(
                row["recepcion_ts"]).date():
            raise NCError("La implementación no puede ser anterior a la recepción de la NC.")
        if conn.execute("SELECT 1 FROM nc_implementacion WHERE accion_id = ?",
                        (accion_id,)).fetchone():
            raise NCError("Esa acción ya tiene la implementación registrada.")
        adj_id = None
        if adjunto:
            adj_id = _guardar_adjunto(conn, proyecto, actor, row, "respuesta", *adjunto,
                                      respuesta_id=resp["id"])
        conn.execute("INSERT INTO nc_implementacion (accion_id, fecha_real, evidencia_txt,"
                     " adjunto_id, registrada_por, registrada_en) VALUES (?,?,?,?,?,?)",
                     (accion_id, fecha_real.isoformat(), evidencia_txt.strip(), adj_id,
                      actor.username, _ahora()))
        _evento(conn, "nc", nc_id, actor, "ACCION_IMPLEMENTADA", row["estado"], row["estado"],
                {"accion_id": accion_id, "fecha_real": fecha_real.isoformat(),
                 "fuera_de_compromiso": fecha_real.isoformat() > accion["fecha_compromiso"],
                 "adjunto_id": adj_id})
    conn.close()


def _datos_acta(conn, row, resp, acciones, aclaraciones: str, fecha_verif: date,
                criterio: str, actor: Actor, ahora: str) -> dict:
    """Todo lo que va en el acta (y en su contenido canónico)."""
    acept = conn.execute("SELECT usuario, ts FROM nc_eventos WHERE entidad = 'nc' AND"
                         " entidad_id = ? AND tipo = 'DESCARGO_ACEPTADO' ORDER BY id DESC"
                         " LIMIT 1", (row["id"],)).fetchone()
    adjs = [dict(a) for a in conn.execute(
        "SELECT id, nombre, sha256, subido_en FROM nc_adjuntos"
        " WHERE nc_id = ? AND etapa = 'respuesta' AND respuesta_id = ? ORDER BY id",
        (row["id"], resp["id"]))]
    return {
        "nc": {k: row[k] for k in ("id", "numero", "codigodepo", "sucursal_nombre", "area",
                                   "tipo", "fecha", "req_codigo", "req_titulo", "sop_version",
                                   "desvio_txt", "recepcion_ts", "sha256_contenido")},
        "respuesta": {k: resp[k] for k in ("id", "intento", "correccion", "causa_raiz",
                                           "categoria_6m", "cargada_por", "cargada_en")},
        "intentos_previos": resp["intento"] - 1,
        "acciones": [{"id": a["id"], "descripcion": a["descripcion"],
                      "responsable": a["responsable"],
                      "fecha_compromiso": a["fecha_compromiso"],
                      "fecha_real": a["implementacion"]["fecha_real"],
                      "evidencia": a["implementacion"]["evidencia_txt"]} for a in acciones],
        "aceptado_por": acept["usuario"] if acept else None,
        "aceptado_en": acept["ts"] if acept else None,
        "adjuntos": adjs,
        "aclaraciones": (aclaraciones or "").strip(),
        "fecha_verificacion_programada": fecha_verif.isoformat(),
        "criterio_verificacion": criterio.strip(),
        "cerrada_por": actor.username,
        "cerrada_por_nombre": actor.nombre,
        "cerrada_en": ahora,
    }


def cerrar_con_sucursal(proyecto, actor: Actor, nc_id: int, fecha_verificacion: date,
                        criterio: str, aclaraciones: str = "", render=None) -> str:
    """Cierra el caso con la sucursal y congela el acta. Exige todas las acciones
    implementadas; la verificación de eficacia queda programada. Devuelve el HTML."""
    _exigir(actor, "nc_emitir")
    if not (criterio or "").strip():
        raise NCError("El criterio de verificación de eficacia es obligatorio.")
    if fecha_verificacion <= date.today():
        raise NCError("La verificación de eficacia tiene que programarse para después de hoy.")
    if render is None:
        from core.reporte import generar_acta_cierre
        render = lambda acta, cfg: generar_acta_cierre(proyecto, acta, cfg)  # noqa: E731
    cfg = cargar_config(proyecto)
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ESTADOS_ACEPTADA)
        resp = _respuesta_aceptada(conn, nc_id)
        acciones = _acciones_impl(conn, resp["id"])
        faltan = [a["descripcion"][:50] for a in acciones if a["implementacion"] is None]
        if faltan:
            raise NCError("Faltan implementar: " + "; ".join(faltan) + ".")
        ahora = _ahora()
        acta = _datos_acta(conn, row, resp, acciones, aclaraciones, fecha_verificacion,
                           criterio, actor, ahora)
        acta["sha256_contenido"] = sha256_bytes(_json(acta).encode("utf-8"))
        html = render(acta, cfg)
        h_html = sha256_bytes(html.encode("utf-8"))
        conn.execute(
            "INSERT INTO nc_cierre (nc_id, respuesta_id, aclaraciones,"
            " fecha_verificacion_programada, criterio_verificacion, html_acta, sha256_html,"
            " sha256_contenido, cerrada_por, cerrada_en) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (nc_id, resp["id"], acta["aclaraciones"], acta["fecha_verificacion_programada"],
             acta["criterio_verificacion"], html, h_html, acta["sha256_contenido"],
             actor.username, ahora))
        conn.execute("UPDATE nc SET estado = 'CERRADA_SUCURSAL' WHERE id = ?", (nc_id,))
        _evento(conn, "nc", nc_id, actor, "CERRADA_SUCURSAL", row["estado"],
                "CERRADA_SUCURSAL",
                {"respuesta_id": resp["id"], "sha256_html": h_html,
                 "sha256_contenido": acta["sha256_contenido"],
                 "fecha_verificacion_programada": acta["fecha_verificacion_programada"],
                 "acciones": [a["id"] for a in acciones]})
    conn.close()
    return html


def cierre(proyecto, nc_id: int) -> dict | None:
    conn = conectar(proyecto)
    r = conn.execute("SELECT * FROM nc_cierre WHERE nc_id = ?", (nc_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def acta_verificada(proyecto, nc_id: int) -> str:
    """HTML del acta de cierre, validado contra el hash del evento CERRADA_SUCURSAL."""
    conn = conectar(proyecto)
    try:
        r = conn.execute("SELECT html_acta FROM nc_cierre WHERE nc_id = ?",
                         (nc_id,)).fetchone()
        if r is None:
            raise NCError("La NC no tiene acta de cierre.")
        ev = conn.execute("SELECT datos FROM nc_eventos WHERE entidad = 'nc' AND entidad_id = ?"
                          " AND tipo = 'CERRADA_SUCURSAL' ORDER BY id LIMIT 1",
                          (nc_id,)).fetchone()
        esperado = json.loads(ev["datos"])["sha256_html"] if ev else None
        if sha256_bytes(r["html_acta"].encode("utf-8")) != esperado:
            raise NCError("El acta de cierre guardada no coincide con el hash registrado al "
                          "cerrar. No se usa: revisar integridad.")
        return r["html_acta"]
    finally:
        conn.close()


def verificar_actas(proyecto) -> list[str]:
    conn = conectar(proyecto)
    problemas = []
    for r in conn.execute(
            "SELECT n.numero, c.html_acta, e.datos FROM nc_cierre c JOIN nc n ON n.id = c.nc_id"
            " LEFT JOIN nc_eventos e ON e.entidad = 'nc' AND e.entidad_id = c.nc_id"
            " AND e.tipo = 'CERRADA_SUCURSAL'"):
        esperado = json.loads(r["datos"])["sha256_html"] if r["datos"] else None
        if sha256_bytes(r["html_acta"].encode("utf-8")) != esperado:
            problemas.append(f"NC {r['numero']}: el acta de cierre no coincide con su hash.")
    conn.close()
    return problemas


def registrar_envio_cierre(proyecto, actor: Actor, nc_id: int, envio: dict,
                           adjunto: tuple[str, bytes]) -> None:
    """Acta de cierre enviada por mail (aceptada por el servidor). No cambia el estado."""
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        row = _estado(conn, nc_id, ESTADOS_CON_ACTA)
        adj_id = _guardar_adjunto(conn, proyecto, actor, row, "envio", *adjunto)
        eid = _insertar_envio(conn, nc_id, actor, envio, "OK", adj_id)
        _evento(conn, "nc", nc_id, actor, "ACTA_CIERRE_ENVIADA", row["estado"], row["estado"],
                {"canal": envio["canal"], "para": envio["para"], "cc": envio.get("cc") or [],
                 "cco": envio.get("cco") or [], "asunto": envio["asunto"],
                 "message_id": envio.get("message_id"), "envio_id": eid,
                 "adjunto_id": adj_id, "adjunto_sha256": sha256_bytes(adjunto[1]),
                 "sha256_cuerpo": sha256_bytes((envio.get("cuerpo") or "").encode("utf-8"))})
    conn.close()


def verificar_eficacia(proyecto, actor: Actor, nc_id: int, fecha: date, resultado: str,
                       evidencia_txt: str, adjunto: tuple[str, bytes] | None = None
                       ) -> int | None:
    """Verificación de eficacia de una NC cerrada con la sucursal: la cierra para el SGC.
    Si NO_EFICAZ, devuelve el id de la NC nueva (borrador)."""
    _exigir(actor, "nc_verificar")
    if resultado not in ("EFICAZ", "NO_EFICAZ"):
        raise NCError("Resultado inválido.")
    if not (evidencia_txt or "").strip():
        raise NCError("La evidencia de la verificación es obligatoria.")
    cfg = cargar_config(proyecto)
    conn = conectar(proyecto)
    nueva = None
    with _tx(conn):
        row = _estado(conn, nc_id, ESTADOS_VERIFICACION_PENDIENTE)
        quien_respondio = conn.execute(
            "SELECT cargada_por FROM nc_respuesta WHERE nc_id = ? ORDER BY intento DESC"
            " LIMIT 1", (nc_id,)).fetchone()["cargada_por"]
        regla = bool(cfg.get("verificador_distinto", True))
        if regla and quien_respondio == actor.username:
            raise NCError("Quien verifica la eficacia no puede ser quien cargó la respuesta.")
        conn.execute("INSERT INTO nc_verificacion (nc_id, verificador, fecha, resultado,"
                     " evidencia_txt, registrada_en) VALUES (?,?,?,?,?,?)",
                     (nc_id, actor.username, fecha.isoformat(), resultado,
                      evidencia_txt.strip(), _ahora()))
        destino = "CERRADA" if resultado == "EFICAZ" else "CERRADA_NO_EFICAZ"
        datos = {"resultado": resultado, "fecha": fecha.isoformat(),
                 "verificador_distinto": regla,
                 "mismo_usuario_que_respondio": quien_respondio == actor.username}
        if adjunto:
            datos["adjunto_id"] = _guardar_adjunto(conn, proyecto, actor, row,
                                                   "verificacion", *adjunto)
        conn.execute("UPDATE nc SET estado = ? WHERE id = ?", (destino, nc_id))
        if resultado == "NO_EFICAZ":
            copia = {k: row[k] for k in ("codigodepo", "sucursal_nombre", "area", "tipo",
                                         "impactos", "responsable_accion", "req_codigo",
                                         "req_titulo", "req_texto", "sop_version_id",
                                         "sop_version")}
            copia.update({"fecha": date.today().isoformat(), "solicitante": actor.nombre,
                          "evidencia_txt": f"Acción correctiva no eficaz de la NC "
                                           f"{numero_impreso(row['numero'])}: "
                                           f"{evidencia_txt.strip()}"})
            cols = list(copia) + ["origen_id", "creada_por", "creada_en"]
            nueva = conn.execute(
                f"INSERT INTO nc ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                list(copia.values()) + [nc_id, actor.username, _ahora()]).lastrowid
            datos["nc_nueva_id"] = nueva
            _evento(conn, "nc", nueva, actor, "CREADA", None, "BORRADOR",
                    {"origen_id": nc_id, "motivo": "verificación no eficaz"})
        _evento(conn, "nc", nc_id, actor, "VERIFICADA", row["estado"], destino, datos)
    conn.close()
    return nueva


def anular(proyecto, actor: Actor, nc_id: int, motivo: str, aviso_sucursal: str = "",
           reemitir: bool = False) -> int | None:
    """Anula (motivo obligatorio). Después de ENVIADA exige `nc_anular` y cómo se avisó.
    Con `reemitir`, crea un borrador copia que guarda `reemplaza_a`."""
    if not (motivo or "").strip():
        raise NCError("El motivo de la anulación es obligatorio.")
    conn = conectar(proyecto)
    nueva = None
    with _tx(conn):
        row = _estado(conn, nc_id, ESTADOS_ABIERTOS)
        if row["estado"] == "EMITIDA":
            if not (actor.tiene("nc_emitir") or actor.tiene("nc_anular")):
                raise NCError("No tenés permiso para anular.")
        else:
            _exigir(actor, "nc_anular")
            if not (aviso_sucursal or "").strip():
                raise NCError("La NC ya fue notificada: registrá cómo se le avisó a la "
                              "sucursal de la anulación.")
        conn.execute("UPDATE nc SET estado = 'ANULADA' WHERE id = ?", (nc_id,))
        datos = {"motivo": motivo.strip(), "aviso_sucursal": aviso_sucursal.strip()}
        if reemitir:
            copia = {k: row[k] for k in _CAMPOS_BORRADOR}
            copia["fecha"] = date.today().isoformat()
            cols = list(copia) + ["reemplaza_a", "creada_por", "creada_en"]
            nueva = conn.execute(
                f"INSERT INTO nc ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                list(copia.values()) + [nc_id, actor.username, _ahora()]).lastrowid
            datos["reemplazada_por"] = nueva
            conn.execute(
                "INSERT INTO nc_anexos (nc_id, orden, tipo, titulo, params, imprimir, html, sha256,"
                " resumen, corte_datos, generado_por, generado_en) SELECT ?, orden, tipo, titulo,"
                " params, imprimir, html, sha256, resumen, corte_datos, generado_por, generado_en"
                " FROM nc_anexos WHERE nc_id = ? ORDER BY orden", (nueva, nc_id))
            _evento(conn, "nc", nueva, actor, "CREADA", None, "BORRADOR", {"reemplaza_a": nc_id})
        _evento(conn, "nc", nc_id, actor, "ANULADA", row["estado"], "ANULADA", datos)
    conn.close()
    return nueva


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def obtener(proyecto, nc_id: int) -> dict:
    conn = conectar(proyecto)
    row = dict(_fila(conn, nc_id))
    conn.close()
    for k in ("impactos", "acciones_recomendadas"):
        row[k] = json.loads(row[k]) if row[k] else []
    return row


def respuestas(proyecto, nc_id: int) -> list[dict]:
    """Todos los intentos de descargo, con sus acciones y si fueron observados."""
    conn = conectar(proyecto)
    out = []
    observadas = {json.loads(e["datos"]).get("respuesta_id"): json.loads(e["datos"])
                  for e in conn.execute("SELECT datos FROM nc_eventos WHERE entidad = 'nc'"
                                        " AND entidad_id = ? AND tipo = 'DESCARGO_OBSERVADO'",
                                        (nc_id,))}
    for r in conn.execute("SELECT * FROM nc_respuesta WHERE nc_id = ? ORDER BY intento",
                          (nc_id,)):
        d = dict(r)
        d["acciones"] = [dict(a) for a in conn.execute(
            "SELECT * FROM nc_acciones_correctivas WHERE respuesta_id = ? ORDER BY id",
            (r["id"],))]
        obs = observadas.get(r["id"])
        d["observada"] = obs is not None
        d["motivo_observacion"] = obs["motivo"] if obs else None
        out.append(d)
    conn.close()
    return out


def verificacion(proyecto, nc_id: int) -> dict | None:
    conn = conectar(proyecto)
    r = conn.execute("SELECT * FROM nc_verificacion WHERE nc_id = ?", (nc_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def listar(proyecto, ahora: datetime | None = None) -> list[dict]:
    """Todas las NC con vencimientos derivados, más nuevas primero."""
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, numero, estado, fecha, codigodepo, sucursal_nombre, area, tipo,"
        " tipo_severidad, req_codigo, req_titulo, recepcion_ts, fecha_limite_contencion,"
        " fecha_limite_descargo, plazo_impl_max_dias, solicitante, reemplaza_a, origen_id,"
        " (SELECT MIN(a.fecha_compromiso) FROM nc_acciones_correctivas a JOIN nc_respuesta r"
        "   ON a.respuesta_id = r.id WHERE r.nc_id = nc.id AND r.intento ="
        "   (SELECT MAX(intento) FROM nc_respuesta WHERE nc_id = nc.id)"
        "   AND a.id NOT IN (SELECT accion_id FROM nc_implementacion)) AS compromiso_pendiente,"
        " (SELECT fecha_verificacion_programada FROM nc_cierre c WHERE c.nc_id = nc.id)"
        "   AS verificacion_programada"
        " FROM nc ORDER BY fecha DESC, id DESC")]
    conn.close()
    for r in rows:
        r["numero_impreso"] = numero_impreso(r["numero"])
        r["vencimientos"] = vencimientos(r, ahora, r["compromiso_pendiente"],
                                         r["verificacion_programada"])
    return rows


# ---------------------------------------------------------------------------
# Observaciones (SOP 8.1): registro simple, sin ciclo
# ---------------------------------------------------------------------------

def registrar_observacion(proyecto, actor: Actor, codigodepo, sucursal_nombre: str,
                          fecha: date, texto: str, req_codigo: str | None = None,
                          req_titulo: str | None = None,
                          sop_version_id: int | None = None) -> str:
    """Número `OBS-NNDDDMMAAAA`, con correlativo propio por sucursal y mes."""
    _exigir(actor, "nc_emitir")
    if not (texto or "").strip():
        raise NCError("El texto de la observación es obligatorio.")
    depo = _depo3(codigodepo)
    conn = conectar(proyecto)
    with _tx(conn):
        orden = conn.execute(
            "SELECT COALESCE(MAX(orden), 0) + 1 FROM observaciones"
            " WHERE codigodepo = ? AND anio = ? AND mes = ?",
            (depo, fecha.year, fecha.month)).fetchone()[0]
        if orden > 99:
            raise NCError("Ya hay 99 observaciones este mes para la sucursal.")
        numero = f"OBS-{orden:02d}{depo}{fecha.month:02d}{fecha.year}"
        oid = conn.execute(
            "INSERT INTO observaciones (numero, orden, codigodepo, mes, anio, fecha,"
            " sucursal_nombre, req_codigo, req_titulo, sop_version_id, texto, creada_por,"
            " creada_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (numero, orden, depo, fecha.month, fecha.year, fecha.isoformat(),
             sucursal_nombre, req_codigo, req_titulo, sop_version_id, texto.strip(),
             actor.username, _ahora())).lastrowid
        _evento(conn, "obs", oid, actor, "OBSERVACION", None, None,
                {"numero": numero, "sha256_texto": sha256_bytes(texto.strip().encode())})
    conn.close()
    return numero


def listar_observaciones(proyecto) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute("SELECT * FROM observaciones ORDER BY fecha DESC,"
                                          " id DESC")]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Backup (ISO 7.5.3): copia consistente de la base + zip de adjuntos
# ---------------------------------------------------------------------------

def backup(proyecto, destino: Path | None = None) -> tuple[Path, Path]:
    destino = destino or (dir_proyecto(proyecto) / "nc_backups")
    destino.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_out = destino / f"no_conformidades_{sello}.sqlite"
    src = conectar(proyecto)
    dst = sqlite3.connect(str(db_out))
    src.backup(dst)
    dst.close()
    src.close()
    zip_out = destino / f"nc_adjuntos_{sello}.zip"
    base = dir_adjuntos(proyecto)
    with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(base.parent).as_posix())
        sop_dir = dir_proyecto(proyecto) / "sop"
        if sop_dir.exists():
            for f in sop_dir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(sop_dir.parent).as_posix())
    return db_out, zip_out

