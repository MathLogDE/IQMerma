"""
core/nc_contactos.py — Agenda de contactos para notificar No Conformidades

Vive en el SQLite de NC (tabla `contactos`), así la pueden mantener los usuarios
con rol nc_emitir también desde la app del cliente (la pestaña Configuración es
solo de la app de administración). A diferencia del resto de las tablas de NC no
es evidencia de auditoría: se edita y se borra libremente; lo que importa (a
quién se mandó cada NC) queda en `nc_envios` y en la cadena de eventos.

Cada contacto tiene:
  - codigodepo: si se completa, el contacto es de esa sucursal (sus "por defecto"
    solo aplican a las NC de esa sucursal); vacío = aplica a todas.
  - grupo:      etiqueta libre ("Coordinación", "Encargados NOA"…) para agregar a
                varios de una vez.
  - por_defecto: '', 'para', 'cc' o 'cco' — sale tildado así al redactar.
  - usos / ultimo_uso: se actualizan en cada envío; ordenan "frecuentes primero".

La primera vez se importan la libreta y los contactos por sucursal que había en
nc_config.json (versión anterior de esta función).
"""

from __future__ import annotations

import re

from core import no_conformidades as nc

POR_DEFECTO = ("", "para", "cc", "cco")
_RX_MAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS contactos (
    id             INTEGER PRIMARY KEY,
    nombre         TEXT NOT NULL DEFAULT '',
    mail           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    codigodepo     TEXT,
    grupo          TEXT,
    por_defecto    TEXT NOT NULL DEFAULT '' CHECK (por_defecto IN ('','para','cc','cco')),
    activo         INTEGER NOT NULL DEFAULT 1,
    usos           INTEGER NOT NULL DEFAULT 0,
    ultimo_uso     TEXT,
    creado_por     TEXT,
    creado_en      TEXT
);
"""


def _conn(proyecto):
    conn = nc.conectar(proyecto)
    conn.executescript(SCHEMA)
    return conn


def _limpio(v) -> str | None:
    if v is None or v != v:          # None o NaN
        return None
    v = str(v).strip()
    return v or None


def _normalizar(c: dict) -> dict:
    mail = (_limpio(c.get("mail")) or "").lower()
    if not _RX_MAIL.match(mail):
        raise nc.NCError(f"Mail inválido: «{c.get('mail')}».")
    depo = _limpio(c.get("codigodepo"))
    pd_ = (_limpio(c.get("por_defecto")) or "").lower()
    if pd_ not in POR_DEFECTO:
        raise nc.NCError(f"«Por defecto» inválido para {mail}: use Para, CC o CCO.")
    activo = c.get("activo")
    return {"nombre": _limpio(c.get("nombre")) or "", "mail": mail,
            "codigodepo": depo.zfill(3) if depo and depo.isdigit() else depo,
            "grupo": _limpio(c.get("grupo")), "por_defecto": pd_,
            "activo": 0 if activo is False or activo == 0 else 1}


def _migrar(conn, proyecto) -> None:
    """Importa una sola vez la libreta y los contactos de nc_config.json."""
    if conn.execute("SELECT COUNT(*) FROM contactos").fetchone()[0]:
        return
    mail_cfg = nc.cargar_config(proyecto).get("mail") or {}
    filas = []
    for depo, cs in (mail_cfg.get("contactos_deposito") or {}).items():
        filas += [{"nombre": c.get("nombre"), "mail": c.get("mail"), "codigodepo": depo,
                   "por_defecto": "para"} for c in cs]
    for c in mail_cfg.get("libreta") or []:
        filas.append({"nombre": c.get("nombre"), "mail": c.get("mail"),
                      "por_defecto": "cc" if c.get("cc") else ("cco" if c.get("cco") else "")})
    for f in filas:
        try:
            c = _normalizar(f)
        except nc.NCError:
            continue
        conn.execute("INSERT OR IGNORE INTO contactos (nombre, mail, codigodepo, grupo,"
                     " por_defecto, activo, creado_por, creado_en)"
                     " VALUES (?,?,?,?,?,?,'migracion',?)",
                     (c["nombre"], c["mail"], c["codigodepo"], c["grupo"], c["por_defecto"],
                      c["activo"], nc._ahora()))


def listar(proyecto, solo_activos: bool = False) -> list[dict]:
    """Frecuentes primero (más usados, más recientes), después por nombre."""
    conn = _conn(proyecto)
    _migrar(conn, proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM contactos" + (" WHERE activo = 1" if solo_activos else "")
        + " ORDER BY usos DESC, ultimo_uso DESC, nombre COLLATE NOCASE, mail")]
    conn.close()
    return rows


def guardar_todos(proyecto, actor: nc.Actor, filas: list[dict]) -> int:
    """Reemplaza la agenda por `filas` (lo que quedó en el editor). Conserva usos."""
    if not actor.tiene("nc_emitir"):
        raise nc.NCError(f"El usuario {actor.username} no tiene el rol nc_emitir.")
    limpias, vistos = [], set()
    for f in filas:
        if not _limpio(f.get("mail")):
            continue
        c = _normalizar(f)
        if c["mail"] in vistos:
            raise nc.NCError(f"El mail {c['mail']} está repetido.")
        vistos.add(c["mail"])
        limpias.append(c)
    conn = _conn(proyecto)
    with nc._tx(conn):
        if vistos:
            conn.execute(f"DELETE FROM contactos WHERE mail NOT IN "
                         f"({','.join('?' * len(vistos))})", list(vistos))
        else:
            conn.execute("DELETE FROM contactos")
        for c in limpias:
            conn.execute(
                "INSERT INTO contactos (nombre, mail, codigodepo, grupo, por_defecto, activo,"
                " creado_por, creado_en) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(mail) DO UPDATE SET nombre = excluded.nombre,"
                " codigodepo = excluded.codigodepo, grupo = excluded.grupo,"
                " por_defecto = excluded.por_defecto, activo = excluded.activo",
                (c["nombre"], c["mail"], c["codigodepo"], c["grupo"], c["por_defecto"],
                 c["activo"], actor.username, nc._ahora()))
    conn.close()
    return len(limpias)


def agregar_nuevos(proyecto, actor: nc.Actor, destinatarios: list[dict]) -> list[str]:
    """Guarda como contacto los mails que todavía no están en la agenda."""
    conn = _conn(proyecto)
    nuevos = []
    with nc._tx(conn):
        for d in destinatarios:
            try:
                c = _normalizar({"nombre": d.get("nombre"), "mail": d.get("mail")})
            except nc.NCError:
                continue
            cur = conn.execute("INSERT OR IGNORE INTO contactos (nombre, mail, creado_por,"
                               " creado_en) VALUES (?,?,?,?)",
                               (c["nombre"], c["mail"], actor.username, nc._ahora()))
            if cur.rowcount:
                nuevos.append(c["mail"])
    conn.close()
    return nuevos


def registrar_uso(proyecto, mails: list[str]) -> None:
    if not mails:
        return
    conn = _conn(proyecto)
    with nc._tx(conn):
        conn.executemany("UPDATE contactos SET usos = usos + 1, ultimo_uso = ? WHERE mail = ?",
                         [(nc._ahora(), m.strip().lower()) for m in mails])
    conn.close()


def grupos(contactos: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in contactos:
        if c.get("grupo") and c.get("activo", 1):
            out.setdefault(c["grupo"], []).append(c)
    return dict(sorted(out.items()))


def sugeridos(proyecto, codigodepo: str) -> list[dict]:
    """Destinatarios por defecto para una NC de `codigodepo`: los contactos activos
    con 'por defecto', globales o de esa sucursal."""
    depo = str(codigodepo).zfill(3)
    filas = []
    for c in listar(proyecto, solo_activos=True):
        if not c["por_defecto"] or (c["codigodepo"] and c["codigodepo"] != depo):
            continue
        filas.append({"nombre": c["nombre"], "mail": c["mail"],
                      "para": c["por_defecto"] == "para", "cc": c["por_defecto"] == "cc",
                      "cco": c["por_defecto"] == "cco"})
    # los de la sucursal primero
    return sorted(filas, key=lambda f: not f["para"])
