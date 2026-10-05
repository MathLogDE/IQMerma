"""
Tests de core/no_conformidades.py y core/sop.py (verificación v1 del plan de NC).

Correr con:
    python -m unittest tests.test_no_conformidades -v
El test del SOP real se saltea si no existe el archivo (ver SOP_REAL).
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import no_conformidades as nc  # noqa: E402
from core import nc_contactos  # noqa: E402
from core import nc_mail  # noqa: E402
from core import pdf  # noqa: E402
from core import sop  # noqa: E402

SOP_REAL = Path(os.environ.get("SOP_REAL", r"F:\Descargas\SOP Rio Shop v2.docx"))

AUDITOR = nc.Actor("auditor", "MATHIAS VILLANUEVA", frozenset({"nc_emitir", "nc_responder"}))
VERIF = nc.Actor("verif", "OTRA PERSONA", frozenset({"nc_verificar"}))
ADMIN = nc.actor_admin()
LECTOR = nc.Actor("lector", "Solo mira", frozenset())


def _datos(**extra):
    d = {"codigodepo": "34", "sucursal_nombre": "SANTIAGO DEL ESTERO", "fecha": date(2026, 9, 23),
         "area": "CONTROL DE INVENTARIO", "tipo": "ROJO", "responsable_accion": "Encargado",
         "intro": "Durante el control…", "req_codigo": "9.2.12",
         "req_titulo": "Cierre de inventario y archivo", "req_texto": "Una vez cumplidos…",
         "sop_version": "2", "evidencia_txt": "Tinta Cyan: informa 1, real 5.",
         "desvio_txt": "El conteo no es verídico.",
         "impactos": ["PERDIDA DE TRAZABILIDAD", "PERDIDAS ECONOMICAS"],
         "acciones_recomendadas": ["Nuevo conteo"]}
    d.update(extra)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def emitida(self, **extra):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos(**extra))
        nc.emitir(self.dir, AUDITOR, i)
        return i

    def respondida(self, recepcion=datetime(2026, 9, 25, 10, 0)):
        i = self.emitida()
        nc.registrar_envio(self.dir, AUDITOR, i, "mail", "encargado@x", recepcion)
        nc.registrar_respuesta(self.dir, AUDITOR, i, "Se recontó", "Conteo apurado",
                               "Mano de obra",
                               [{"descripcion": "Capacitar", "responsable": "Encargado",
                                 "fecha_compromiso": date(2026, 10, 10)}])
        return i


class TestNumeracion(Base):
    def test_correlativo_por_sucursal_y_mes(self):
        a = nc.crear_borrador(self.dir, AUDITOR, _datos())
        b = nc.crear_borrador(self.dir, AUDITOR, _datos())
        c = nc.crear_borrador(self.dir, AUDITOR, _datos(codigodepo="7"))
        d = nc.crear_borrador(self.dir, AUDITOR, _datos(fecha=date(2026, 10, 1)))
        self.assertEqual(nc.emitir(self.dir, AUDITOR, a), "01034092026")
        self.assertEqual(nc.emitir(self.dir, AUDITOR, b), "02034092026")
        self.assertEqual(nc.emitir(self.dir, AUDITOR, c), "01007092026")
        self.assertEqual(nc.emitir(self.dir, AUDITOR, d), "01034102026")
        self.assertEqual(nc.numero_impreso("02034092026"), "02 034 092026")

    def test_anulada_no_reutiliza_numero_y_reemite(self):
        hoy = date.today()
        a = self.emitida(fecha=hoy)
        nueva = nc.anular(self.dir, AUDITOR, a, "Error de tipeo", reemitir=True)
        self.assertEqual(nc.obtener(self.dir, nueva)["reemplaza_a"], a)
        self.assertEqual(nc.obtener(self.dir, a)["numero"], f"01034{hoy:%m%Y}")
        self.assertEqual(nc.emitir(self.dir, AUDITOR, nueva), f"02034{hoy:%m%Y}")

    def test_emisiones_simultaneas(self):
        ids = [nc.crear_borrador(self.dir, AUDITOR, _datos()) for _ in range(6)]
        barrera, numeros, errores = threading.Barrier(len(ids)), [], []

        def emitir(i):
            barrera.wait()
            try:
                numeros.append(nc.emitir(self.dir, AUDITOR, i))
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos = [threading.Thread(target=emitir, args=(i,)) for i in ids]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(sorted(numeros), [f"{k:02d}034092026" for k in range(1, 7)])

    def test_faltan_datos(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos(req_codigo=None))
        with self.assertRaises(nc.NCError):
            nc.emitir(self.dir, AUDITOR, i)

    def test_sin_rol_no_emite(self):
        with self.assertRaises(nc.NCError):
            nc.crear_borrador(self.dir, LECTOR, _datos())


class TestInmutabilidad(Base):
    def test_eventos_append_only(self):
        self.emitida()
        conn = nc.conectar(self.dir)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE nc_eventos SET usuario = 'x'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM nc_eventos")

    def test_respuesta_append_only(self):
        self.respondida()
        conn = nc.conectar(self.dir)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE nc_respuesta SET causa_raiz = 'otra'")

    def test_contenido_congelado(self):
        i = self.emitida()
        conn = nc.conectar(self.dir)
        for sql in ("UPDATE nc SET evidencia_txt = 'otra' WHERE id = ?",
                    "UPDATE nc SET html_emitido = 'x' WHERE id = ?",
                    "UPDATE nc SET fecha_limite_descargo = '2030-01-01' WHERE id = ?",
                    "UPDATE nc SET estado = 'CERRADA' WHERE id = ?",
                    "DELETE FROM nc WHERE id = ?"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, (i,))

    def test_envio_puede_fijar_plazos(self):
        i = self.emitida()
        nc.registrar_envio(self.dir, AUDITOR, i, "mail", "x", datetime(2026, 9, 25, 9, 0))
        self.assertIsNotNone(nc.obtener(self.dir, i)["fecha_limite_descargo"])

    def test_cadena_detecta_alteracion(self):
        self.respondida()
        ok, _ = nc.verificar_cadena(self.dir)
        self.assertTrue(ok)
        conn = nc.conectar(self.dir)
        conn.execute("DROP TRIGGER nc_eventos_no_update")
        conn.execute("UPDATE nc_eventos SET usuario = 'otro' WHERE id = 2")
        conn.close()
        ok, problemas = nc.verificar_cadena(self.dir)
        self.assertFalse(ok)
        self.assertIn("Evento 2", problemas[0])

    def test_ancla(self):
        self.emitida()
        a = nc.ancla(self.dir)
        self.assertEqual(a["eventos"], 2)
        self.assertEqual(len(a["hash"]), 64)


class TestReimpresion(Base):
    def test_mismo_hash(self):
        i = self.emitida()
        html = nc.html_para_reimprimir(self.dir, AUDITOR, i)
        self.assertEqual(nc.sha256_bytes(html.encode()), nc.obtener(self.dir, i)["sha256_html"])
        self.assertIn("02 034 092026".replace("02", "01", 1), html)
        self.assertIn("TIPO DE NO CONFORMIDAD: ROJO", html)
        self.assertIn("3 días hábiles", html)

    def test_html_alterado_bloquea(self):
        i = self.emitida()
        conn = nc.conectar(self.dir)
        conn.execute("DROP TRIGGER nc_congelada")
        conn.execute("UPDATE nc SET html_emitido = html_emitido || ' ' WHERE id = ?", (i,))
        conn.close()
        with self.assertRaises(nc.NCError):
            nc.html_para_reimprimir(self.dir, AUDITOR, i)
        self.assertTrue(nc.verificar_html_emitidos(self.dir))

    def test_escapa_html(self):
        i = self.emitida(evidencia_txt="<script>alert(1)</script>")
        self.assertNotIn("<script>alert", nc.obtener(self.dir, i)["html_emitido"])


class TestPlazos(Base):
    VIERNES = datetime(2026, 10, 2, 15, 30)  # viernes

    def test_rojo_notificado_viernes(self):
        i = self.emitida()
        p = nc.registrar_envio(self.dir, AUDITOR, i, "mail", "x", self.VIERNES)
        self.assertEqual(p["fecha_limite_descargo"], "2026-10-07")      # lun-mar-mié
        self.assertEqual(p["fecha_limite_contencion"], "2026-10-04T15:30")  # 48 h corridas

    def test_feriado_provincial_y_sabado(self):
        cfg = nc.cargar_config(self.dir)
        cfg["provincia_por_deposito"] = {"034": "G"}
        cfg["feriados_extra"] = {"G": ["2026-10-06"]}
        nc.guardar_config(self.dir, cfg)
        self.assertEqual(nc.sumar_dias_habiles(date(2026, 10, 2), 3, cfg, "034"),
                         date(2026, 10, 8))
        cfg["sabado_habil"] = True
        self.assertEqual(nc.sumar_dias_habiles(date(2026, 10, 2), 3, cfg, "034"),
                         date(2026, 10, 7))  # sáb 3, lun 5, (mar 6 feriado), mié 7

    def test_amarillo_sin_contencion_y_naranja_con_descargo(self):
        a = self.emitida(tipo="AMARILLO")
        p = nc.registrar_envio(self.dir, AUDITOR, a, "mail", "x", self.VIERNES)
        self.assertIsNone(p["fecha_limite_contencion"])
        n = self.emitida(tipo="NARANJA")
        p = nc.registrar_envio(self.dir, AUDITOR, n, "mail", "x", self.VIERNES)
        self.assertEqual(p["fecha_limite_contencion"], p["fecha_limite_descargo"] + "T23:59")

    def test_aviso_implementacion(self):
        i = self.emitida()
        nc.registrar_envio(self.dir, AUDITOR, i, "mail", "x", self.VIERNES)
        avisos = nc.registrar_respuesta(
            self.dir, AUDITOR, i, "c", "r", "Método",
            [{"descripcion": "A", "responsable": "E", "fecha_compromiso": date(2026, 12, 1)}])
        self.assertEqual(len(avisos), 1)

    def test_vencimientos_derivados(self):
        i = self.emitida()
        nc.registrar_envio(self.dir, AUDITOR, i, "mail", "x", self.VIERNES)
        fila = [r for r in nc.listar(self.dir, ahora=datetime(2026, 10, 9, 12)) if r["id"] == i][0]
        self.assertTrue(any("DESCARGO VENCIDO · 2 d" in v for v in fila["vencimientos"]))
        self.assertTrue(any("CONTENCIÓN VENCIDA" in v for v in fila["vencimientos"]))


class TestCiclo(Base):
    def test_descargo_observado(self):
        i = self.respondida()
        nc.observar_descargo(self.dir, AUDITOR, i, "No identifica la causa raíz")
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "ENVIADA")
        nc.registrar_respuesta(self.dir, AUDITOR, i, "c2", "r2", "Método",
                               [{"descripcion": "B", "responsable": "E",
                                 "fecha_compromiso": "2026-10-20"}])
        rs = nc.respuestas(self.dir, i)
        self.assertEqual([r["intento"] for r in rs], [1, 2])
        self.assertTrue(rs[0]["observada"])
        self.assertEqual(rs[0]["causa_raiz"], "Conteo apurado")
        self.assertFalse(rs[1]["observada"])

    def test_observar_sin_motivo(self):
        i = self.respondida()
        with self.assertRaises(nc.NCError):
            nc.observar_descargo(self.dir, AUDITOR, i, "  ")

    def test_no_cierra_sin_verificacion_ni_mismo_usuario(self):
        i = self.respondida()
        with self.assertRaises(nc.NCError):   # todavía no está EN_VERIFICACION
            nc.verificar_eficacia(self.dir, VERIF, i, date.today(), "EFICAZ", "ok")
        nc.aceptar_descargo(self.dir, AUDITOR, i)
        mismo = nc.Actor("auditor", "x", frozenset({"nc_verificar"}))
        with self.assertRaises(nc.NCError):
            nc.verificar_eficacia(self.dir, mismo, i, date.today(), "EFICAZ", "ok")
        with self.assertRaises(nc.NCError):
            nc.verificar_eficacia(self.dir, VERIF, i, date.today(), "EFICAZ", "")
        nc.verificar_eficacia(self.dir, VERIF, i, date.today(), "EFICAZ", "Recuento ok")
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "CERRADA")

    def test_regla_verificador_apagada(self):
        cfg = nc.cargar_config(self.dir)
        cfg["verificador_distinto"] = False
        nc.guardar_config(self.dir, cfg)
        i = self.respondida()
        nc.aceptar_descargo(self.dir, AUDITOR, i)
        nc.verificar_eficacia(self.dir, ADMIN.__class__("auditor", "x", frozenset(nc.ROLES_NC)),
                              i, date.today(), "EFICAZ", "ok")
        ev = nc.eventos(self.dir, "nc", i)[-1]
        self.assertTrue(ev["datos"]["mismo_usuario_que_respondio"])

    def test_no_eficaz_crea_nc_vinculada(self):
        i = self.respondida()
        nc.aceptar_descargo(self.dir, AUDITOR, i)
        nueva = nc.verificar_eficacia(self.dir, VERIF, i, date.today(), "NO_EFICAZ",
                                      "Se repite la diferencia")
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "CERRADA_NO_EFICAZ")
        n = nc.obtener(self.dir, nueva)
        self.assertEqual((n["estado"], n["origen_id"], n["req_codigo"]), ("BORRADOR", i, "9.2.12"))

    def test_anular_despues_de_enviada(self):
        i = self.emitida()
        nc.registrar_envio(self.dir, AUDITOR, i, "mail", "x", datetime(2026, 9, 25, 9))
        with self.assertRaises(nc.NCError):
            nc.anular(self.dir, AUDITOR, i, "motivo", "avisado por mail")   # sin rol
        with self.assertRaises(nc.NCError):
            nc.anular(self.dir, ADMIN, i, "", "avisado")                    # sin motivo
        with self.assertRaises(nc.NCError):
            nc.anular(self.dir, ADMIN, i, "motivo", "")                     # sin aviso
        nc.anular(self.dir, ADMIN, i, "motivo", "avisado por mail")
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "ANULADA")


class TestAdjuntosBackupObs(Base):
    def test_adjunto_hash_y_backup(self):
        i = self.emitida()
        nc.agregar_adjunto(self.dir, AUDITOR, i, "hallazgo", "foto.jpg", b"123")
        self.assertEqual(nc.verificar_adjuntos(self.dir), [])
        db, z = nc.backup(self.dir)
        c = sqlite3.connect(db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM nc").fetchone()[0], 1)
        c.close()
        self.assertTrue(z.exists())
        adj = nc.adjuntos(self.dir, i)[0]
        (self.dir / adj["ruta"]).write_bytes(b"cambiado")
        self.assertEqual(len(nc.verificar_adjuntos(self.dir)), 1)

    def test_observaciones(self):
        n1 = nc.registrar_observacion(self.dir, AUDITOR, "34", "SDE", date(2026, 9, 1), "Orden")
        n2 = nc.registrar_observacion(self.dir, AUDITOR, "34", "SDE", date(2026, 9, 2), "Otra")
        self.assertEqual((n1, n2), ("OBS-01034092026", "OBS-02034092026"))


class TestSOP(Base):
    @unittest.skipUnless(SOP_REAL.exists(), "SOP real no disponible")
    def test_sop_real_y_versionado(self):
        contenido = SOP_REAL.read_bytes()
        arts, metodo = sop.parsear_docx(contenido)
        a = {x["codigo"]: x for x in arts}
        self.assertEqual(a["9.2.12"]["titulo"], "Cierre de inventario y archivo")
        self.assertEqual(a["9.2.12"]["padre_codigo"], "9.2")

        vid, _, _ = sop.cargar_version(self.dir, AUDITOR, "sop.docx", contenido, "2", None)
        sop.activar_version(self.dir, AUDITOR, vid)
        art = sop.buscar(self.dir, "9.2.12")[0]
        i = self.emitida(req_texto=art["texto"], sop_version_id=vid)
        h = nc.obtener(self.dir, i)["sha256_html"]
        conn = nc.conectar(self.dir)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE sop_articulos SET titulo = 'x' WHERE version_id = ?", (vid,))
        conn.close()
        # nueva versión con otro texto: la NC emitida conserva el suyo
        vid2, _, _ = sop.cargar_version(self.dir, AUDITOR, "sop3.docx", contenido + b" ", "3", None)
        arts2 = sop.articulos(self.dir, vid2)
        for x in arts2:
            if x["codigo"] == "9.2.12":
                x["texto"] = "Texto nuevo"
        sop.reemplazar_articulos(self.dir, AUDITOR, vid2, arts2)
        sop.activar_version(self.dir, AUDITOR, vid2)
        self.assertEqual(sop.buscar(self.dir, "9.2.12")[0]["texto"], "Texto nuevo")
        n = nc.obtener(self.dir, i)
        self.assertEqual(n["req_texto"], art["texto"])
        self.assertEqual(nc.html_para_reimprimir(self.dir, AUDITOR, i).encode(),
                         n["html_emitido"].encode())
        self.assertEqual(n["sha256_html"], h)


def _anexo(titulo="Merma 09/2026", marca="FRAGMENTO-IMPRESO"):
    return {"tipo": "merma", "titulo": titulo, "html": f"<p>{marca}</p>",
            "params": {"codigodepo": "034", "desde": "2026-09-01", "hasta": "2026-09-23"},
            "resumen": {"merma_valorizada": 1000.0, "fuente": "x"}, "corte_datos": "2026-09-23"}


class TestAnexos(Base):
    def test_impreso_e_interno_y_congelado(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos())
        a1 = nc.agregar_anexo(self.dir, AUDITOR, i, _anexo())
        a2 = nc.agregar_anexo(self.dir, AUDITOR, i, _anexo("Serie interna", "FRAGMENTO-INTERNO"),
                              imprimir=False)
        nc.emitir(self.dir, AUDITOR, i)
        html = nc.obtener(self.dir, i)["html_emitido"]
        self.assertIn("Anexo 1 — Merma 09/2026", html)
        self.assertIn("FRAGMENTO-IMPRESO", html)
        self.assertNotIn("FRAGMENTO-INTERNO", html)
        ev = [e for e in nc.eventos(self.dir, "nc", i) if e["tipo"] == "EMITIDA"][0]
        self.assertEqual({a["id"] for a in ev["datos"]["anexos"]}, {a1, a2})
        conn = nc.conectar(self.dir)
        for sql, args in (("UPDATE nc_anexos SET html = 'x' WHERE id = ?", (a1,)),
                          ("DELETE FROM nc_anexos WHERE id = ?", (a2,)),
                          ("INSERT INTO nc_anexos (nc_id, orden, tipo, titulo, params, html,"
                           " sha256, generado_por, generado_en) VALUES (?,9,'m','t','{}','h','s',"
                           "'u','t')", (i,))):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, args)
        conn.close()
        with self.assertRaises(nc.NCError):
            nc.marcar_anexo(self.dir, AUDITOR, a1, False)
        self.assertEqual(nc.verificar_anexos(self.dir), [])

    def test_alteracion_detectada(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos())
        a = nc.agregar_anexo(self.dir, AUDITOR, i, _anexo())
        nc.emitir(self.dir, AUDITOR, i)
        conn = nc.conectar(self.dir)
        conn.execute("DROP TRIGGER nc_anexos_congelados_upd")
        conn.execute("UPDATE nc_anexos SET html = '<p>otro</p>' WHERE id = ?", (a,))
        conn.close()
        self.assertEqual(len(nc.verificar_anexos(self.dir)), 1)

    def test_borrador_editable_y_reemision_copia(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos(fecha=date.today()))
        a = nc.agregar_anexo(self.dir, AUDITOR, i, _anexo())
        nc.marcar_anexo(self.dir, AUDITOR, a, False)
        self.assertEqual(nc.anexos(self.dir, i)[0]["imprimir"], 0)
        nc.marcar_anexo(self.dir, AUDITOR, a, True)
        nc.emitir(self.dir, AUDITOR, i)
        nueva = nc.anular(self.dir, AUDITOR, i, "error", reemitir=True)
        copia = nc.anexos(self.dir, nueva)
        self.assertEqual([x["sha256"] for x in copia], [x["sha256"] for x in nc.anexos(self.dir, i)])
        nc.quitar_anexo(self.dir, AUDITOR, copia[0]["id"])
        self.assertEqual(nc.anexos(self.dir, nueva), [])

    def test_eliminar_borrador_con_anexos(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos())
        nc.agregar_anexo(self.dir, AUDITOR, i, _anexo())
        nc.eliminar_borrador(self.dir, AUDITOR, i)
        conn = nc.conectar(self.dir)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM nc_anexos").fetchone()[0], 0)
        conn.close()

    @unittest.skipUnless(pdf.disponible(), "sin Edge/Chrome")
    def test_pdf(self):
        i = nc.crear_borrador(self.dir, AUDITOR, _datos())
        nc.agregar_anexo(self.dir, AUDITOR, i, _anexo())
        nc.emitir(self.dir, AUDITOR, i)
        b = pdf.html_a_pdf(nc.html_para_reimprimir(self.dir, AUDITOR, i))
        self.assertTrue(b.startswith(b"%PDF"))


class TestMail(Base):
    DEST = [{"nombre": "Encargado", "mail": "encargado@sucursal.com", "para": True,
             "cc": False, "cco": False},
            {"nombre": "Coordinación", "mail": "coord@rioshop.com", "para": False,
             "cc": True, "cco": False},
            {"nombre": "Dirección", "mail": "direccion@rioshop.com", "para": False,
             "cc": False, "cco": True}]

    def setUp(self):
        super().setUp()
        self.enviados = []

    def _enviar(self, msg, todos):
        self.enviados.append((msg, todos))

    def _mandar(self, i, **kw):
        borrador = nc_mail.componer(self.dir, i)
        return nc_mail.enviar_nc(self.dir, AUDITOR, i, kw.get("dest", self.DEST),
                                 borrador["asunto"], borrador["cuerpo"], borrador["firma"],
                                 enviar=kw.get("enviar", self._enviar),
                                 html_a_pdf=lambda h: b"%PDF-falso")

    def test_envio_fija_plazos_y_registra(self):
        i = self.emitida(fecha=date.today())
        r = self._mandar(i)
        msg, todos = self.enviados[0]
        n = nc.obtener(self.dir, i)
        self.assertEqual(n["estado"], "ENVIADA")
        self.assertEqual(n["recepcion_ts"][:10], date.today().isoformat())
        self.assertEqual(r["plazos"]["fecha_limite_descargo"], n["fecha_limite_descargo"])
        self.assertEqual(msg["Subject"], f"No conformidad N° 01 034 {date.today():%m%Y}")
        self.assertEqual(msg["To"], "encargado@sucursal.com")
        self.assertEqual(msg["Cc"], "coord@rioshop.com")
        self.assertIsNone(msg["Bcc"])                          # CCO solo en el sobre
        self.assertIn("direccion@rioshop.com", todos)
        texto = msg.get_body(("plain",)).get_content()
        self.assertIn("no reviste carácter sancionatorio", texto)
        self.assertIn("Saludos.\nMathias Villanueva", texto)        # firmante = solicitante
        self.assertIn("nombre y apellido", texto)
        self.assertIn("devolver la no conformidad firmada dentro", texto.replace(
            " (firma, aclaración y fecha y hora de recepción)", ""))
        self.assertTrue(texto.rstrip().endswith(nc_mail.PIE))
        html_mail = msg.get_body(("html",)).get_content()
        self.assertIn("font-size:10px\">" + nc_mail.PIE, html_mail)
        self.assertIn(nc_mail.saludo(datetime.now()), texto)
        self.assertNotIn("{", texto)
        fecha_desc = date.fromisoformat(n["fecha_limite_descargo"]).strftime("%d/%m/%Y")
        self.assertIn(fecha_desc, texto)                       # el mail dice la fecha registrada
        adj = [a for a in msg.iter_attachments()][0]
        self.assertEqual(adj.get_content_type(), "application/pdf")
        env = nc.envios(self.dir, i)[0]
        self.assertEqual((env["resultado"], env["cco"]), ("OK", "direccion@rioshop.com"))
        pdf_guardado = [a for a in nc.adjuntos(self.dir, i) if a["etapa"] == "envio"][0]
        self.assertEqual(pdf_guardado["sha256"], nc.sha256_bytes(b"%PDF-falso"))
        self.assertEqual(nc.eventos(self.dir, "nc", i)[-1]["tipo"], "ENVIADA")

    def test_fallo_no_cambia_estado(self):
        i = self.emitida()

        def falla(msg, todos):
            raise ConnectionError("sin red")

        with self.assertRaises(nc.NCError):
            self._mandar(i, enviar=falla)
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "EMITIDA")
        self.assertEqual(nc.envios(self.dir, i)[0]["resultado"], "ERROR")
        self.assertEqual(nc.eventos(self.dir, "nc", i)[-1]["tipo"], "ENVIO_FALLIDO")

    def test_reenvio_no_toca_plazos(self):
        i = self.emitida()
        self._mandar(i)
        antes = nc.obtener(self.dir, i)["fecha_limite_descargo"]
        r = self._mandar(i)
        self.assertTrue(r["reenvio"])
        self.assertEqual(nc.obtener(self.dir, i)["fecha_limite_descargo"], antes)
        self.assertEqual(nc.eventos(self.dir, "nc", i)[-1]["tipo"], "MAIL_REENVIADO")
        self.assertEqual(len(nc.envios(self.dir, i)), 2)

    def test_validaciones(self):
        i = self.emitida()
        sin_para = [dict(d, para=False) for d in self.DEST]
        with self.assertRaises(nc.NCError):
            self._mandar(i, dest=sin_para)
        malo = [dict(self.DEST[0], mail="no-es-un-mail")]
        with self.assertRaises(nc.NCError):
            self._mandar(i, dest=malo)
        self.assertEqual(self.enviados, [])
        with self.assertRaises(nc.NCError):   # borrador: todavía no se puede enviar
            b = nc.crear_borrador(self.dir, AUDITOR, _datos())
            self._mandar(b)

    def test_destinatarios_y_marcadores(self):
        cfg = nc.cargar_config(self.dir)
        cfg["mail"] = {"contactos_deposito": {"034": [{"nombre": "Enc", "mail": "e@x.com"}]},
                       "libreta": [{"nombre": "Coord", "mail": "c@x.com", "cc": True},
                                   {"nombre": "Enc dup", "mail": "e@x.com", "cco": True}]}
        nc.guardar_config(self.dir, cfg)
        i = self.emitida()
        d = nc_mail.componer(self.dir, i)["destinatarios"]
        self.assertEqual([(x["mail"], x["para"], x["cc"]) for x in d],
                         [("e@x.com", True, False), ("c@x.com", False, True)])
        self.assertEqual(nc_mail.separar([{"mail": "a@x.com", "para": True, "cc": True}]),
                         (["a@x.com"], [], []))
        self.assertEqual(nc_mail.rellenar("{saludo} {desconocido}", {"saludo": "Hola"}),
                         "Hola {desconocido}")
        self.assertEqual(nc_mail.saludo(datetime(2026, 1, 1, 9)), "Buenos días")
        self.assertEqual(nc_mail.saludo(datetime(2026, 1, 1, 15)), "Buenas tardes")
        self.assertEqual(nc_mail.saludo(datetime(2026, 1, 1, 22)), "Buenas noches")

    def test_password_fuera_del_config(self):
        archivo = self.dir / "smtp.json"
        nc_mail.guardar_password("x@gmail.com", "abcd efgh ijkl mnop", archivo)
        self.assertEqual(nc_mail.password("x@gmail.com", archivo), "abcdefghijklmnop")
        self.assertNotIn("abcd", (self.dir / "nc_config.json").read_text(encoding="utf-8")
                         if (self.dir / "nc_config.json").exists() else "")


class TestContactos(Base):
    AGENDA = [
        {"nombre": "Encargado SDE", "mail": "Enc.SDE@x.com", "codigodepo": "34",
         "por_defecto": "para"},
        {"nombre": "Encargado SJ", "mail": "enc.sj@x.com", "codigodepo": "007",
         "por_defecto": "para"},
        {"nombre": "Coordinación", "mail": "coord@x.com", "grupo": "Calidad",
         "por_defecto": "cc"},
        {"nombre": "Dirección", "mail": "dir@x.com", "grupo": "Calidad", "por_defecto": "cco"},
        {"nombre": "Inactivo", "mail": "viejo@x.com", "por_defecto": "cc", "activo": False},
    ]

    def test_sugeridos_por_sucursal(self):
        nc_contactos.guardar_todos(self.dir, AUDITOR, self.AGENDA)
        sug = nc_contactos.sugeridos(self.dir, "034")
        self.assertEqual([(x["mail"], x["para"], x["cc"], x["cco"]) for x in sug],
                         [("enc.sde@x.com", True, False, False),
                          ("coord@x.com", False, True, False),
                          ("dir@x.com", False, False, True)])
        g = nc_contactos.grupos(nc_contactos.listar(self.dir))
        self.assertEqual({k: [c["mail"] for c in v] for k, v in g.items()},
                         {"Calidad": ["coord@x.com", "dir@x.com"]})

    def test_validaciones_y_borrado(self):
        with self.assertRaises(nc.NCError):
            nc_contactos.guardar_todos(self.dir, AUDITOR, [{"mail": "no-mail"}])
        with self.assertRaises(nc.NCError):
            nc_contactos.guardar_todos(self.dir, AUDITOR, [{"mail": "a@x.com"},
                                                           {"mail": "A@x.com"}])
        with self.assertRaises(nc.NCError):
            nc_contactos.guardar_todos(self.dir, LECTOR, self.AGENDA)
        nc_contactos.guardar_todos(self.dir, AUDITOR, self.AGENDA)
        nc_contactos.registrar_uso(self.dir, ["dir@x.com"])
        nc_contactos.guardar_todos(self.dir, AUDITOR, self.AGENDA[2:4])   # borra el resto
        agenda = nc_contactos.listar(self.dir)
        self.assertEqual([c["mail"] for c in agenda], ["dir@x.com", "coord@x.com"])
        self.assertEqual(agenda[0]["usos"], 1)                            # conserva usos

    def test_migra_config_anterior(self):
        cfg = nc.cargar_config(self.dir)
        cfg["mail"] = {"contactos_deposito": {"034": [{"nombre": "Enc", "mail": "e@x.com"}]},
                       "libreta": [{"nombre": "Coord", "mail": "c@x.com", "cc": True}]}
        nc.guardar_config(self.dir, cfg)
        self.assertEqual({c["mail"]: (c["codigodepo"], c["por_defecto"])
                          for c in nc_contactos.listar(self.dir)},
                         {"e@x.com": ("034", "para"), "c@x.com": (None, "cc")})

    def test_envio_guarda_nuevos_y_frecuentes(self):
        nc_contactos.guardar_todos(self.dir, AUDITOR, self.AGENDA)
        i = self.emitida()
        dest = nc_mail.componer(self.dir, i)["destinatarios"] + [
            {"nombre": "Nuevo", "mail": "nuevo@x.com", "para": False, "cc": True, "cco": False}]
        b = nc_mail.componer(self.dir, i)
        r = nc_mail.enviar_nc(self.dir, AUDITOR, i, dest, b["asunto"], b["cuerpo"], b["firma"],
                              enviar=lambda m, t: None, html_a_pdf=lambda h: b"%PDF",
                              guardar_nuevos=True)
        self.assertEqual(r["contactos_nuevos"], ["nuevo@x.com"])
        agenda = nc_contactos.listar(self.dir)
        usados = {c["mail"]: c["usos"] for c in agenda}
        self.assertEqual(usados["nuevo@x.com"], 1)
        self.assertEqual(usados["enc.sj@x.com"], 0)
        self.assertEqual(agenda[-1]["usos"], 0)                           # sin uso, al final


class TestMailAutocompletado(Base):
    def test_texto_completo_sin_marcadores(self):
        i = self.emitida(fecha=date.today())
        b = nc_mail.componer(self.dir, i)
        self.assertEqual(b["asunto"], f"No conformidad N° 01 034 {date.today():%m%Y}")
        todo = b["cuerpo"] + b["firma"]
        self.assertNotIn("{", todo)
        self.assertIn("- Punto del POE: 9.2.12 — Cierre de inventario y archivo (versión 2)", todo)
        self.assertIn("- Tipo de no conformidad: ROJO", todo)
        self.assertIn("- Desvío detectado: El conteo no es verídico.", todo)
        self.assertIn("dentro de las 48 horas de recibido", todo)
        self.assertIn("Saludos.\nMathias Villanueva", b["firma"])

    def test_texto_desfasado_se_rechaza(self):
        from datetime import timedelta
        i = self.emitida(fecha=date.today() - timedelta(days=3))
        ayer = datetime.now() - timedelta(days=1)
        b = nc_mail.componer(self.dir, i, ahora=ayer)
        dest = [{"mail": "e@x.com", "para": True}]
        enviar = dict(enviar=lambda m, t: None, html_a_pdf=lambda h: b"%PDF")
        if nc_mail.fechas_clave(nc.obtener(self.dir, i), nc.cargar_config(self.dir),
                                datetime.now()) != b["referencia"]:
            with self.assertRaises(nc.NCError):
                nc_mail.enviar_nc(self.dir, AUDITOR, i, dest, b["asunto"], b["cuerpo"],
                                  b["firma"], referencia=b["referencia"], **enviar)
            self.assertEqual(nc.obtener(self.dir, i)["estado"], "EMITIDA")
        b = nc_mail.componer(self.dir, i)            # regenerado: sale
        nc_mail.enviar_nc(self.dir, AUDITOR, i, dest, b["asunto"], b["cuerpo"], b["firma"],
                          referencia=b["referencia"], **enviar)
        self.assertEqual(nc.obtener(self.dir, i)["estado"], "ENVIADA")


class TestMailErrores(unittest.TestCase):
    def test_login_solo_plain_y_mensajes(self):
        import smtplib

        class Falso:
            def __init__(self):
                self.mecanismos = []

            def ehlo(self):
                pass

            def auth_plain(self, challenge=None):
                return "x"

            def auth(self, mecanismo, objeto):
                self.mecanismos.append(mecanismo)
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password "
                                                           b"not accepted")

        f = Falso()
        with self.assertRaises(smtplib.SMTPAuthenticationError) as cm:
            nc_mail._login(f, "a@gmail.com", "abcdefghijklmnop")
        self.assertEqual(f.mecanismos, ["PLAIN"])          # no reintenta con LOGIN
        self.assertIn("contraseña de aplicación", nc_mail.explicar_error(cm.exception))
        e534 = smtplib.SMTPAuthenticationError(534, b"5.7.9 Application-specific password required")
        self.assertIn("exige una contraseña de aplicación", nc_mail.explicar_error(e534))
        self.assertIn("firewall", nc_mail.explicar_error(ConnectionRefusedError("rechazada")))

    def test_formato_password_de_aplicacion(self):
        self.assertTrue(nc_mail.parece_password_de_aplicacion("abcd efgh ijkl mnop"))
        self.assertFalse(nc_mail.parece_password_de_aplicacion("MiClave2026!xyzabc"))
        self.assertFalse(nc_mail.parece_password_de_aplicacion("abcdefgh"))


if __name__ == "__main__":
    unittest.main()
