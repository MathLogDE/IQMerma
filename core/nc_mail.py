"""
core/nc_mail.py — Notificación de una NC por mail (SMTP de Gmail)

Flujo: `componer` arma el borrador del mail (destinatarios sugeridos, asunto,
cuerpo y firma desde plantillas editables) → la UI lo deja editar y previsualizar
→ `enviar_nc` genera el PDF desde el HTML congelado (verificando su hash), lo
manda y, solo si el servidor lo aceptó, registra el envío:
  - EMITIDA → ENVIADA: la recepción es el momento del envío y desde ahí corren
    los plazos (el SOP 6 declara oficial el correo de la organización).
  - Estados posteriores: reenvío, sin tocar plazos.
El PDF enviado queda como adjunto (con hash) y cada intento, bueno o fallido, en
`nc_envios` y en la cadena de eventos. Gmail además guarda la copia en Enviados.

Las plantillas usan marcadores entre llaves ({saludo}, {numero}, …, ver
MARCADORES) que se reemplazan al previsualizar y al enviar; un marcador
desconocido queda tal cual.

La contraseña de aplicación de Gmail NO va en nc_config.json: se lee de la
variable MERMAIQ_SMTP_PASSWORD o de auth/smtp.json (fuera de git).
"""

from __future__ import annotations

import json
import re
import smtplib
import ssl
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from pathlib import Path

from core import no_conformidades as nc

ROOT = Path(__file__).parent.parent
SECRETO = ROOT / "auth" / "smtp.json"

MAIL_DEFAULT = {
    "remitente": "inventariopermanentesanjuan@gmail.com",
    "nombre_remitente": "Inventario Permanente — Auditoría Interna de Stock",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "asunto": "No conformidad N° {numero}",
    "cuerpo": (
        "{saludo}:\n\n"
        "Se adjunta la No Conformidad N° {numero}, correspondiente a la sucursal {sucursal}, "
        "generada en base a la versión vigente del Procedimiento de Operaciones Estandarizado "
        "(POE) de la organización, en la que se identificaron desvíos respecto de nuestros "
        "procedimientos estandarizados.\n\n"
        "Puntos de la no conformidad:\n"
        "{puntos}\n\n"
        "El documento adjunto es un reporte técnico que advierte sobre desvíos de "
        "procedimientos estandarizados. Su objetivo principal es la búsqueda de la mejora "
        "constante, la excelencia y la transparencia en las operaciones diarias que alcanza, "
        "por lo que no reviste carácter sancionatorio. No obstante, podrá ser utilizado como "
        "antecedente y adjuntado como prueba en futuras actuaciones de la organización, de así "
        "considerarse.\n\n"
        "Plazos, contados desde la recepción de este correo:\n"
        "- Contención (corrección inmediata): {plazo_contencion}\n"
        "- Descargo con causa raíz y plan de acción: {plazo_descargo}\n"
        "- Implementación de las acciones correctivas: {plazo_implementacion}\n\n"
        "Se solicita confirmar la recepción de este correo respondiéndolo con nombre y "
        "apellido de quien lo recibe, y devolver la no conformidad firmada (firma, aclaración "
        "y fecha y hora de recepción) dentro de las {plazo_acuse} de recibido.\n\n"
        "Quedamos a disposición ante cualquier consulta."
    ),
    "firma": (
        "Saludos.\n"
        "{firmante}\n"
        "Auditoría Interna de Stock\n"
        "Inventario Permanente — Río Shop"
    ),
    # Horas corridas para confirmar la recepción y devolver la NC firmada.
    "acuse_horas": 48,
    # Los destinatarios frecuentes viven en la agenda de contactos (core/nc_contactos.py).
}

MARCADORES = {
    "saludo": "Buenos días / Buenas tardes / Buenas noches, según la hora del envío",
    "numero": "Número de la NC con separadores (02 034 092026)",
    "sucursal": "Nombre de la sucursal",
    "tipo": "Tipo de NC (AMARILLO, NARANJA, ROJO)",
    "requisito": "Punto del SOP y a qué se refiere",
    "puntos": "Lista con el punto del POE, el tipo y el desvío detectado",
    "desvio": "Texto del desvío cargado en la NC",
    "fecha_nc": "Fecha de la NC",
    "plazo_contencion": "Plazo de contención (horas corridas desde la recepción)",
    "plazo_descargo": "Plazo de descargo con su fecha límite",
    "plazo_implementacion": "Plazo máximo de implementación con su fecha",
    "plazo_acuse": "Horas para confirmar recepción y devolver firmada (Configuración → Mail)",
    "firmante": "Nombre de quien generó la NC (responsable solicitante)",
}

# Pie fijo, en letra chica, al final de todos los mails.
PIE = "Enviado a través de la suite IQMerma"

_RX_MAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class _Seguro(dict):
    def __missing__(self, k):
        return "{" + k + "}"


# ---------------------------------------------------------------------------
# Configuración y secreto
# ---------------------------------------------------------------------------

def config_mail(cfg: dict) -> dict:
    out = json.loads(json.dumps(MAIL_DEFAULT))
    out.update(cfg.get("mail") or {})
    return out


def guardar_config_mail(proyecto, mail_cfg: dict) -> None:
    cfg = nc.cargar_config(proyecto)
    cfg["mail"] = mail_cfg
    nc.guardar_config(proyecto, cfg)


def password(remitente: str, archivo: Path = SECRETO) -> str | None:
    import os
    env = os.environ.get("MERMAIQ_SMTP_PASSWORD")
    if env:
        return env
    if archivo.exists():
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        return (datos.get(remitente) or "").replace(" ", "") or None
    return None


def guardar_password(remitente: str, pwd: str, archivo: Path = SECRETO) -> None:
    datos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    datos[remitente] = pwd.replace(" ", "")
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(datos, indent=2), encoding="utf-8")


def mail_valido(m: str) -> bool:
    return bool(_RX_MAIL.match((m or "").strip()))


# ---------------------------------------------------------------------------
# Composición
# ---------------------------------------------------------------------------

def saludo(ahora: datetime) -> str:
    if 5 <= ahora.hour < 12:
        return "Buenos días"
    if 12 <= ahora.hour < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _fmt(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        d = datetime.fromisoformat(d) if "T" in d else date.fromisoformat(d)
    return d.strftime("%d/%m/%Y %H:%M") if isinstance(d, datetime) else d.strftime("%d/%m/%Y")


def plazos_para(n: dict, cfg: dict, ahora: datetime) -> dict:
    """Fechas límite: las ya fijadas (reenvío) o las que fijaría un envío ahora."""
    if n.get("recepcion_ts"):
        recep = datetime.fromisoformat(n["recepcion_ts"])
        lc, ld = n["fecha_limite_contencion"], n["fecha_limite_descargo"]
    else:
        recep = ahora
        p = nc.calcular_plazos(n, ahora, cfg)
        lc, ld = p["fecha_limite_contencion"], p["fecha_limite_descargo"]
    li = (recep.date() + timedelta(days=n["plazo_impl_max_dias"])
          if n.get("plazo_impl_max_dias") else None)
    return {"recepcion": recep, "contencion": lc, "descargo": ld, "implementacion": li}


def _nombre(n: dict) -> str:
    nombre = (n.get("solicitante") or n.get("creada_por") or "").strip()
    return nombre.title() if nombre.isupper() else nombre


def _puntos(n: dict) -> str:
    lineas = [f"- Punto del POE: {n['req_codigo']} — {n['req_titulo']}"
              + (f" (versión {n['sop_version']})" if n.get("sop_version") else ""),
              f"- Tipo de no conformidad: {n['tipo']}"]
    if (n.get("desvio_txt") or "").strip():
        lineas.append(f"- Desvío detectado: {n['desvio_txt'].strip()}")
    return "\n".join(lineas)


def fechas_clave(n: dict, cfg: dict, ahora: datetime) -> dict:
    """Fechas límite (por día) que el texto escribe: si cambian entre la redacción
    y el envío, el texto ya no dice lo que queda registrado."""
    p = plazos_para(n, cfg, ahora)
    return {"descargo": _fmt(p["descargo"]), "implementacion": _fmt(p["implementacion"])}


def contexto(n: dict, cfg: dict, ahora: datetime) -> dict:
    p = plazos_para(n, cfg, ahora)
    horas = int(config_mail(cfg).get("acuse_horas") or 48)
    acuse = f"{horas} horas"
    if n.get("plazo_contencion_horas"):
        cont = f"{n['plazo_contencion_horas']} horas corridas"
    elif n.get("contencion_con_descargo"):
        cont = f"junto con el descargo (hasta el {_fmt(p['descargo'])})"
    else:
        cont = "no aplica"
    dias = n["plazo_descargo_dias"]
    desc = (f"{dias} día{'s' if dias != 1 else ''} hábil{'es' if dias != 1 else ''}"
            f" (hasta el {_fmt(p['descargo'])})")
    impl = (f"hasta {n['plazo_impl_max_dias']} días corridos (hasta el {_fmt(p['implementacion'])})"
            if p["implementacion"] else "fecha que proponga la sucursal en el descargo")
    return {
        "saludo": saludo(ahora), "numero": nc.numero_impreso(n["numero"]),
        "sucursal": n.get("sucursal_nombre") or n["codigodepo"], "tipo": n["tipo"],
        "requisito": f"{n['req_codigo']} — {n['req_titulo']}", "fecha_nc": _fmt(n["fecha"]),
        "puntos": _puntos(n), "desvio": (n.get("desvio_txt") or "").strip(),
        "plazo_contencion": cont, "plazo_descargo": desc, "plazo_implementacion": impl,
        "plazo_acuse": acuse, "firmante": _nombre(n),
    }


def rellenar(plantilla: str, ctx: dict) -> str:
    return (plantilla or "").format_map(_Seguro(ctx))


def componer(proyecto, nc_id: int, ahora: datetime | None = None) -> dict:
    """Borrador del mail ya completado con los datos de la NC (número, puntos,
    firmante, plazos) y los contactos «por defecto» de la sucursal. `referencia`
    guarda las fechas límite escritas, para detectar un texto desfasado al enviar."""
    from core import nc_contactos
    ahora = ahora or datetime.now()
    cfg = nc.cargar_config(proyecto)
    mcfg = config_mail(cfg)
    n = nc.obtener(proyecto, nc_id)
    if not n["numero"]:
        raise nc.NCError("La NC todavía no fue emitida: no hay nada para enviar.")
    ctx = contexto(n, cfg, ahora)
    return {"destinatarios": nc_contactos.sugeridos(proyecto, n["codigodepo"]),
            "asunto": rellenar(mcfg["asunto"], ctx), "cuerpo": rellenar(mcfg["cuerpo"], ctx),
            "firma": rellenar(mcfg["firma"], ctx), "referencia": fechas_clave(n, cfg, ahora),
            "redactado": ahora.isoformat(timespec="minutes")}


def separar(destinatarios: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """(para, cc, cco) sin repetidos; un mail marcado en varias columnas va en la primera."""
    para, cc, cco, vistos = [], [], [], set()
    for col, lista in (("para", para), ("cc", cc), ("cco", cco)):
        for d in destinatarios:
            m = (d.get("mail") or "").strip()
            if d.get(col) and m and m.lower() not in vistos:
                vistos.add(m.lower())
                lista.append(m)
    return para, cc, cco


def previsualizar(proyecto, nc_id: int, asunto: str, cuerpo: str, firma: str,
                  ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now()
    cfg = nc.cargar_config(proyecto)
    ctx = contexto(nc.obtener(proyecto, nc_id), cfg, ahora)
    return {"asunto": rellenar(asunto, ctx),
            "texto": rellenar(cuerpo, ctx).rstrip() + "\n\n" + rellenar(firma, ctx).strip()
            + "\n\n" + PIE}


def construir_mensaje(mcfg: dict, para: list[str], cc: list[str], asunto: str, texto: str,
                      firma_inicio: int, adjunto_nombre: str, adjunto: bytes) -> EmailMessage:
    """Mensaje texto + HTML con el PDF. Sin encabezado Bcc: los CCO van solo en el sobre."""
    msg = EmailMessage()
    msg["From"] = formataddr((mcfg["nombre_remitente"], mcfg["remitente"]))
    msg["To"] = ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = asunto
    msg["Message-ID"] = make_msgid(domain=mcfg["remitente"].split("@")[-1])
    msg.set_content(texto + "\n\n--\n" + PIE)
    cuerpo, firma = texto[:firma_inicio], texto[firma_inicio:]
    html = ("<div style=\"font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1a2233\">"
            + escape(cuerpo).replace("\n", "<br>")
            + "<div style=\"color:#5b6472;font-size:13px\">"
            + escape(firma).replace("\n", "<br>") + "</div>"
            + "<div style=\"margin-top:18px;color:#9aa5b8;font-size:10px\">"
            + escape(PIE) + "</div></div>")
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(adjunto, maintype="application", subtype="pdf", filename=adjunto_nombre)
    return msg


_RX_APP_PWD = re.compile(r"^[a-z]{16}$")


def parece_password_de_aplicacion(pwd: str) -> bool:
    """Las contraseñas de aplicación de Google son 16 letras minúsculas
    (se muestran en 4 grupos de 4; los espacios no cuentan)."""
    return bool(_RX_APP_PWD.match((pwd or "").replace(" ", "")))


def _login(s: smtplib.SMTP, usuario: str, pwd: str) -> None:
    """Solo AUTH PLAIN. `SMTP.login()` prueba otro método si el primero falla, y
    Gmail corta la conexión ante el segundo intento: el 535 real («usuario o
    contraseña no aceptados») quedaba tapado por «Connection unexpectedly closed»."""
    s.ehlo()
    s.user, s.password = usuario, pwd
    s.auth("PLAIN", s.auth_plain)


def explicar_error(e: Exception) -> str:
    """Mensaje claro para los errores de SMTP más comunes con Gmail."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        detalle = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) \
            else str(e.smtp_error)
        if e.smtp_code == 534 or "Application-specific password" in detalle:
            return ("Gmail exige una contraseña de aplicación: la contraseña normal de la "
                    "cuenta no sirve para enviar desde IQMerma.")
        return ("Gmail no aceptó el usuario y la contraseña (535). Revisá que el remitente "
                "sea la cuenta correcta y que la contraseña sea una «contraseña de aplicación» "
                "de 16 letras (Cuenta de Google → Seguridad → Verificación en dos pasos → "
                "Contraseñas de aplicaciones), no la contraseña con la que entrás a Gmail.")
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return f"El servidor cortó la conexión ({e}). Probá de nuevo en unos minutos."
    if isinstance(e, (OSError, TimeoutError)):
        return (f"No se pudo conectar con el servidor de correo ({e}). Revisá la conexión a "
                "internet o si un firewall/antivirus bloquea el puerto 465.")
    return f"{type(e).__name__}: {e}"


def enviar_smtp(mcfg: dict, pwd: str, msg: EmailMessage, todos: list[str]) -> None:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(mcfg["smtp_host"], int(mcfg["smtp_port"]), context=ctx,
                          timeout=30) as s:
        _login(s, mcfg["remitente"], pwd)
        s.send_message(msg, from_addr=mcfg["remitente"], to_addrs=todos)


def probar_conexion(mcfg: dict, pwd: str) -> None:
    """Solo autentica: no manda nada."""
    with smtplib.SMTP_SSL(mcfg["smtp_host"], int(mcfg["smtp_port"]),
                          context=ssl.create_default_context(), timeout=20) as s:
        _login(s, mcfg["remitente"], pwd)


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------

ESTADOS_REENVIO = ("ENVIADA", "RESPONDIDA", "EN_VERIFICACION")


def enviar_nc(proyecto, actor: nc.Actor, nc_id: int, destinatarios: list[dict], asunto: str,
              cuerpo: str, firma: str, enviar=None, html_a_pdf=None,
              pwd: str | None = None, guardar_nuevos: bool = False,
              referencia: dict | None = None) -> dict:
    """Envía la NC por mail y registra el resultado. `enviar` y `html_a_pdf` se
    inyectan en los tests. Devuelve {'plazos', 'message_id', 'reenvio'}."""
    if not actor.tiene("nc_emitir"):
        raise nc.NCError(f"El usuario {actor.username} no tiene el rol nc_emitir.")
    cfg = nc.cargar_config(proyecto)
    mcfg = config_mail(cfg)
    n = nc.obtener(proyecto, nc_id)
    if n["estado"] not in ("EMITIDA",) + ESTADOS_REENVIO:
        raise nc.NCError(f"La NC está en {n['estado']}: no se puede enviar.")
    para, cc, cco = separar(destinatarios)
    if not para:
        raise nc.NCError("Marcá al menos un destinatario en «Para».")
    malos = [m for m in para + cc + cco if not mail_valido(m)]
    if malos:
        raise nc.NCError("Direcciones inválidas: " + ", ".join(malos))
    if not (asunto or "").strip():
        raise nc.NCError("El asunto no puede estar vacío.")
    pwd = pwd or password(mcfg["remitente"])
    if enviar is None and not pwd:
        raise nc.NCError("Falta la contraseña de aplicación de Gmail (Configuración → Mail).")

    ahora = datetime.now()
    if referencia and n["estado"] == "EMITIDA" and fechas_clave(n, cfg, ahora) != referencia:
        raise nc.NCError("Las fechas límite escritas en el texto ya no coinciden con las de un "
                         "envío ahora (cambió el día desde que se redactó). Tocá «Regenerar "
                         "texto» y revisalo de nuevo.")
    ctx = contexto(n, cfg, ahora)
    asunto_f = rellenar(asunto, ctx)
    cuerpo_f = rellenar(cuerpo, ctx).rstrip() + "\n\n"
    texto = cuerpo_f + rellenar(firma, ctx).strip()

    html = nc.html_verificado(proyecto, nc_id)
    if html_a_pdf is None:
        from core.pdf import html_a_pdf
    pdf_bytes = html_a_pdf(html)
    nombre_pdf = f"No conformidad {n['numero']}.pdf"
    msg = construir_mensaje(mcfg, para, cc, asunto_f, texto, len(cuerpo_f), nombre_pdf, pdf_bytes)
    envio = {"canal": "mail", "remitente": mcfg["remitente"], "para": para, "cc": cc,
             "cco": cco, "asunto": asunto_f, "cuerpo": texto,
             "message_id": msg["Message-ID"]}
    try:
        if enviar is None:
            enviar_smtp(mcfg, pwd, msg, para + cc + cco)
        else:
            enviar(msg, para + cc + cco)
    except Exception as e:  # noqa: BLE001 — se registra y se informa
        nc.registrar_envio_fallido(proyecto, actor, nc_id, envio, f"{type(e).__name__}: {e}")
        raise nc.NCError("No se pudo enviar el mail. " + explicar_error(e)) from e
    plazos = nc.registrar_envio_mail(proyecto, actor, nc_id, envio, (nombre_pdf, pdf_bytes),
                                     ahora)
    # Agenda: no es evidencia, un error acá no debe ocultar que el mail salió.
    nuevos = []
    try:
        from core import nc_contactos
        if guardar_nuevos:
            nuevos = nc_contactos.agregar_nuevos(proyecto, actor, destinatarios)
        nc_contactos.registrar_uso(proyecto, para + cc + cco)
    except Exception:  # noqa: BLE001
        pass
    return {"plazos": plazos, "message_id": msg["Message-ID"],
            "reenvio": n["estado"] != "EMITIDA", "contactos_nuevos": nuevos}
