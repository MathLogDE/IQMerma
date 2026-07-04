# MermaIQ

Analizador de merma de inventario para retail. Reemplaza el flujo Excel/Power Pivot con un sistema local basado en DuckDB + Streamlit.

## Stack

| Capa | Tecnología |
|---|---|
| Base de datos | DuckDB 1.5.3 (un archivo `.duckdb` por proyecto) |
| Procesamiento | Python 3.12 + Pandas 3.x + NumPy |
| Interfaz | Streamlit 1.58 |
| Gráficos | Plotly 6.x |
| Reportes imprimibles | Jinja2 + HTML/CSS |
| Export Excel | openpyxl |

## Estructura

```
mermaiq/
├── core/
│   └── merma.py        # Motor de cálculo de merma (sin dependencias de UI)
├── ui/
│   └── app.py          # Interfaz Streamlit
├── ingesta/            # Carga de archivos del ERP
│   ├── movimientos.py  #   movimientos + inventario físico (TIPOMOV='INV')
│   ├── ventas.py       #   reporte de ventas
│   ├── stock.py        #   snapshots de stock (valorización)
│   └── referencias.py  #   depósitos, estructura, artículos
├── projects/           # Un .duckdb por cliente (excluido de git)
├── exports/            # Temporales de ingesta en runtime (excluido de git)
├── setup_db.py         # Schema + catálogo tipos_categoria
├── requirements.txt
├── README.md
├── GUIA_USO.md         # Guía de uso completa
└── MIGRACION_V2.md     # Notas del refactor v2
```

> Directorios reservados sin uso actual: `config/`, `templates/`, `tests/`.

## Setup inicial

```bash
# Instalar dependencias
pip install -r requirements.txt

# Crear schema para un proyecto nuevo
python setup_db.py --project nombre_cliente

# Correr la app
streamlit run ui/app.py
```

## Guía de uso

Instalación, carga de datos y lectura de los análisis, paso a paso, en
**[GUIA_USO.md](GUIA_USO.md)**. Detalle del refactor v2 en
[MIGRACION_V2.md](MIGRACION_V2.md).

## Fórmula central

```
merma real (%) = merma valorizada / ventas del período
```

Donde:
- **merma valorizada** = suma de faltantes de las categorías de merma
  (Inventario, Dif. de camión, Ajustes), valorizados contra el stock (costo o
  precio de lista).
- **ventas del período** = venta del archivo de ventas (a costo o a precio de
  lista según el modo elegido).
