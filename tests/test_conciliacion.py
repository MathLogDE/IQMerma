"""
Tests de core/merma/conciliacion.py (lógica pura, sin base).

Correr con:
    python -m unittest tests.test_conciliacion -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.merma.conciliacion import combinaciones, eventos  # noqa: E402

GRIS, BLANCO = "1006053", "1006054"


def _m(fecha, depo, tipomov, tipo, numero, codigo, dif, tipo_aj=None, usuario="U"):
    return {"fecha": pd.Timestamp(fecha), "codigodepo": depo, "abreviacion": None,
            "tipomov": tipomov, "tipo": tipo, "tipo_aj": tipo_aj, "usuario": usuario,
            "numero": numero, "codigo": codigo, "diferencia": float(dif)}


MOV = pd.DataFrame([
    # remito balanceado 001 -> 016: no es evento
    _m("2026-07-01", "001", "REM", "RI", "R0", BLANCO, -2),
    _m("2026-07-02", "016", "REM", "RI", "R0", BLANCO, 2),
    # ajustes sueltos en 016 (dos comprobantes el mismo día se agrupan)
    _m("2026-07-06", "016", "AJU", "CS", "A1", BLANCO, 2, "STOCK ACTUAL", "EFLORES"),
    _m("2026-07-06", "016", "AJU", "CS", "A2", BLANCO, 1, "STOCK ACTUAL", "EFLORES"),
    _m("2026-07-13", "016", "AJU", "CS", "A3", BLANCO, 3, "STOCK ACTUAL", "EFLORES"),
    # ajuste cruzado en 039
    _m("2026-04-19", "039", "AJU", "CS", "A4", GRIS, -1, "STOCK ACTUAL"),
    _m("2026-04-19", "039", "AJU", "CS", "A5", BLANCO, 1, "STOCK ACTUAL"),
    # tres remitos sin destino desde 001 con los dos códigos; dos se anulan
    *[_m(f, "001", "REM", "RI", n, c, q)
      for f, n in (("2026-08-13", "R1"), ("2026-08-15", "R2"), ("2026-08-15", "R3"))
      for c, q in ((GRIS, -3), (BLANCO, -5))],
    _m("2026-08-13", "001", "AJU", "CS", "A6", GRIS, 3, None, "M"),
    _m("2026-08-13", "001", "AJU", "CS", "A6", BLANCO, 5, None, "M"),
    _m("2026-08-15", "001", "AJU", "CS", "A7", GRIS, 3, "ANULACION", "M"),
    _m("2026-08-15", "001", "AJU", "CS", "A7", BLANCO, 5, "ANULACION", "M"),
    # remito reciente sin destino -> en tránsito
    _m("2026-10-30", "001", "REM", "RI", "R4", GRIS, -3),
    # ventas e ingresos de proveedor no son eventos
    _m("2026-07-20", "016", "VTA", "FB", "V1", BLANCO, -1),
    _m("2026-07-20", "001", "REM", "RE", "P1", BLANCO, 30),
])


class TestEventos(unittest.TestCase):
    def setUp(self):
        self.ev = eventos(MOV, "2026-11-02")

    def patron(self, comprobante):
        return self.ev.loc[self.ev["comprobantes"] == comprobante, "patron"].item()

    def test_columnas_por_codigo(self):
        self.assertEqual(self.ev.attrs["codigos"], [GRIS, BLANCO])
        self.assertNotIn("R0", set(self.ev["comprobantes"]))
        self.assertNotIn("V1", set(self.ev["comprobantes"]))
        self.assertNotIn("P1", set(self.ev["comprobantes"]))

    def test_ajustes(self):
        self.assertEqual(self.patron("A1, A2"), "ajuste suelto")
        self.assertEqual(self.ev.loc[self.ev["comprobantes"] == "A1, A2", BLANCO].item(), 3)
        self.assertEqual(self.patron("A4, A5"), "ajuste cruzado (+/−)")

    def test_remitos_y_anulaciones(self):
        self.assertEqual(self.patron("R1"), "remito anulado")
        self.assertEqual(self.patron("A6"), "anulación de remito R1")
        self.assertEqual(self.patron("R2"), "remito anulado")
        # R2 y R3 son idénticos y hay una sola anulación: no se sabe cuál quedó colgado
        self.assertEqual(self.patron("R3"), "remito sin destino (o R2: anulación ambigua)")
        self.assertEqual(self.patron("R4"), "remito en tránsito")
        r3 = self.ev[self.ev["comprobantes"] == "R3"].iloc[0]
        self.assertEqual((r3[GRIS], r3[BLANCO]), (-3, -5))

    def test_combinaciones(self):
        c = combinaciones(self.ev, BLANCO, 6)
        self.assertEqual(len(c), 1)  # +3 (06/07) + +3 (13/07); el +1 cruzado no completa
        self.assertEqual(c.loc[0, "n_eventos"], 2)
        self.assertIn("EFLORES", c.loc[0, "detalle"])
        # los anulados no cuentan: -5 solo lo explica el remito colgado
        c = combinaciones(self.ev, BLANCO, -5)
        self.assertEqual(c["detalle"].str.contains("RI").sum(), 1)

    def test_vacio(self):
        ev = eventos(MOV[MOV["tipomov"] == "VTA"], "2026-11-02")
        self.assertTrue(ev.empty)
        self.assertTrue(combinaciones(ev, BLANCO, 6).empty)


if __name__ == "__main__":
    unittest.main()
