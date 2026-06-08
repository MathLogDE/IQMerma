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
├── core/          # Lógica de negocio (sin dependencias de UI)
├── ui/            # Streamlit
├── ingesta/       # Carga de archivos del ERP
├── projects/      # Un subdirectorio por cliente (excluido de git)
├── templates/     # HTML/CSS para reportes imprimibles
├── exports/       # Archivos generados (excluido de git)
├── tests/
├── config/
├── setup_db.py    # Inicialización del schema
└── requirements.txt
```

## Setup inicial

```bash
# Instalar dependencias
pip install -r requirements.txt

# Crear schema para un proyecto nuevo
python setup_db.py --project nombre_cliente
```

## Fórmula central

```
merma real (%) = merma valorizada / ventas del período
```

Donde:
- **merma valorizada** = unidades faltantes × costo unitario (del conteo o del historial de remitos)
- **ventas del período** = venta neta con descuentos, del reporte de cierre
