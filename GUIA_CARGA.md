# Guía: borrar datos y cargar desde cero

Cada proyecto/cliente es un archivo independiente en
`projects/<proyecto>/data.duckdb`. Borrar o recargar un proyecto **no afecta**
a los demás.

## 1. Borrar los datos actuales

**Opción A — reset (recomendada):** borra y recrea todas las tablas, y vuelve a
sembrar el catálogo `tipos_categoria` por defecto.

```bash
python setup_db.py --project <proyecto> --reset
# pide confirmación: escribí  s  y Enter
```

**Opción B — borrar el archivo a mano** y recrear el schema vacío:

```bash
rm projects/<proyecto>/data.duckdb
python setup_db.py --project <proyecto>
```

> Ambas dejan el schema v2 limpio. El reset **también resetea** el catálogo
> `tipos_categoria` a los valores por defecto: si lo habías editado, se pierde.

## 2. Orden de carga (importante)

El orden importa por dependencias:

1. **depositos** — primero de todo. `stock` lo necesita para mapear las columnas
   de sucursal por su *Abreviación*.
2. **estructura** — rubros (para el análisis por Rubros).
3. **articulos** — SKUs (descripción, rubro, activo).
4. **stock** — snapshots (fuente de valorización: costo / lista_1).
5. **movimientos** — incluye el inventario físico (TIPOMOV='INV').
6. **ventas** — denominador del % de merma.
7. **marcar logística** — marcar los centros de distribución (una vez).

## 3. Formato de cada archivo (columnas esperadas)

Los nombres de columna se normalizan (mayúsculas/espacios), pero deben estar:

| Archivo | Columnas |
|---|---|
| depositos | `Cod. Dep.` · `Nombre` · `Dirección` · `Abreviación` |
| estructura | `Rubro` · `Super Rubro` · `Gran Super Rubro` |
| articulos | `Código` · `Descripción` · `Rubro` · `Marca` · `EAN` · `Clase` · `Activo` (mínimo: Código, Descripción, Activo) |
| stock | `CODIGO` · `Lista 1` · `Costo` · `UxB` · + una columna por sucursal (encabezado = *Abreviación* del depósito) |
| movimientos | `FECHA` · `TIPOMOV` · `TIPO` · `NUMERO` · `CODIGODEPO` · `NOMBRE` · `CODIGO` · `COSTO` · `INGRESO` · `EGRESO` · `DIFERENCIA` · `USER` · `TIPO AJ` (obligatorias: FECHA, TIPOMOV, TIPO, NUMERO, CODIGODEPO, CODIGO) |
| ventas | `FECHA DESDE` · `FECHA HASTA` · `CÓDIGO` · `COD. DEP.` · `TOTAL VTA.` · `TOTAL UNID.` |

Notas:
- Números en formato argentino (coma decimal) se parsean solos.
- `movimientos`: la `DIFERENCIA` se **recalcula** como `INGRESO - EGRESO`.
- `stock`: las columnas que no matcheen una abreviación de depósito se ignoran
  (se reportan como advertencia).

## 4. Cargar los archivos

### Opción A — Interfaz (Streamlit)

```bash
streamlit run ui/app.py
```

En la página **Ingesta → Cargar archivo**, subí cada Excel eligiendo su tipo,
respetando el orden de arriba. Para `stock` te pide la *fecha del snapshot*.

### Opción B — Script Python (más rápido para carga masiva)

Adaptá las rutas y corré `python cargar_todo.py`:

```python
# cargar_todo.py
from ingesta.referencias import cargar_depositos, cargar_estructura, cargar_articulos, marcar_logistica
from ingesta.stock import ingestar_stock
from ingesta.movimientos import ingestar_movimientos, ingestar_libro_movimientos
from ingesta.ventas import ingestar_ventas

PROY = "cliente_x"

cargar_depositos("data/depositos.xlsx", PROY)
cargar_estructura("data/estructura.xlsx", PROY)
cargar_articulos("data/articulos.xlsx", PROY)

ingestar_stock("data/stock_2026-03-31.xlsx", PROY, fecha_snapshot="2026-03-31")

# Movimientos: un archivo con varias pestañas (una por mes) → una sola llamada
ingestar_libro_movimientos("data/2025.xlsx", PROY)
# ...o archivos de un mes cada uno:
# ingestar_movimientos("data/movimientos_2026-01.xlsx", PROY)

ingestar_ventas("data/ventas_2026-01.xlsx", PROY)
ingestar_ventas("data/ventas_2026-02.xlsx", PROY)
ingestar_ventas("data/ventas_2026-03.xlsx", PROY)

# Marcar centros de distribución (una vez)
marcar_logistica(PROY, ["CDC", "CR2"])
```

Recargar un período ya cargado: pasá `forzar=True` (movimientos, ventas, stock).

### Movimientos con varias pestañas (un Excel por año)

Si tu archivo de movimientos tiene una pestaña por mes (`01-25`, `02-25`, …),
usá `ingestar_libro_movimientos` (o, en la UI, marcá **"Archivo con varias
pestañas"**). Hace **una ingesta por pestaña**, así cada mes se detecta y
deduplica por separado, y lee una hoja a la vez (liviano en memoria). Una
pestaña que falle (mes ya cargado, u hoja que no es de datos) no corta las
demás: se reporta al final.

## 5. Verificar

En la app, **Inicio** muestra los contadores (inventarios, movimientos, ventas,
SKUs, sucursales) y el rango de fechas disponible. Después **Análisis** → elegí
sucursal + rango + valorización → *Calcular merma*.

## 6. Revisar el catálogo de tipos (opcional)

El mapeo `tipo → categoría` y qué suma a merma vive en la tabla
`tipos_categoria`. Para verlo o editarlo:

```python
import duckdb
conn = duckdb.connect("projects/cliente_x/data.duckdb")
print(conn.execute("SELECT * FROM tipos_categoria ORDER BY orden, tipo").df())
# ejemplo: agregar un subtipo nuevo
conn.execute("INSERT INTO tipos_categoria VALUES ('XX','REM','Remitido',FALSE,5,now())")
conn.close()
```
