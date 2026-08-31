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
├── core/                       # Motores de análisis (sin dependencias de UI)
│   ├── comun.py                #   conexión, valorización, jerarquía, catálogos
│   ├── merma/                  #   analisis.py · control.py
│   ├── inventario/             #   salud.py · politicas.py
│   ├── distribucion/           #   transferencias.py
│   ├── comercial/              #   margenes.py
│   ├── forecasting/            #   demanda.py
│   └── reporte.py              #   reportes imprimibles con marca (transversal)
├── ui/                         # Interfaz Streamlit
│   ├── app.py                  #   router: config, CSS, navegación por secciones
│   ├── comun.py                #   helpers de UI + fachada de core
│   └── paginas/                #   una página por archivo (función render)
├── ingesta/                    # Carga de archivos del ERP
│   ├── movimientos.py          #   ventas (VTA), inventario (INV), remitos, ajustes
│   ├── stock.py                #   snapshots de stock (valorización)
│   └── referencias.py          #   depósitos, estructura, artículos
├── templates/                  # HTML (Jinja2) de los reportes imprimibles
├── projects/                   # Un .duckdb por cliente (excluido de git)
├── setup_db.py                 # Schema + catálogo tipos_categoria
├── README.md · GUIA_USO.md · MIGRACION_V2.md
└── requirements.txt · iniciar.bat
```

Dominios: **merma · inventario · distribución · comercial · forecasting**.
Cada uno tiene su motor en `core/<dominio>/` y su(s) página(s) en `ui/paginas/`.

## Setup inicial

```bash
# Instalar dependencias
pip install -r requirements.txt

# Crear schema para un proyecto nuevo
python setup_db.py --project nombre_cliente

# Correr la app
python -m streamlit run ui/app.py
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
- **ventas del período** = unidades vendidas (movimientos VTA) valorizadas
  desde el stock (a costo o a precio de lista según el modo).
