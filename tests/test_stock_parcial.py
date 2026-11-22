"""
Snapshot de stock parcial: la marca se guarda y la consulta tolera DBs viejas.

Correr con:
    python -m unittest tests.test_stock_parcial -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import setup_db  # noqa: E402
from core.comun import snapshots_parciales  # noqa: E402
from ingesta.stock import ingestar_stock  # noqa: E402


class TestStockParcial(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(setup_db, "PROJECTS_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proyecto = "t"
        conn = duckdb.connect(str(setup_db.get_db_path(self.proyecto)))
        # schema viejo: periodos_ingesta sin `parcial`, como una DB previa al cambio
        viejo = setup_db.SCHEMA_SQL.replace(
            ",\n    parcial            BOOLEAN     DEFAULT FALSE", "")
        assert viejo != setup_db.SCHEMA_SQL
        conn.execute(viejo)
        conn.execute("INSERT INTO depositos (codigodepo, nombre, abreviacion) VALUES ('1', 'Suc', 'SUC')")
        conn.close()
        self.xlsx = self.tmp / "stock.xlsx"
        pd.DataFrame({"CODIGO": ["A"], "SUC": ["5"]}).to_excel(self.xlsx, index=False)

    def test_marca_y_reemplazo(self):
        self.assertEqual(snapshots_parciales(self.proyecto), set())  # DB sin columna
        ingestar_stock(self.xlsx, self.proyecto, "2026-11-01")
        ingestar_stock(self.xlsx, self.proyecto, "2026-11-02", parcial=True)
        self.assertEqual(snapshots_parciales(self.proyecto), {"2026-11-02"})
        # reemplazarlo sin marcar quita la advertencia
        ingestar_stock(self.xlsx, self.proyecto, "2026-11-02", forzar=True)
        self.assertEqual(snapshots_parciales(self.proyecto), set())


if __name__ == "__main__":
    unittest.main()
