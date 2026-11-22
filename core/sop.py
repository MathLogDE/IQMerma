"""
core/sop.py — SOP (Procedimiento de Operaciones Estandarizado) como fuente de criterios

Lee el .docx con la librería estándar (zipfile + XML), sin python-docx: el
número de cada artículo ("9.2.12") muchas veces NO está en el texto, lo genera
Word con la numeración automática de los estilos de título, y python-docx
tampoco lo resuelve. Dos estrategias, en orden:

  1. regex: títulos cuyo texto ya empieza con la numeración ("9.2.12 Cierre…").
  2. estilos: si la regex no encuentra nada, los títulos numerados por estilo
     (Heading N con w:numPr en styles.xml) se numeran contando por nivel, con el
     `w:start` de numbering.xml. Los párrafos de índice (TOC) se ignoran: el índice
     puede estar desactualizado respecto del cuerpo.

Cada versión se guarda en el SQLite de NC (core/no_conformidades.py) como
BORRADOR, se revisa en la UI y recién al activarla queda congelada (§7.5).
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from io import BytesIO
from xml.etree import ElementTree as ET

from core.no_conformidades import (
    Actor, NCError, _evento, _exigir, _tx, _ahora, conectar, dir_proyecto, sha256_bytes,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_RX_NUM = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*)$")


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------

def _estilos(z: zipfile.ZipFile) -> dict:
    """styleId -> {nombre, nivel (1..9) si es título, num: (numId, ilvl) | None}."""
    out = {}
    if "word/styles.xml" not in z.namelist():
        return out
    raiz = ET.fromstring(z.read("word/styles.xml"))
    crudos = {}
    for s in raiz.iter(W + "style"):
        sid = s.get(W + "styleId")
        nm = s.find(W + "name")
        nombre = nm.get(W + "val") if nm is not None else sid
        based = s.find(W + "basedOn")
        ppr = s.find(W + "pPr")
        num = None
        nivel_outline = None
        if ppr is not None:
            np_ = ppr.find(W + "numPr")
            if np_ is not None:
                ni, il = np_.find(W + "numId"), np_.find(W + "ilvl")
                num = (ni.get(W + "val") if ni is not None else None,
                       int(il.get(W + "val")) if il is not None else 0)
            ol = ppr.find(W + "outlineLvl")
            if ol is not None:
                nivel_outline = int(ol.get(W + "val")) + 1
        crudos[sid] = {"nombre": nombre, "num": num, "outline": nivel_outline,
                       "based": based.get(W + "val") if based is not None else None}
    for sid, c in crudos.items():
        # hereda numPr/outline del estilo base si no los define
        num, outline, b, vistos = c["num"], c["outline"], c["based"], set()
        while b and b in crudos and b not in vistos and (num is None or outline is None):
            vistos.add(b)
            num = num or crudos[b]["num"]
            outline = outline or crudos[b]["outline"]
            b = crudos[b]["based"]
        m = re.match(r"(?i)^heading\s*(\d)$", c["nombre"] or "")
        nivel = int(m.group(1)) if m else outline
        out[sid] = {"nombre": c["nombre"], "nivel": nivel, "num": num}
    return out


def _starts(z: zipfile.ZipFile) -> dict:
    """numId -> {ilvl: start}."""
    if "word/numbering.xml" not in z.namelist():
        return {}
    raiz = ET.fromstring(z.read("word/numbering.xml"))
    abstract = {}
    for a in raiz.iter(W + "abstractNum"):
        niveles = {}
        for lvl in a.iter(W + "lvl"):
            st = lvl.find(W + "start")
            niveles[int(lvl.get(W + "ilvl"))] = int(st.get(W + "val")) if st is not None else 1
        abstract[a.get(W + "abstractNumId")] = niveles
    out = {}
    for n in raiz.iter(W + "num"):
        an = n.find(W + "abstractNumId")
        niveles = dict(abstract.get(an.get(W + "val"), {})) if an is not None else {}
        for ov in n.iter(W + "lvlOverride"):
            so = ov.find(W + "startOverride")
            if so is not None:
                niveles[int(ov.get(W + "ilvl"))] = int(so.get(W + "val"))
        out[n.get(W + "numId")] = niveles
    return out


def _parrafos(z: zipfile.ZipFile, estilos: dict) -> list[dict]:
    doc = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in doc.iter(W + "p"):
        texto = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        ppr = p.find(W + "pPr")
        sid, num = None, None
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            sid = ps.get(W + "val") if ps is not None else None
            np_ = ppr.find(W + "numPr")
            if np_ is not None:
                ni, il = np_.find(W + "numId"), np_.find(W + "ilvl")
                num = (ni.get(W + "val") if ni is not None else None,
                       int(il.get(W + "val")) if il is not None else 0)
        est = estilos.get(sid, {"nombre": sid or "", "nivel": None, "num": None})
        out.append({"texto": texto, "estilo": est["nombre"] or "", "nivel": est["nivel"],
                    "num": num or est["num"]})
    return out


def _es_toc(p: dict) -> bool:
    return p["estilo"].lower().startswith("toc")


def parsear_docx(contenido: bytes) -> tuple[list[dict], str]:
    """Devuelve (artículos, método). Artículo: codigo, titulo, texto, padre_codigo,
    nivel, orden. Método: 'regex' | 'estilos'."""
    z = zipfile.ZipFile(BytesIO(contenido))
    estilos = _estilos(z)
    parrafos = [p for p in _parrafos(z, estilos) if not _es_toc(p)]

    # 1) regex sobre los títulos
    marcas = []
    for i, p in enumerate(parrafos):
        if p["nivel"] and p["texto"]:
            m = _RX_NUM.match(p["texto"])
            if m:
                marcas.append((i, m.group(1), m.group(2).strip()))
    metodo = "regex"

    # 2) numeración automática por estilo de título
    if not marcas:
        metodo = "estilos"
        starts = _starts(z)
        cont: list[int | None] = [None] * 9
        for i, p in enumerate(parrafos):
            if not p["nivel"] or not p["texto"] or not p["num"]:
                continue
            num_id, ilvl = p["num"]
            nivel = ilvl + 1 if ilvl is not None else p["nivel"]
            inicio = starts.get(num_id, {})
            for k in range(nivel - 1):          # niveles superiores sin abrir
                if cont[k] is None:
                    cont[k] = inicio.get(k, 1)
            cont[nivel - 1] = (inicio.get(nivel - 1, 1) if cont[nivel - 1] is None
                               else cont[nivel - 1] + 1)
            for k in range(nivel, 9):
                cont[k] = None
            codigo = ".".join(str(c) for c in cont[:nivel])
            marcas.append((i, codigo, p["texto"]))

    articulos = []
    for n, (i, codigo, titulo) in enumerate(marcas):
        fin = marcas[n + 1][0] if n + 1 < len(marcas) else len(parrafos)
        cuerpo = [parrafos[j]["texto"] for j in range(i + 1, fin) if parrafos[j]["texto"]]
        articulos.append({
            "codigo": codigo,
            "titulo": titulo.rstrip(":").strip(),
            "texto": "\n".join(cuerpo),
            "padre_codigo": codigo.rsplit(".", 1)[0] if "." in codigo else None,
            "nivel": codigo.count(".") + 1,
            "orden": n,
        })
    return articulos, metodo


def comparar_con_indice(contenido: bytes, articulos: list[dict]) -> list[str]:
    """Diferencias entre el índice (TOC) del documento y lo extraído del cuerpo.
    Útil en la revisión: un índice desactualizado aparece acá."""
    z = zipfile.ZipFile(BytesIO(contenido))
    por_codigo = {a["codigo"]: a["titulo"] for a in articulos}
    rx = re.compile(r"^(\d+(?:\.\d+)*)\.?\s*(.*?)\s*\d+$")
    difs = []
    for p in _parrafos(z, _estilos(z)):
        if _es_toc(p) and p["texto"]:
            m = rx.match(p["texto"])
            if not m:
                continue
            cod, tit = m.group(1), m.group(2).rstrip(":").strip()
            if por_codigo.get(cod) != tit:
                difs.append(f"Índice {cod} «{tit}» — cuerpo: «{por_codigo.get(cod, 'no existe')}»")
    return difs


# ---------------------------------------------------------------------------
# Versiones
# ---------------------------------------------------------------------------

def cargar_version(proyecto, actor: Actor, nombre_archivo: str, contenido: bytes,
                   version: str, vigencia_desde: date | None) -> tuple[int, str, int]:
    """Parsea y guarda una versión en BORRADOR. Devuelve (version_id, método, n_artículos)."""
    _exigir(actor, "nc_emitir")
    if not version.strip():
        raise NCError("Indicá el número de versión del SOP.")
    articulos, metodo = parsear_docx(contenido)
    if not articulos:
        raise NCError("No se encontraron artículos numerados en el documento.")
    h = sha256_bytes(contenido)
    carpeta = dir_proyecto(proyecto) / "sop"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{h[:12]}_{nombre_archivo}"
    destino.write_bytes(contenido)
    conn = conectar(proyecto)
    with _tx(conn):
        if conn.execute("SELECT 1 FROM sop_versiones WHERE sha256 = ? AND estado <> 'BORRADOR'",
                        (h,)).fetchone():
            raise NCError("Ese mismo archivo ya fue cargado y activado.")
        vid = conn.execute(
            "INSERT INTO sop_versiones (version, vigencia_desde, archivo, ruta, sha256,"
            " metodo_parseo, cargado_por, cargado_en) VALUES (?,?,?,?,?,?,?,?)",
            (version.strip(), vigencia_desde.isoformat() if vigencia_desde else None,
             nombre_archivo, destino.relative_to(dir_proyecto(proyecto)).as_posix(), h,
             metodo, actor.username, _ahora())).lastrowid
        conn.executemany(
            "INSERT INTO sop_articulos (version_id, codigo, titulo, texto, padre_codigo, nivel,"
            " orden) VALUES (?,?,?,?,?,?,?)",
            [(vid, a["codigo"], a["titulo"], a["texto"], a["padre_codigo"], a["nivel"],
              a["orden"]) for a in articulos])
        _evento(conn, "sop", vid, actor, "SOP_CARGADO", None, "BORRADOR",
                {"version": version.strip(), "sha256": h, "metodo": metodo,
                 "articulos": len(articulos)})
    conn.close()
    return vid, metodo, len(articulos)


def reemplazar_articulos(proyecto, actor: Actor, version_id: int, articulos: list[dict]
                         ) -> None:
    """Correcciones de la revisión (solo mientras la versión es BORRADOR)."""
    _exigir(actor, "nc_emitir")
    codigos = [str(a["codigo"]).strip() for a in articulos]
    if len(set(codigos)) != len(codigos) or any(not c for c in codigos):
        raise NCError("Hay códigos vacíos o repetidos.")
    conn = conectar(proyecto)
    with _tx(conn):
        conn.execute("DELETE FROM sop_articulos WHERE version_id = ?", (version_id,))
        conn.executemany(
            "INSERT INTO sop_articulos (version_id, codigo, titulo, texto, padre_codigo, nivel,"
            " orden) VALUES (?,?,?,?,?,?,?)",
            [(version_id, c, str(a["titulo"]).strip(), a.get("texto") or "",
              c.rsplit(".", 1)[0] if "." in c else None, c.count(".") + 1, n)
             for n, (c, a) in enumerate(zip(codigos, articulos))])
        _evento(conn, "sop", version_id, actor, "SOP_REVISADO", "BORRADOR", "BORRADOR",
                {"articulos": len(articulos)})
    conn.close()


def activar_version(proyecto, actor: Actor, version_id: int) -> None:
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        r = conn.execute("SELECT estado FROM sop_versiones WHERE id = ?", (version_id,)).fetchone()
        if r is None or r["estado"] != "BORRADOR":
            raise NCError("Solo se activa una versión en borrador.")
        previa = conn.execute("SELECT id FROM sop_versiones WHERE estado = 'ACTIVA'").fetchone()
        if previa:
            conn.execute("UPDATE sop_versiones SET estado = 'REEMPLAZADA' WHERE id = ?",
                         (previa["id"],))
        conn.execute("UPDATE sop_versiones SET estado = 'ACTIVA' WHERE id = ?", (version_id,))
        _evento(conn, "sop", version_id, actor, "SOP_ACTIVADO", "BORRADOR", "ACTIVA",
                {"reemplaza_a": previa["id"] if previa else None})
    conn.close()


def descartar_borrador(proyecto, actor: Actor, version_id: int) -> None:
    _exigir(actor, "nc_emitir")
    conn = conectar(proyecto)
    with _tx(conn):
        conn.execute("DELETE FROM sop_articulos WHERE version_id = ?", (version_id,))
        conn.execute("DELETE FROM sop_versiones WHERE id = ? AND estado = 'BORRADOR'",
                     (version_id,))
        _evento(conn, "sop", version_id, actor, "SOP_DESCARTADO", "BORRADOR", None)
    conn.close()


def versiones(proyecto) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT v.*, (SELECT COUNT(*) FROM sop_articulos a WHERE a.version_id = v.id) AS n"
        " FROM sop_versiones v ORDER BY v.id DESC")]
    conn.close()
    return rows


def version_activa(proyecto) -> dict | None:
    conn = conectar(proyecto)
    r = conn.execute("SELECT * FROM sop_versiones WHERE estado = 'ACTIVA'").fetchone()
    conn.close()
    return dict(r) if r else None


def articulos(proyecto, version_id: int) -> list[dict]:
    conn = conectar(proyecto)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sop_articulos WHERE version_id = ? ORDER BY orden", (version_id,))]
    conn.close()
    return rows


def buscar(proyecto, texto: str = "", limite: int = 50) -> list[dict]:
    """Artículos de la versión activa por código (prefijo) o palabra en el título/texto."""
    va = version_activa(proyecto)
    if not va:
        return []
    arts = articulos(proyecto, va["id"])
    t = (texto or "").strip().lower()
    if t:
        arts = [a for a in arts if a["codigo"].startswith(t) or t in a["titulo"].lower()
                or t in (a["texto"] or "").lower()]
    return arts[:limite]
