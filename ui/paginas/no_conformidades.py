"""ui/paginas/no_conformidades.py — Página No conformidades (Auditoría)

Toda la lógica y las reglas viven en core/no_conformidades.py y core/sop.py; acá
solo se arma el actor (admin = todos los roles; cliente = roles_nc del YAML) y
los formularios. Los colores amarillo/naranja/rojo quedan reservados para la
severidad: los vencimientos se muestran como texto.
"""
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st
import yaml

from ui.comun import *  # noqa: F401,F403
from ui.comun import ROOT, get_connection, modo_solo_lectura
from core import nc_anexos
from core import nc_contactos
from core import nc_mail
from core import no_conformidades as nc
from core import pdf
from core import sop
from core.auth import ROLES_NC
from core.reporte import generar_no_conformidad, vista_anexo

_ICONO_ESTADO = {
    "BORRADOR": "📝", "EMITIDA": "📄", "ENVIADA": "📨", "RESPONDIDA": "💬",
    "ACEPTADA": "🛠", "EN_VERIFICACION": "🛠", "CERRADA_SUCURSAL": "📬", "CERRADA": "✅",
    "CERRADA_NO_EFICAZ": "↩", "ANULADA": "🚫",
}
_ETIQUETA_ESTADO = {
    "ACEPTADA": "EN IMPLEMENTACIÓN",
    "EN_VERIFICACION": "EN IMPLEMENTACIÓN",
    "CERRADA_SUCURSAL": "CERRADA CON SUCURSAL · verificación pendiente",
    "CERRADA": "CERRADA (eficaz)",
}


def _estado_txt(est: str) -> str:
    return f"{_ICONO_ESTADO.get(est, '')} {_ETIQUETA_ESTADO.get(est, est)}"


# ---------------------------------------------------------------------------
# Actor, catálogos
# ---------------------------------------------------------------------------

def _actor(cfg: dict) -> nc.Actor:
    if not modo_solo_lectura():
        return nc.actor_admin(cfg.get("admin_nombre") or "Administrador")
    username = st.session_state.get("username", "")
    datos = {}
    archivo = ROOT / "auth" / "usuarios.yaml"
    if archivo.exists():
        conf = yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}
        datos = conf.get("credentials", {}).get("usernames", {}).get(username, {})
    return nc.Actor(username, datos.get("name") or username,
                    frozenset(r for r in (datos.get("roles_nc") or []) if r in ROLES_NC))


@st.cache_data(ttl=600, show_spinner=False)
def _sucursales(proyecto: str) -> pd.DataFrame:
    conn = get_connection(proyecto)
    try:
        df = conn.execute("""
            SELECT codigodepo, COALESCE(nombre, codigodepo) AS nombre
            FROM depositos WHERE NOT COALESCE(es_logistica, FALSE)
            ORDER BY codigodepo
        """).df()
    finally:
        conn.close()
    return df[df["codigodepo"].astype(str).str.strip().str.lstrip("0").str.len() <= 3]


def _err(fn, *a, **k):
    """Ejecuta una acción del core mostrando los errores de negocio."""
    try:
        return True, fn(*a, **k)
    except nc.NCError as e:
        st.error(str(e))
    except Exception as e:  # noqa: BLE001 — incluye triggers de integridad
        st.error(f"Operación rechazada: {e}")
    return False, None


def _adjunto(archivo) -> tuple[str, bytes] | None:
    return (archivo.name, archivo.getvalue()) if archivo is not None else None


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

def render(proyecto):
    st.markdown("## No conformidades")
    cfg = nc.cargar_config(proyecto)
    actor = _actor(cfg)
    roles = ", ".join(sorted(actor.roles)) or "solo lectura"
    st.caption(f"Usuario: **{actor.nombre}** ({actor.username}) · roles: {roles}")

    filas = nc.listar(proyecto)
    abiertas = [f for f in filas if f["estado"] in nc.ESTADOS_ABIERTOS]
    pendientes = [f for f in filas if f["estado"] in nc.ESTADOS_VERIFICACION_PENDIENTE]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Abiertas", len(abiertas))
    c2.metric("Con vencimientos", sum(1 for f in abiertas
                                     if any("⏰" in v for v in f["vencimientos"])))
    c3.metric("En implementación", sum(1 for f in filas if f["estado"] in nc.ESTADOS_ACEPTADA))
    c4.metric("Verificación pendiente", len(pendientes),
              help="Cerradas con la sucursal: falta verificar la eficacia (ISO 9001 §10.2). "
                   f"Vencidas: {sum(1 for f in pendientes if any('⏰' in v for v in f['vencimientos']))}.")
    c5.metric("Borradores", sum(1 for f in filas if f["estado"] == "BORRADOR"))

    pestañas = ["➕ Nueva NC", "📋 Seguimiento", "📇 Contactos", "🗒 Observaciones", "📘 SOP",
                "🛡 Integridad"]
    if not modo_solo_lectura():
        pestañas.append("⚙ Configuración")
    tabs = st.tabs(pestañas)
    with tabs[0]:
        _tab_nueva(proyecto, cfg, actor, filas)
    with tabs[1]:
        _tab_seguimiento(proyecto, cfg, actor, filas)
    with tabs[2]:
        _tab_contactos(proyecto, actor)
    with tabs[3]:
        _tab_observaciones(proyecto, actor)
    with tabs[4]:
        _tab_sop(proyecto, actor)
    with tabs[5]:
        _tab_integridad(proyecto)
    if len(tabs) > 6:
        with tabs[6]:
            _tab_config(proyecto, cfg)


def _descargas(html: str, numero: str, key: str, prefijo: str = "NC") -> None:
    """HTML imprimible + PDF (generado con el navegador del servidor)."""
    c1, c2 = st.columns(2)
    with c1:
        boton_reporte(html, f"{prefijo}_{numero}.html", key=f"{key}_html")
    with c2:
        if not pdf.disponible():
            st.caption("PDF no disponible: no se encontró Edge ni Chrome en el servidor.")
            return
        k = f"{key}_pdf_bytes"
        if k not in st.session_state:
            if st.button("📑 Generar PDF", key=f"{key}_pdf_gen"):
                with st.spinner("Generando PDF…"):
                    ok, b = _err(pdf.html_a_pdf, html)
                if ok:
                    st.session_state[k] = b
                    st.rerun()
        else:
            st.download_button("📑 Descargar PDF", st.session_state[k], f"{prefijo}_{numero}.pdf",
                               "application/pdf", key=f"{key}_pdf_dl")


def _lista_codigos(texto: str) -> list[str]:
    return [c for c in texto.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",")
            if c.strip()]


def _seccion_anexos(proyecto, actor, actual: dict, k: str) -> None:
    st.markdown("##### Anexos con datos de IQMerma")
    if not actual:
        st.caption("Guardá el borrador para agregar anexos.")
        return
    depo, suc_nombre = actual["codigodepo"], actual["sucursal_nombre"]
    st.caption(f"Se generan para la sucursal del borrador ({suc_nombre}, {depo}) con los datos "
               "de hoy y quedan congelados al emitir. «Imprimir» = va dentro del documento; "
               "«interno» = solo queda en el seguimiento.")

    for a in nc.anexos(proyecto, actual["id"]):
        c1, c2, c3, c4 = st.columns([5, 2, 2, 1])
        c1.markdown(f"**{a['titulo']}** · datos al {a['corte_datos'] or '—'}")
        imprimir = c2.toggle("Imprimir", value=bool(a["imprimir"]), key=f"anx_imp_{a['id']}")
        if imprimir != bool(a["imprimir"]):
            _err(nc.marcar_anexo, proyecto, actor, a["id"], imprimir)
            st.rerun()
        if a["tipo"] == "merma" and a["resumen"].get("merma_valorizada") is not None:
            if c3.button("Usar como línea base", key=f"anx_lb_{a['id']}"):
                r = a["resumen"]
                fuente = r["fuente"] + (f" — {r['pct_merma']:.2f}% s/venta"
                                        if r.get("pct_merma") is not None else "")
                ok, _ = _err(nc.actualizar_borrador, proyecto, actor, actual["id"],
                             {"merma_linea_base": r["merma_valorizada"],
                              "linea_base_unidad": "$", "linea_base_fuente": fuente})
                if ok:
                    for suf in ("_lb", "_lbu", "_lbf"):
                        st.session_state.pop(f"{k}{suf}", None)
                    st.rerun()
        if c4.button("🗑", key=f"anx_del_{a['id']}", help="Quitar anexo"):
            _err(nc.quitar_anexo, proyecto, actor, a["id"])
            st.rerun()
        with st.expander("Ver anexo"):
            st.iframe(vista_anexo(proyecto, a), height=620)

    with st.expander("➕ Agregar anexo", expanded=False):
        tipo = st.radio("Tipo", list(nc_anexos.TIPOS), format_func=nc_anexos.TIPOS.get,
                        horizontal=True, key=f"{k}_anx_tipo")
        d0, d1 = nc_anexos.rango_default(date.fromisoformat(actual["fecha"]))
        if tipo == "merma":
            d0 = date.fromisoformat(actual["fecha"]).replace(day=1)
        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", value=d0, format="DD/MM/YYYY", key=f"{k}_anx_d0_{tipo}")
        hasta = c2.date_input("Hasta", value=d1, format="DD/MM/YYYY", key=f"{k}_anx_d1_{tipo}")
        codigos = _lista_codigos(st.text_input(
            "Códigos" + (" (hasta 6)" if tipo == "serie_stock" else " a destacar (opcional)"),
            key=f"{k}_anx_cod_{tipo}", help="Separados por coma o espacio."))
        params = {"codigodepo": depo, "sucursal_nombre": suc_nombre, "desde": desde,
                  "hasta": hasta, "codigos": codigos}
        if tipo == "merma":
            c1, c2, c3 = st.columns([3, 2, 1])
            params["rubros"] = c1.multiselect("Rubros (vacío = todos)",
                                              _rubros(proyecto), key=f"{k}_anx_rub")
            params["modo_valorizacion"] = c2.selectbox(
                "Valorización", ["costo", "lista_1", "mixto"], key=f"{k}_anx_modo",
                format_func={"costo": "A costo", "lista_1": "A precio de lista",
                             "mixto": "Mixto"}.get)
            params["top_n"] = c3.number_input("Top", 5, 50, 15, key=f"{k}_anx_top")
        imprimir = st.toggle("Imprimir en la NC", value=True, key=f"{k}_anx_imp")
        if st.button("Generar y agregar", type="primary", key=f"{k}_anx_gen"):
            with st.spinner("Calculando con los datos de IQMerma…"):
                try:
                    anexo = nc_anexos.generar(proyecto, tipo, params)
                except (ValueError, FileNotFoundError) as e:
                    st.error(str(e))
                    return
            if _err(nc.agregar_anexo, proyecto, actor, actual["id"], anexo, imprimir)[0]:
                st.rerun()


@st.cache_data(ttl=3600, show_spinner=False)
def _rubros(proyecto: str) -> list[str]:
    return nc_anexos.rubros(proyecto)


# ---------------------------------------------------------------------------
# Nueva NC / borradores
# ---------------------------------------------------------------------------

def _tab_nueva(proyecto, cfg, actor, filas):
    if not actor.tiene("nc_emitir"):
        st.info("Tu usuario no tiene el rol nc_emitir.")
        return
    va = sop.version_activa(proyecto)
    if not va:
        st.warning("No hay una versión del SOP activa: cargala en la pestaña 📘 SOP. "
                   "Los criterios de la NC salen de ahí.")
        return

    borradores = [f for f in filas if f["estado"] == "BORRADOR"]
    opciones = ["(nueva)"] + [f"#{b['id']} · {b['sucursal_nombre'] or '—'} · {b['tipo']}"
                              + (" · reemplaza otra NC" if b["reemplaza_a"] else "")
                              + (" · por acción no eficaz" if b["origen_id"] else "")
                              for b in borradores]
    sel = st.selectbox("Borrador", opciones, key="nc_borrador_sel")
    actual = {} if sel == "(nueva)" else nc.obtener(proyecto, borradores[opciones.index(sel) - 1]["id"])
    k = f"nc_{actual.get('id', 'nueva')}"

    suc = _sucursales(proyecto)
    etiquetas = [f"{r.codigodepo} · {r.nombre}" for r in suc.itertuples()]
    idx_suc = next((i for i, r in enumerate(suc.itertuples())
                    if str(r.codigodepo).zfill(3) == actual.get("codigodepo")), 0)
    tipos = nc.tipos_ordenados(cfg)

    c1, c2, c3 = st.columns(3)
    suc_sel = c1.selectbox("Sucursal", etiquetas, index=idx_suc, key=f"{k}_suc")
    fecha = c2.date_input("Fecha", value=date.fromisoformat(actual["fecha"])
                          if actual.get("fecha") else date.today(),
                          format="DD/MM/YYYY", key=f"{k}_fecha")
    tipo = c3.selectbox("Tipo", tipos, index=tipos.index(actual["tipo"])
                        if actual.get("tipo") in tipos else 0, key=f"{k}_tipo")
    t = cfg["tipos"][tipo]
    st.caption(f"Plazos de {tipo} (desde la notificación): contención "
               f"{str(t['contencion_horas']) + ' h corridas' if t.get('contencion_horas') else ('junto con el descargo' if t.get('contencion_con_descargo') else 'no aplica')}"
               f" · descargo {t['descargo_dias_habiles']} días hábiles · implementación "
               f"{'hasta ' + str(t['implementacion_max_dias']) + ' días' if t.get('implementacion_max_dias') else 'la propone la sucursal'}")

    c1, c2, c3 = st.columns(3)
    areas = cfg["areas"]
    area = c1.selectbox("Área afectada", areas, index=areas.index(actual["area"])
                        if actual.get("area") in areas else 0, key=f"{k}_area")
    resp = cfg["responsables_accion"]
    responsable = c2.selectbox("Responsable de la acción", resp,
                               index=resp.index(actual["responsable_accion"])
                               if actual.get("responsable_accion") in resp else 0,
                               key=f"{k}_resp")
    solicitante = c3.text_input("Responsable solicitante",
                                value=actual.get("solicitante") or actor.nombre.upper(),
                                key=f"{k}_solic")

    # Criterio del SOP
    st.markdown("##### Requisito incumplido (SOP versión " + va["version"] + ")")
    busq = st.text_input("Buscar por código o palabra", value=actual.get("req_codigo") or "",
                         key=f"{k}_busq")
    arts = sop.buscar(proyecto, busq)
    art = None
    if arts:
        art_et = [f"{a['codigo']} — {a['titulo']}" for a in arts]
        art_idx = next((i for i, a in enumerate(arts) if a["codigo"] == actual.get("req_codigo")), 0)
        art = arts[art_et.index(st.selectbox("Artículo", art_et, index=art_idx,
                                             key=f"{k}_art"))]
        with st.expander("Texto del requisito", expanded=False):
            st.write(art["texto"] or "—")
    else:
        st.warning("Ningún artículo coincide.")

    intro_def = cfg["intro_plantilla"].format(fecha=fecha.strftime("%d/%m/%Y"))
    intro = st.text_area("Introducción", value=actual.get("intro") or intro_def, height=90,
                         key=f"{k}_intro")
    evidencia = st.text_area("Evidencia (lo observado, con datos)",
                             value=actual.get("evidencia_txt") or "", height=170,
                             key=f"{k}_evid")
    desvio = st.text_area("Desvío (en qué difiere lo observado del requisito)",
                          value=actual.get("desvio_txt") or "", height=90, key=f"{k}_desv")

    impactos = st.multiselect("Impacto", cfg["impactos"],
                              default=[i for i in actual.get("impactos", []) if i in cfg["impactos"]],
                              key=f"{k}_imp")
    acc_prev = actual.get("acciones_recomendadas") or []
    plantillas = st.multiselect("Acciones recomendadas (plantillas)", cfg["acciones_plantilla"],
                                default=[a for a in acc_prev if a in cfg["acciones_plantilla"]],
                                key=f"{k}_accp")
    extra = st.text_area("Acciones adicionales (una por línea)",
                         value="\n".join(a for a in acc_prev if a not in cfg["acciones_plantilla"]),
                         height=70, key=f"{k}_acce")

    c1, c2, c3 = st.columns([1, 1, 2])
    lb_val = c1.number_input("Línea base de merma", value=actual.get("merma_linea_base"),
                             step=1.0, key=f"{k}_lb")
    unidades = ["%", "$", "unidades"]
    lb_uni = c2.selectbox("Unidad", unidades, index=unidades.index(actual["linea_base_unidad"])
                          if actual.get("linea_base_unidad") in unidades else 0, key=f"{k}_lbu")
    lb_fte = c3.text_input("Período / fuente", value=actual.get("linea_base_fuente") or "",
                           key=f"{k}_lbf")

    fila_suc = suc.iloc[etiquetas.index(suc_sel)] if etiquetas else None
    datos = {
        "codigodepo": str(fila_suc["codigodepo"]) if fila_suc is not None else None,
        "sucursal_nombre": str(fila_suc["nombre"]) if fila_suc is not None else None,
        "fecha": fecha, "tipo": tipo, "area": area, "responsable_accion": responsable,
        "solicitante": solicitante.strip(), "intro": intro.strip(),
        "evidencia_txt": evidencia.strip(), "desvio_txt": desvio.strip(),
        "impactos": impactos,
        "acciones_recomendadas": plantillas + [x.strip() for x in extra.splitlines() if x.strip()],
        "merma_linea_base": lb_val, "linea_base_unidad": lb_uni if lb_val is not None else None,
        "linea_base_fuente": lb_fte.strip() or None,
        "req_codigo": art["codigo"] if art else None, "req_titulo": art["titulo"] if art else None,
        "req_texto": art["texto"] if art else None, "sop_version_id": va["id"],
        "sop_version": va["version"],
    }

    evid_file = st.file_uploader("Adjuntar evidencia (captura, export, foto)", key=f"{k}_adj")

    _seccion_anexos(proyecto, actor, actual, k)
    st.markdown("---")

    c1, c2, c3, c4 = st.columns(4)
    guardar = c1.button("💾 Guardar borrador", key=f"{k}_guardar")
    previa = c2.button("👁 Vista previa", key=f"{k}_previa")
    emitir = c3.button("📄 Emitir", type="primary", key=f"{k}_emitir")
    borrar = c4.button("🗑 Descartar borrador", key=f"{k}_borrar", disabled=not actual)

    def _guardar() -> int | None:
        if actual:
            ok, _ = _err(nc.actualizar_borrador, proyecto, actor, actual["id"], datos)
            nc_id = actual["id"] if ok else None
        else:
            ok, nc_id = _err(nc.crear_borrador, proyecto, actor, datos)
        if nc_id and evid_file is not None:
            _err(nc.agregar_adjunto, proyecto, actor, nc_id, "hallazgo", *_adjunto(evid_file))
        return nc_id

    if guardar and _guardar():
        st.success("Borrador guardado.")
        st.rerun()
    if borrar and actual:
        ok, _ = _err(nc.eliminar_borrador, proyecto, actor, actual["id"])
        if ok:
            st.rerun()
    if previa:
        prev = dict(datos)
        prev.update({
            "codigodepo": nc._depo3(datos["codigodepo"] or 0), "fecha": fecha.isoformat(),
            "numero": f"00{nc._depo3(datos['codigodepo'] or 0)}{fecha:%m%Y}",
            "tipo_severidad": t["severidad"], "plazo_descargo_dias": t["descargo_dias_habiles"],
            "plazo_contencion_horas": t.get("contencion_horas"),
            "contencion_con_descargo": t.get("contencion_con_descargo"),
            "plazo_impl_max_dias": t.get("implementacion_max_dias"),
            "emitida_en": datetime.now().isoformat(timespec="seconds"),
            "sha256_contenido": "VISTA-PREVIA",
            "anexos": nc.anexos(proyecto, actual["id"]) if actual else [],
        })
        st.caption("Vista previa: el número definitivo se asigna al emitir.")
        st.iframe(generar_no_conformidad(proyecto, prev, cfg), height=1150)
    if emitir:
        nc_id = _guardar()
        if nc_id:
            ok, numero = _err(nc.emitir, proyecto, actor, nc_id)
            if ok:
                st.session_state["nc_recien_emitida"] = (nc_id, numero)
                st.session_state.pop("nc_borrador_sel", None)
                st.rerun()

    if "nc_recien_emitida" in st.session_state:
        nc_id, numero = st.session_state["nc_recien_emitida"]
        st.success(f"NC {nc.numero_impreso(numero)} emitida.")
        ok, html = _err(nc.html_para_reimprimir, proyecto, actor, nc_id)
        if ok:
            _descargas(html, numero, key=f"nc_dl_{nc_id}")


# ---------------------------------------------------------------------------
# Seguimiento
# ---------------------------------------------------------------------------

def _tab_seguimiento(proyecto, cfg, actor, filas):
    emitidas = [f for f in filas if f["estado"] != "BORRADOR"]
    if not emitidas:
        st.info("Todavía no hay NC emitidas.")
        return
    c1, c2, c3, c4 = st.columns(4)
    f_suc = c1.multiselect("Sucursal", sorted({f["sucursal_nombre"] or "" for f in emitidas}))
    f_est = c2.multiselect("Estado", [e for e in nc.ESTADOS if e != "BORRADOR"])
    f_tipo = c3.multiselect("Tipo", nc.tipos_ordenados(cfg))
    solo_venc = c4.checkbox("Solo con vencimientos")
    vista = [f for f in emitidas
             if (not f_suc or f["sucursal_nombre"] in f_suc)
             and (not f_est or f["estado"] in f_est)
             and (not f_tipo or f["tipo"] in f_tipo)
             and (not solo_venc or any("⏰" in v for v in f["vencimientos"]))]

    def _color_tipo(v):
        c = cfg["tipos"].get(v, {}).get("color")
        return f"background-color: {c}; color: #111; font-weight: 600" if c else ""

    df = pd.DataFrame([{
        "Número": f["numero_impreso"], "Fecha": f["fecha"], "Sucursal": f["sucursal_nombre"],
        "Tipo": f["tipo"], "Estado": _estado_txt(f["estado"]),
        "Plazos": " · ".join(f["vencimientos"]) or "—",
        "Requisito": f"{f['req_codigo']} {f['req_titulo'] or ''}",
    } for f in vista])
    if df.empty:
        st.info("Ninguna NC coincide con los filtros.")
        return
    st.dataframe(df.style.map(_color_tipo, subset=["Tipo"]), hide_index=True,
                 width="stretch")

    sel = st.selectbox("Ver NC", [f["numero_impreso"] for f in vista], key="nc_seg_sel")
    fila = vista[[f["numero_impreso"] for f in vista].index(sel)]
    _detalle(proyecto, cfg, actor, fila["id"])


def _detalle(proyecto, cfg, actor, nc_id):
    n = nc.obtener(proyecto, nc_id)
    st.markdown(f"### NC {nc.numero_impreso(n['numero'])} · {n['tipo']} · "
                f"{_estado_txt(n['estado'])}")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Sucursal:** {n['sucursal_nombre']} ({n['codigodepo']})  \n"
                f"**Fecha:** {n['fecha']}  \n**Área:** {n['area']}")
    c2.markdown(f"**Requisito:** {n['req_codigo']} — {n['req_titulo']}  \n"
                f"**SOP:** versión {n['sop_version']}  \n**Solicitante:** {n['solicitante']}")
    lim_impl = nc.limite_implementacion(n)
    c3.markdown(f"**Recepción:** {n['recepcion_ts'] or '—'}  \n"
                f"**Contención hasta:** {n['fecha_limite_contencion'] or '—'}  \n"
                f"**Descargo hasta:** {n['fecha_limite_descargo'] or '—'}  \n"
                f"**Implementación hasta:** {lim_impl or '—'}")
    if n["reemplaza_a"]:
        st.caption(f"Reemplaza a la NC #{n['reemplaza_a']}.")
    if n["origen_id"]:
        st.caption(f"Generada por acción no eficaz de la NC #{n['origen_id']}.")

    k_html = f"reimp_html_{nc_id}"
    if k_html not in st.session_state:
        if st.button("🖨 Preparar reimpresión", key=f"reimp_{nc_id}"):
            ok, html = _err(nc.html_para_reimprimir, proyecto, actor, nc_id)
            if ok:
                st.session_state[k_html] = html
                st.rerun()
    else:
        _descargas(st.session_state[k_html], n["numero"], key=f"reimp_dl_{nc_id}")
        st.caption(f"SHA-256 verificado: `{n['sha256_html']}`")

    anx = nc.anexos(proyecto, nc_id)
    if anx:
        with st.expander(f"Anexos ({len(anx)})"):
            for a in anx:
                st.markdown(f"**{a['titulo']}** · {'impreso' if a['imprimir'] else 'interno'}"
                            f" · datos al {a['corte_datos'] or '—'} · sha256 {a['sha256'][:16]}…")
                if st.toggle("Ver", key=f"seg_anx_{a['id']}"):
                    st.iframe(vista_anexo(proyecto, a), height=620)

    # Descargos
    rs = nc.respuestas(proyecto, nc_id)
    for r in rs:
        etiqueta = (f"Descargo intento {r['intento']} · {r['cargada_en'][:16]} · "
                    + ("OBSERVADO" if r["observada"] else "vigente"))
        with st.expander(etiqueta, expanded=not r["observada"]):
            st.markdown(f"**Corrección inmediata:** {r['correccion']}  \n"
                        f"**Causa raíz ({r['categoria_6m']}):** {r['causa_raiz']}")
            if r["observada"]:
                st.markdown(f"**Motivo de la observación:** {r['motivo_observacion']}")
            acciones = r["acciones"]
            if r is rs[-1] and n["estado"] in nc.ESTADOS_ACEPTADA + nc.ESTADOS_CON_ACTA:
                impl = {a["id"]: a["implementacion"]
                        for a in nc.acciones_con_implementacion(proyecto, nc_id)}
                acciones = [{**a, "implementada": (impl.get(a["id"]) or {}).get("fecha_real")
                             or "pendiente",
                             "evidencia": (impl.get(a["id"]) or {}).get("evidencia_txt") or ""}
                            for a in acciones]
            cols = [c for c in ("descripcion", "responsable", "fecha_compromiso", "implementada",
                                "evidencia") if c in (acciones[0] if acciones else {})]
            st.dataframe(pd.DataFrame(acciones)[cols], hide_index=True, width="stretch")
    c = nc.cierre(proyecto, nc_id)
    if c:
        with st.container(border=True):
            st.markdown(f"**Acta de cierre con la sucursal** · {c['cerrada_en'][:16]} · "
                        f"por {c['cerrada_por']}  \n"
                        f"**Verificación de eficacia programada:** "
                        f"{date.fromisoformat(c['fecha_verificacion_programada']):%d/%m/%Y}  \n"
                        f"**Criterio:** {c['criterio_verificacion']}")
            if c["aclaraciones"]:
                st.markdown(f"**Aclaraciones:** {c['aclaraciones']}")
            k_acta = f"acta_html_{nc_id}"
            if k_acta not in st.session_state:
                if st.button("📄 Preparar acta (HTML / PDF)", key=f"acta_{nc_id}"):
                    ok, html = _err(nc.acta_verificada, proyecto, nc_id)
                    if ok:
                        st.session_state[k_acta] = html
                        st.rerun()
            else:
                _descargas(st.session_state[k_acta], n["numero"], key=f"acta_dl_{nc_id}",
                           prefijo="Acta_cierre_NC")
                st.caption(f"SHA-256 verificado: `{c['sha256_html']}`")
    v = nc.verificacion(proyecto, nc_id)
    if v:
        st.markdown(f"**Verificación:** {v['resultado']} · {v['fecha']} · por {v['verificador']}"
                    f"  \n{v['evidencia_txt']}")

    envs = nc.envios(proyecto, nc_id)
    if envs:
        with st.expander(f"Envíos ({len(envs)})"):
            st.dataframe(pd.DataFrame([{
                "Fecha": e["enviado_en"], "Canal": e["canal"], "Resultado": e["resultado"],
                "Para": e["para"], "CC": e["cc"], "CCO": e["cco"], "Asunto": e["asunto"],
                "Por": e["enviado_por"], "Error": e["error"] or "",
            } for e in envs]), hide_index=True, width="stretch")

    adjs = nc.adjuntos(proyecto, nc_id)
    if adjs:
        with st.expander(f"Adjuntos ({len(adjs)})"):
            for a in adjs:
                c1, c2 = st.columns([3, 1])
                c1.caption(f"{a['etapa']} · {a['nombre']} · {a['subido_por']} · "
                           f"sha256 {a['sha256'][:16]}…")
                c2.download_button("Descargar", nc.leer_adjunto(proyecto, a), a["nombre"],
                                   key=f"adj_{a['id']}")

    with st.expander("Historial"):
        evs = nc.eventos(proyecto, "nc", nc_id)
        st.dataframe(pd.DataFrame([{
            "Fecha": e["ts"], "Usuario": e["usuario"], "Evento": e["tipo"],
            "De": e["estado_anterior"], "A": e["estado_nuevo"],
            "Datos": ", ".join(f"{k}: {v}" for k, v in e["datos"].items()
                               if k not in ("sha256_html", "sha256_contenido")),
            "Hash": e["hash"][:12],
        } for e in evs]), hide_index=True, width="stretch")

    _acciones(proyecto, cfg, actor, n)


def _form_mail(proyecto, actor, n, cierre: bool = False):
    """Redactar, previsualizar y enviar por mail la NC o, con `cierre`, su acta de
    cierre (Para / CC / CCO editables)."""
    nc_id = n["id"]
    k = f"mail_cierre_{nc_id}" if cierre else f"mail_{nc_id}"
    componer = nc_mail.componer_cierre if cierre else nc_mail.componer
    cfg = nc.cargar_config(proyecto)
    mcfg = nc_mail.config_mail(cfg)
    # Un borrador armado por una versión anterior de esta página (sin `redactado` /
    # `referencia`) se descarta junto con los campos editables que lo mostraban.
    if "redactado" not in st.session_state.get(f"{k}_borr", {}):
        for suf in ("_borr", "_asunto", "_cuerpo", "_firma", "_ok"):
            st.session_state.pop(f"{k}{suf}", None)
        ok, borr = _err(componer, proyecto, nc_id)
        if not ok:
            return
        st.session_state[f"{k}_borr"] = borr
    b = st.session_state[f"{k}_borr"]
    reenvio = n["estado"] != "EMITIDA"

    if cierre:
        st.caption(f"Desde **{mcfg['remitente']}**. Se adjunta el acta de cierre congelada; "
                   "enviarla no cambia el estado de la NC.")
    else:
        st.caption(f"Desde **{mcfg['remitente']}**. "
                   + ("Reenvío: los plazos ya corren desde la primera notificación."
                      if reenvio else "Al enviarse, la NC pasa a ENVIADA y los plazos corren "
                                      "desde este momento."))
    dest = st.data_editor(
        pd.DataFrame(b["destinatarios"] or [{"nombre": "", "mail": "", "para": True,
                                             "cc": False, "cco": False}]),
        num_rows="dynamic", width="stretch", hide_index=True, key=f"{k}_dest",
        column_config={
            "nombre": st.column_config.TextColumn("Nombre"),
            "mail": st.column_config.TextColumn("Mail", required=True),
            "para": st.column_config.CheckboxColumn("Para", default=False),
            "cc": st.column_config.CheckboxColumn("CC", default=False),
            "cco": st.column_config.CheckboxColumn("CCO", default=False),
        })
    st.caption("Sugeridos según los contactos «por defecto» de la agenda (📇 Contactos). "
               "Podés agregar filas a mano.")

    agenda = nc_contactos.listar(proyecto, solo_activos=True)
    grupos = nc_contactos.grupos(agenda)
    opciones = ([f"👥 {g} ({len(cs)})" for g, cs in grupos.items()]
                + [f"{c['nombre']} <{c['mail']}>" if c["nombre"] else c["mail"] for c in agenda])
    por_opcion = {f"👥 {g} ({len(cs)})": cs for g, cs in grupos.items()}
    por_opcion.update({(f"{c['nombre']} <{c['mail']}>" if c["nombre"] else c["mail"]): [c]
                       for c in agenda})
    if agenda:
        st.markdown("**⚡ Agregar rápido** — frecuentes primero")
        c1, c2, c3 = st.columns(3)
        rapido = {"para": c1.multiselect("Para", opciones, key=f"{k}_q_para"),
                  "cc": c2.multiselect("CC", opciones, key=f"{k}_q_cc"),
                  "cco": c3.multiselect("CCO", opciones, key=f"{k}_q_cco")}
    else:
        rapido = {}
        st.caption("Todavía no hay contactos: cargalos en 📇 Contactos o tildá «guardar» "
                   "al enviar.")
    c1, c2 = st.columns([4, 1])
    c1.caption(f"Texto completado con los datos de la NC (redactado {b['redactado'][11:16]} del "
               f"{b['redactado'][8:10]}/{b['redactado'][5:7]}). Editalo libremente.")
    if c2.button("🔄 Regenerar texto", key=f"{k}_regen",
                 help="Vuelve a armar asunto, texto y firma desde la plantilla "
                      "(Configuración → Mail), con los datos y plazos de este momento."):
        for suf in ("_borr", "_asunto", "_cuerpo", "_firma", "_ok"):
            st.session_state.pop(f"{k}{suf}", None)
        st.rerun()
    asunto = st.text_input("Asunto", value=b["asunto"], key=f"{k}_asunto")
    cuerpo = st.text_area("Texto", value=b["cuerpo"], height=380, key=f"{k}_cuerpo")
    firma = st.text_area("Firma", value=b["firma"], height=110, key=f"{k}_firma")
    with st.expander("Marcadores (también se pueden escribir en el texto)"):
        st.markdown("\n".join(f"- `{{{m}}}` — {d}" for m, d in nc_mail.MARCADORES.items()))

    destinatarios = [r for r in dest.astype(object).where(dest.notna(), None).to_dict("records")
                     if r.get("mail")]
    for col, elegidos in rapido.items():
        for o in elegidos:
            destinatarios += [{"nombre": c["nombre"], "mail": c["mail"], "para": col == "para",
                               "cc": col == "cc", "cco": col == "cco"} for c in por_opcion[o]]
    para, cc, cco = nc_mail.separar(destinatarios)
    prev = nc_mail.previsualizar(proyecto, nc_id, asunto, cuerpo, firma, cierre=cierre)
    adjunto_txt = (f"Acta de cierre NC {n['numero']}.pdf" if cierre
                   else f"No conformidad {n['numero']}.pdf")
    with st.container(border=True):
        st.markdown(f"**De:** {mcfg['nombre_remitente']} <{mcfg['remitente']}>  \n"
                    f"**Para:** {', '.join(para) or '—'}  \n"
                    f"**CC:** {', '.join(cc) or '—'}  \n"
                    f"**CCO:** {', '.join(cco) or '—'}  \n"
                    f"**Asunto:** {prev['asunto']}  \n"
                    f"**Adjunto:** {adjunto_txt}")
        st.text(prev["texto"])

    tiene_pwd = bool(nc_mail.password(mcfg["remitente"]))
    if not tiene_pwd:
        st.warning("Falta la contraseña de aplicación de Gmail: cargala en ⚙ Configuración → "
                   "Mail (solo en la app de administración).")
    if not pdf.disponible():
        st.warning("No se encontró Edge ni Chrome en el servidor para generar el PDF.")
    conocidos = {c["mail"] for c in nc_contactos.listar(proyecto)}
    nuevos = sorted({d["mail"].strip().lower() for d in destinatarios
                     if d.get("mail") and d["mail"].strip().lower() not in conocidos})
    guardar_nuevos = False
    if nuevos:
        guardar_nuevos = st.checkbox(f"Guardar en la agenda los mails nuevos: {', '.join(nuevos)}",
                                     value=True, key=f"{k}_guardar_nuevos")
    confirmo = st.checkbox("Revisé destinatarios, texto y adjunto", key=f"{k}_ok")
    if st.button("📨 Enviar mail", type="primary", key=f"{k}_enviar",
                 disabled=not (confirmo and tiene_pwd and pdf.disponible() and para)):
        with st.spinner("Generando PDF y enviando…"):
            if cierre:
                ok, r = _err(nc_mail.enviar_cierre, proyecto, actor, nc_id, destinatarios,
                             asunto, cuerpo, firma, guardar_nuevos=guardar_nuevos)
                if ok:
                    r = {**r, "reenvio": True}
            else:
                ok, r = _err(nc_mail.enviar_nc, proyecto, actor, nc_id, destinatarios, asunto,
                             cuerpo, firma, guardar_nuevos=guardar_nuevos,
                             referencia=b.get("referencia"))
        if ok:
            for suf in ("_borr", "_dest", "_asunto", "_cuerpo", "_firma", "_ok", "_q_para",
                        "_q_cc", "_q_cco", "_guardar_nuevos"):
                st.session_state.pop(f"{k}{suf}", None)
            st.session_state["nc_mail_ok"] = (
                ("Acta de cierre enviada a " if cierre else "Mail enviado a ")
                + ", ".join(para + cc)
                + (f" (+{len(cco)} en CCO)" if cco else "")
                + ("" if r["reenvio"] else
                   f". Descargo hasta el {r['plazos']['fecha_limite_descargo']}.")
                + (f" Agenda: {len(r['contactos_nuevos'])} contacto(s) nuevo(s)."
                   if r["contactos_nuevos"] else ""))
            st.rerun()


def _acciones(proyecto, cfg, actor, n):
    nc_id, est = n["id"], n["estado"]
    st.markdown("#### Acciones")
    if "nc_mail_ok" in st.session_state:
        st.success(st.session_state.pop("nc_mail_ok"))

    if est == "EMITIDA" and actor.tiene("nc_emitir"):
        t_mail, t_manual = st.tabs(["✉ Enviar por mail", "📝 Registrar notificación manual"])
        with t_mail:
            _form_mail(proyecto, actor, n)
        with t_manual, st.form(f"envio_{nc_id}"):
            st.markdown("**Registrar notificación a la sucursal** (en mano, WhatsApp, otro "
                        "medio) — fija los plazos")
            c1, c2 = st.columns(2)
            medio = c1.selectbox("Medio", ["Correo electrónico", "WhatsApp", "En mano", "Otro"])
            dest = c2.text_input("Destinatario")
            c1, c2 = st.columns(2)
            d = c1.date_input("Fecha de recepción", value=date.today(), format="DD/MM/YYYY")
            h = c2.time_input("Hora de recepción", value=time(9, 0))
            adj = st.file_uploader("NC firmada (opcional)", key=f"env_adj_{nc_id}")
            if st.form_submit_button("📨 Registrar envío"):
                ok, p = _err(nc.registrar_envio, proyecto, actor, nc_id, medio, dest.strip(),
                             datetime.combine(d, h), _adjunto(adj))
                if ok:
                    st.success(f"Descargo hasta {p['fecha_limite_descargo']}.")
                    st.rerun()

    if est in nc_mail.ESTADOS_REENVIO and actor.tiene("nc_emitir"):
        with st.expander("✉ Reenviar por mail (no cambia los plazos)"):
            _form_mail(proyecto, actor, n)

    if est == "ENVIADA" and actor.tiene("nc_responder"):
        with st.form(f"resp_{nc_id}"):
            st.markdown("**Cargar descargo de la sucursal (Reporte de Corrección)**")
            st.caption("Corrección = arregla este caso (ej. se recontó y ajustó). "
                       "Acción correctiva = cambia algo para que no se repita.")
            corr = st.text_area("Corrección inmediata (contención)")
            causa = st.text_area("Causa raíz")
            cat = st.selectbox("Categoría 6M", nc.CATEGORIAS_6M)
            lim = nc.limite_implementacion(n)
            if lim:
                st.caption(f"Máximo de implementación: {lim:%d/%m/%Y}")
            acc = st.data_editor(
                pd.DataFrame({"descripcion": [""], "responsable": [n["responsable_accion"]],
                              "fecha_compromiso": [lim or date.today()]}),
                num_rows="dynamic", width="stretch", key=f"resp_acc_{nc_id}",
                column_config={"fecha_compromiso": st.column_config.DateColumn(
                    "fecha_compromiso", format="DD/MM/YYYY")})
            adj = st.file_uploader("Descargo recibido (opcional)", key=f"resp_adj_{nc_id}")
            if st.form_submit_button("💬 Registrar descargo"):
                acciones = acc.dropna(subset=["descripcion"]).to_dict("records")
                ok, avisos = _err(nc.registrar_respuesta, proyecto, actor, nc_id, corr, causa,
                                  cat, acciones, _adjunto(adj))
                if ok:
                    for a in avisos:
                        st.warning(a)
                    st.rerun()

    if est == "RESPONDIDA" and actor.tiene("nc_emitir"):
        c1, c2 = st.columns(2)
        with c1.form(f"acept_{nc_id}"):
            st.markdown("**Aceptar descargo** → las acciones quedan en implementación")
            if st.form_submit_button("✔ Aceptar"):
                if _err(nc.aceptar_descargo, proyecto, actor, nc_id)[0]:
                    st.rerun()
        with c2.form(f"obs_{nc_id}"):
            st.markdown("**Observar descargo** → vuelve a la sucursal con plazo nuevo")
            motivo = st.text_area("Motivo")
            adj = st.file_uploader("Adjunto (opcional)", key=f"obs_adj_{nc_id}")
            if st.form_submit_button("↩ Observar"):
                ok, nuevo = _err(nc.observar_descargo, proyecto, actor, nc_id, motivo,
                                 _adjunto(adj))
                if ok:
                    st.success(f"Nuevo plazo de descargo: {nuevo}")
                    st.rerun()

    if est in nc.ESTADOS_ACEPTADA:
        _implementacion_y_cierre(proyecto, cfg, actor, n)

    if est in nc.ESTADOS_CON_ACTA and actor.tiene("nc_emitir"):
        with st.expander("✉ Enviar acta de cierre por mail"):
            _form_mail(proyecto, actor, n, cierre=True)

    if est in nc.ESTADOS_VERIFICACION_PENDIENTE and actor.tiene("nc_verificar"):
        with st.form(f"verif_{nc_id}"):
            st.markdown("**Verificar eficacia** → cierra la NC para el sistema de gestión")
            c1, c2 = st.columns(2)
            f = c1.date_input("Fecha de verificación", value=date.today(), format="DD/MM/YYYY")
            res = c2.radio("Resultado", ["EFICAZ", "NO_EFICAZ"], horizontal=True)
            evid = st.text_area("Evidencia (ej. merma del inventario siguiente del mismo rubro)")
            adj = st.file_uploader("Adjunto (opcional)", key=f"ver_adj_{nc_id}")
            if st.form_submit_button("🔎 Registrar verificación"):
                ok, nueva = _err(nc.verificar_eficacia, proyecto, actor, nc_id, f, res, evid,
                                 _adjunto(adj))
                if ok:
                    if nueva:
                        st.warning(f"No eficaz: se creó el borrador #{nueva} vinculado.")
                    st.rerun()

    if est in nc.ESTADOS_ABIERTOS and (
            actor.tiene("nc_anular") or (est == "EMITIDA" and actor.tiene("nc_emitir"))):
        with st.expander("🚫 Anular"):
            with st.form(f"anular_{nc_id}"):
                motivo = st.text_area("Motivo (obligatorio)")
                aviso = ""
                if est != "EMITIDA":
                    aviso = st.text_input("Cómo se le avisó a la sucursal (obligatorio)")
                reemitir = st.checkbox("Crear borrador de reemplazo", value=True)
                if st.form_submit_button("Anular NC"):
                    ok, nueva = _err(nc.anular, proyecto, actor, nc_id, motivo, aviso, reemitir)
                    if ok:
                        if nueva:
                            st.info(f"Borrador de reemplazo #{nueva} en ➕ Nueva NC.")
                        st.rerun()


def _implementacion_y_cierre(proyecto, cfg, actor, n):
    """ACEPTADA: registrar la implementación de cada acción y cerrar con la sucursal."""
    nc_id = n["id"]
    acciones = nc.acciones_con_implementacion(proyecto, nc_id)
    pendientes = [a for a in acciones if a["implementacion"] is None]
    st.markdown(f"**Implementación de acciones correctivas** — "
                f"{len(acciones) - len(pendientes)} de {len(acciones)} implementadas")

    if pendientes and actor.tiene("nc_responder"):
        with st.form(f"impl_{nc_id}"):
            st.caption("Transcribí lo que informa la sucursal sobre cada acción.")
            etiquetas = {f"{a['descripcion'][:70]} · {a['responsable']} · compromiso "
                         f"{a['fecha_compromiso']}": a["id"] for a in pendientes}
            elegida = st.selectbox("Acción", list(etiquetas))
            f = st.date_input("Fecha de implementación", value=date.today(),
                              max_value=date.today(), format="DD/MM/YYYY")
            evid = st.text_area("Evidencia (qué se hizo y cómo se comprueba)")
            adj = st.file_uploader("Evidencia adjunta (opcional)", key=f"impl_adj_{nc_id}")
            if st.form_submit_button("🛠 Registrar implementación"):
                if _err(nc.registrar_implementacion, proyecto, actor, nc_id, etiquetas[elegida],
                        f, evid, _adjunto(adj))[0]:
                    st.rerun()
    elif pendientes:
        st.caption("Falta registrar la implementación (rol nc_responder).")

    if not actor.tiene("nc_emitir"):
        return
    if pendientes:
        st.info("Para cerrar con la sucursal tienen que estar implementadas todas las acciones.")
        return
    with st.form(f"cierre_{nc_id}"):
        st.markdown("**Cerrar con la sucursal** → se congela el acta y la verificación de "
                    "eficacia queda programada")
        sugerida = date.today() + timedelta(days=nc.dias_verificacion(cfg, n["tipo"]))
        fv = st.date_input("Verificación de eficacia programada para", value=sugerida,
                           min_value=date.today() + timedelta(days=1), format="DD/MM/YYYY")
        criterio = st.text_area("Criterio de verificación (métrica y meta)",
                                value=nc.criterio_sugerido(n))
        aclar = st.text_area("Aclaraciones de cierre (opcional, van en el acta)")
        st.caption("El acta incluye el descargo aceptado, la implementación de cada acción y "
                   "los adjuntos del descargo (nombre y huella). Después se puede descargar y "
                   "enviar por mail.")
        if st.form_submit_button("📬 Cerrar con la sucursal", type="primary"):
            ok, _ = _err(nc.cerrar_con_sucursal, proyecto, actor, nc_id, fv, criterio, aclar)
            if ok:
                st.session_state["nc_mail_ok"] = ("NC cerrada con la sucursal. Acta lista para "
                                                  "descargar y enviar.")
                st.rerun()


# ---------------------------------------------------------------------------
# Observaciones
# ---------------------------------------------------------------------------

def _tab_contactos(proyecto, actor):
    st.caption("Agenda para notificar NC por mail. «Por defecto» = sale tildado así al redactar; "
               "si tiene sucursal, solo para las NC de esa sucursal. El grupo sirve para agregar "
               "a varios de una vez. Frecuentes primero.")
    agenda = nc_contactos.listar(proyecto)
    suc = _sucursales(proyecto)
    depos = [""] + [str(c) for c in suc["codigodepo"]]
    nombres_suc = dict(zip(suc["codigodepo"].astype(str), suc["nombre"]))
    df = pd.DataFrame(agenda, columns=["nombre", "mail", "codigodepo", "grupo", "por_defecto",
                                       "activo", "usos", "ultimo_uso"])
    df["activo"] = df["activo"].fillna(1).astype(bool)
    df["por_defecto"] = df["por_defecto"].fillna("")
    puede = actor.tiene("nc_emitir")
    ed = st.data_editor(
        df, num_rows="dynamic" if puede else "fixed", disabled=not puede, width="stretch",
        hide_index=True, key="contactos_ed",
        column_config={
            "nombre": st.column_config.TextColumn("Nombre"),
            "mail": st.column_config.TextColumn("Mail", required=True),
            "codigodepo": st.column_config.SelectboxColumn(
                "Sucursal", options=depos,
                help="Vacío = todas. " + ", ".join(f"{k} {v}" for k, v in nombres_suc.items())),
            "grupo": st.column_config.TextColumn("Grupo"),
            "por_defecto": st.column_config.SelectboxColumn(
                "Por defecto", options=list(nc_contactos.POR_DEFECTO),
                help="para / cc / cco, o vacío"),
            "activo": st.column_config.CheckboxColumn("Activo", default=True),
            "usos": st.column_config.NumberColumn("Usos", disabled=True),
            "ultimo_uso": st.column_config.TextColumn("Último uso", disabled=True),
        })
    if puede and st.button("💾 Guardar contactos", type="primary", key="contactos_guardar"):
        filas = ed.astype(object).where(ed.notna(), None).to_dict("records")
        ok, n = _err(nc_contactos.guardar_todos, proyecto, actor, filas)
        if ok:
            st.success(f"{n} contactos guardados.")
            st.session_state.pop("contactos_ed", None)
            st.rerun()


def _tab_observaciones(proyecto, actor):
    st.caption("SOP 8.1: recomendaciones que no requieren pasos adicionales. Registro "
               "simple, sin descargo ni verificación.")
    if actor.tiene("nc_emitir"):
        suc = _sucursales(proyecto)
        etiquetas = [f"{r.codigodepo} · {r.nombre}" for r in suc.itertuples()]
        with st.form("obs_nueva", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            s = c1.selectbox("Sucursal", etiquetas)
            f = c2.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")
            arts = sop.buscar(proyecto, "", limite=1000)
            a = c3.selectbox("Punto del SOP (opcional)",
                             ["—"] + [f"{x['codigo']} — {x['titulo']}" for x in arts])
            texto = st.text_area("Observación")
            if st.form_submit_button("Registrar observación") and etiquetas:
                fila = suc.iloc[etiquetas.index(s)]
                art = arts[[f"{x['codigo']} — {x['titulo']}" for x in arts].index(a)] \
                    if a != "—" else None
                ok, numero = _err(nc.registrar_observacion, proyecto, actor,
                                  fila["codigodepo"], fila["nombre"], f, texto,
                                  art["codigo"] if art else None, art["titulo"] if art else None,
                                  art["version_id"] if art else None)
                if ok:
                    st.success(f"Observación {numero} registrada.")
    obs = nc.listar_observaciones(proyecto)
    if obs:
        st.dataframe(pd.DataFrame(obs)[["numero", "fecha", "sucursal_nombre", "req_codigo",
                                        "texto", "creada_por"]],
                     hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# SOP
# ---------------------------------------------------------------------------

def _tab_sop(proyecto, actor):
    vs = sop.versiones(proyecto)
    if vs:
        st.dataframe(pd.DataFrame(vs)[["id", "version", "estado", "vigencia_desde", "archivo",
                                       "n", "metodo_parseo", "cargado_por", "cargado_en"]]
                     .rename(columns={"n": "artículos"}), hide_index=True, width="stretch")
    if not actor.tiene("nc_emitir"):
        return

    with st.form("sop_carga"):
        st.markdown("**Cargar versión del SOP (.docx)**")
        archivo = st.file_uploader("Archivo", type=["docx"])
        c1, c2 = st.columns(2)
        version = c1.text_input("Versión")
        vig = c2.date_input("Vigente desde", value=date.today(), format="DD/MM/YYYY")
        if st.form_submit_button("Leer y crear borrador") and archivo is not None:
            ok, res = _err(sop.cargar_version, proyecto, actor, archivo.name,
                           archivo.getvalue(), version, vig)
            if ok:
                st.success(f"Borrador creado: {res[2]} artículos (método: {res[1]}). "
                           "Revisalo abajo antes de activarlo.")
                st.rerun()

    for v in [x for x in vs if x["estado"] == "BORRADOR"]:
        st.markdown(f"#### Revisión — versión {v['version']} (borrador #{v['id']})")
        contenido = (nc.dir_proyecto(proyecto) / v["ruta"]).read_bytes()
        arts = sop.articulos(proyecto, v["id"])
        difs = sop.comparar_con_indice(contenido, arts)
        if difs:
            st.warning("El índice del documento no coincide con el cuerpo (suele ser un "
                       "índice sin actualizar). Se usa el cuerpo:\n\n- " + "\n- ".join(difs))
        ed = st.data_editor(pd.DataFrame(arts)[["codigo", "titulo", "texto"]],
                            num_rows="dynamic", width="stretch", key=f"sop_ed_{v['id']}")
        c1, c2, c3 = st.columns(3)
        if c1.button("💾 Guardar correcciones", key=f"sop_g_{v['id']}"):
            if _err(sop.reemplazar_articulos, proyecto, actor, v["id"],
                    ed.fillna("").to_dict("records"))[0]:
                st.rerun()
        if c2.button("✅ Activar esta versión", type="primary", key=f"sop_a_{v['id']}"):
            if _err(sop.activar_version, proyecto, actor, v["id"])[0]:
                st.rerun()
        if c3.button("🗑 Descartar", key=f"sop_d_{v['id']}"):
            if _err(sop.descartar_borrador, proyecto, actor, v["id"])[0]:
                st.rerun()


# ---------------------------------------------------------------------------
# Integridad
# ---------------------------------------------------------------------------

def _tab_integridad(proyecto):
    a = nc.ancla(proyecto)
    if a:
        st.markdown("**Ancla de la cadena** — copiala o mandala por mail fuera del sistema; "
                    "si alguien reescribe el historial, deja de coincidir.")
        st.code(f"MermaIQ NC · {proyecto} · evento {a['id']} de {a['eventos']} · {a['ts']}\n"
                f"{a['hash']}", language=None)
    if st.button("🛡 Verificar integridad"):
        ok, probs = nc.verificar_cadena(proyecto)
        probs += (nc.verificar_html_emitidos(proyecto) + nc.verificar_actas(proyecto)
                  + nc.verificar_adjuntos(proyecto))
        if not probs:
            st.success("Cadena de eventos, HTML emitidos, actas de cierre y adjuntos: "
                       "todo coincide.")
        for p in probs:
            st.error(p)
    if not modo_solo_lectura() and st.button("💾 Backup NC"):
        db, z = nc.backup(proyecto)
        st.success(f"Backup generado:\n\n- {db}\n- {z}")


# ---------------------------------------------------------------------------
# Configuración (solo admin)
# ---------------------------------------------------------------------------

def _config_mail(proyecto, cfg):
    st.markdown("#### ✉ Mail")
    m = nc_mail.config_mail(cfg)
    c1, c2 = st.columns(2)
    remitente = c1.text_input("Remitente (cuenta de Gmail)", m["remitente"], key="mc_rem")
    nombre = c2.text_input("Nombre visible", m["nombre_remitente"], key="mc_nom")
    c1, c2, c3 = st.columns([2, 1, 3])
    host = c1.text_input("Servidor SMTP", m["smtp_host"], key="mc_host")
    port = c2.number_input("Puerto", 1, 65535, int(m["smtp_port"]), key="mc_port")

    with c3:
        tiene = bool(nc_mail.password(remitente))
        pwd = st.text_input("Contraseña de aplicación de Gmail "
                            + ("(configurada ✓)" if tiene else "(sin configurar)"),
                            type="password", key="mc_pwd",
                            help="Cuenta de Google → Seguridad → Verificación en dos pasos → "
                                 "Contraseñas de aplicaciones. Se guarda en auth/smtp.json, "
                                 "fuera de git y fuera de nc_config.json.")
    c1, c2 = st.columns(2)
    if pwd and not nc_mail.parece_password_de_aplicacion(pwd):
        st.warning("Eso no parece una contraseña de aplicación de Google (son 16 letras "
                   "minúsculas, en 4 grupos de 4). La contraseña con la que entrás a Gmail "
                   "no sirve para enviar desde IQMerma.")
    if c1.button("Guardar contraseña", disabled=not pwd, key="mc_pwd_g"):
        nc_mail.guardar_password(remitente, pwd)
        st.success("Contraseña guardada.")
    if c2.button("Probar conexión (no envía nada)", key="mc_probar"):
        clave = pwd or nc_mail.password(remitente)
        if not clave:
            st.error("Cargá la contraseña primero.")
        else:
            try:
                nc_mail.probar_conexion({**m, "remitente": remitente, "smtp_host": host,
                                         "smtp_port": port}, clave.replace(" ", ""))
                st.success("Gmail aceptó el usuario y la contraseña.")
            except Exception as e:  # noqa: BLE001
                st.error("Falló la conexión: " + nc_mail.explicar_error(e))

    asunto = st.text_input("Asunto por defecto", m["asunto"], key="mc_asunto")
    cuerpo = st.text_area("Texto por defecto", m["cuerpo"], height=300, key="mc_cuerpo")
    firma = st.text_area("Firma por defecto", m["firma"], height=100, key="mc_firma")
    asunto_c = st.text_input("Asunto del acta de cierre", m["asunto_cierre"], key="mc_asunto_c")
    cuerpo_c = st.text_area("Texto del acta de cierre", m["cuerpo_cierre"], height=260,
                            key="mc_cuerpo_c")
    st.caption("Marcadores: " + ", ".join(f"`{{{x}}}`" for x in nc_mail.MARCADORES))

    acuse = st.number_input("Horas para confirmar recepción y devolver firmada",
                            1, 240, int(m.get("acuse_horas") or 48), key="mc_acuse")
    st.caption("Los destinatarios frecuentes se manejan en la pestaña 📇 Contactos.")

    if st.button("💾 Guardar configuración de mail", type="primary", key="mc_guardar"):
        if not nc_mail.mail_valido(remitente):
            st.error("El remitente no es un mail válido.")
            return
        nc_mail.guardar_config_mail(proyecto, {
            "remitente": remitente.strip(), "nombre_remitente": nombre.strip(),
            "smtp_host": host.strip(), "smtp_port": int(port), "asunto": asunto,
            "cuerpo": cuerpo, "firma": firma, "acuse_horas": int(acuse),
            "asunto_cierre": asunto_c, "cuerpo_cierre": cuerpo_c,
        })
        st.success("Configuración de mail guardada.")


def _tab_config(proyecto, cfg):
    _config_mail(proyecto, cfg)
    st.markdown("---")
    st.markdown("#### Tipos, plazos y catálogos")
    st.caption(f"projects/{proyecto}/nc_config.json — tipos y plazos, catálogos, días "
               "hábiles (sabado_habil, provincia_por_deposito con subdivisiones de "
               "`holidays` AR, feriados_extra), la regla verificador_distinto y, por "
               "tipo, verificacion_dias (días sugeridos entre el cierre con la sucursal y "
               "la verificación de eficacia). Los "
               "cambios no afectan NC ya emitidas: sus plazos quedan congelados.")
    import json
    sin_mail = {kk: v for kk, v in cfg.items() if kk != "mail"}
    texto = st.text_area("Configuración", json.dumps(sin_mail, ensure_ascii=False, indent=2),
                         height=520, key="nc_cfg_txt")
    if st.button("Guardar configuración"):
        try:
            nuevo = json.loads(texto)
            for t, d in nuevo["tipos"].items():
                for campo in ("severidad", "color", "descargo_dias_habiles"):
                    if campo not in d:
                        raise ValueError(f"Al tipo {t} le falta '{campo}'.")
            nuevo["mail"] = nc.cargar_config(proyecto).get("mail", {})
            nc.guardar_config(proyecto, nuevo)
            st.success("Configuración guardada.")
        except Exception as e:  # noqa: BLE001
            st.error(f"No se guardó: {e}")
