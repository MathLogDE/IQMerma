# Guía de uso — MermaIQ

Analizador de merma de inventario. Esta guía cubre todo el flujo: instalar,
crear un proyecto, cargar datos, correr la app y leer los análisis.

## Índice
1. [Requisitos e instalación](#1-requisitos-e-instalación)
2. [Crear un proyecto](#2-crear-un-proyecto)
3. [Correr la app](#3-correr-la-app)
4. [La interfaz de un vistazo](#4-la-interfaz-de-un-vistazo)
5. [Cargar datos](#5-cargar-datos)
6. [Analizar la merma](#6-analizar-la-merma)
7. [Cómo se calcula la merma](#7-cómo-se-calcula-la-merma)
8. [Mantenimiento](#8-mantenimiento)
9. [Problemas comunes](#9-problemas-comunes)

---

## 1. Requisitos e instalación

- Python 3.12
- Dependencias:

```bash
pip install -r requirements.txt
```

Cada cliente es un proyecto independiente: un archivo
`projects/<proyecto>/data.duckdb`. Borrar o recargar un proyecto no afecta a
los demás.

## 2. Crear un proyecto

```bash
python setup_db.py --project cliente_x
```

Crea el schema vacío y siembra el catálogo de tipos de movimiento por defecto
(ver [sección 7](#7-cómo-se-calcula-la-merma)). Es idempotente: se puede
correr de nuevo sin romper datos.

## 3. Correr la app

```bash
streamlit run ui/app.py
```

Abre la interfaz en el navegador. En la **barra lateral** elegís el proyecto y
navegás entre las páginas. Si todavía no hay proyectos, la app te avisa cómo
crear uno.

## 4. La interfaz de un vistazo

**Barra lateral:** selector de **proyecto** + navegación entre 5 páginas.

| Página | Para qué |
|---|---|
| **Inicio** | Métricas rápidas del proyecto y rango de fechas disponible. |
| **Ingesta** | Ver qué períodos hay cargados y subir archivos nuevos. |
| **Análisis** | Merma por SKU para una sucursal y un rango de fechas. |
| **Sucursales** | Merma agregada, comparando todas las sucursales. |
| **Rubros** | Pareto de merma por rubro (usa lo calculado en Análisis). |

## 5. Cargar datos

### Orden de carga (importante)

1. **depositos** — primero. `stock` lo necesita para mapear las columnas de
   sucursal por su *Abreviación*.
2. **estructura** — rubros (para el análisis por Rubros).
3. **articulos** — SKUs (descripción, rubro, activo).
4. **stock** — snapshots (fuente de valorización: costo / lista_1).
5. **movimientos** — fuente principal del análisis: incluye ventas (VTA), el
   inventario físico (INV), remitos y ajustes.
6. **marcar logística** — marcar los centros de distribución (una vez).

> No hay archivo de ventas: la venta (denominador del %) se deriva de los
> movimientos VTA (FA/FB/NCA/NCB), valorizada desde el stock.

### Formato de cada archivo

Los nombres de columna se normalizan (mayúsculas/espacios), pero deben estar:

Columnas según el export del ERP (validadas contra archivos reales):

| Archivo | Columnas |
|---|---|
| depositos | `CD` · `Nombre Deposito` · `Abreviatura Deposito` · `Ubicación` (obligatorias: CD, Nombre Deposito, Abreviatura Deposito) |
| estructura | `CR` · `Descripción Rubro` · `Super Rubro:` · `Grupo Super Rubro:` (+ códigos `CGSR`/`CSR`, que se conservan) — obligatorias: CR, Descripción Rubro |
| articulos | `Código` · `Descripción` · `Rubro` · `Marca` · `EAN` · `Clase` · `Activo?` (mínimo: Código, Descripción, Activo?) |
| stock | `Código` · `Costo` · `Lista 1` · `Cant X Bulto` · + una columna por sucursal (encabezado = *Abreviatura* del depósito) |
| movimientos | `FECHA` · `TIPOMOV` · `TIPO` · `NUMERO` · `CODIGODEPO` · `NOMBRE` · `CODIGO` · `COSTO` · `INGRESO` · `EGRESO` · `DIFERENCIA` · `USER` · `TIPO AJ` (obligatorias: FECHA, TIPOMOV, TIPO, NUMERO, CODIGODEPO, CODIGO) |

Notas:
- Números en formato argentino (coma decimal) se parsean solos.
- **Códigos de SKU con ceros a la izquierda** (`0418886`): el Excel debe tenerlos
  como **texto**, no como número, o se pierden y no matchean entre archivos.
- `movimientos`: la `DIFERENCIA` se **recalcula** como `INGRESO - EGRESO`
  (tolera ingreso/egreso negativos, como en NCCA/RDC).
- `stock`: la columna derivada `LOG` (= CDC + CR2) se **ignora**; las demás
  columnas que no matcheen una abreviatura de depósito se reportan como
  advertencia. El stock logístico se computa sumando los depósitos marcados
  `es_logistica` (ver [sección 8](#8-mantenimiento)).
- `estructura`: se guarda el **código** de rubro (CR) y las **descripciones**;
  el join con artículos funciona tanto si `articulos.Rubro` trae el código como
  el nombre.

### Opción A — desde la interfaz

En **Ingesta → 📁 Cargar archivo**: elegís el tipo, subís el Excel y apretás
*Ingestar*, respetando el orden de arriba. Para `stock` te pide la *fecha del
snapshot*. La pestaña **📋 Períodos cargados** muestra todo lo ya ingestado.

### Opción B — script Python (más rápido para carga masiva)

```python
# cargar_todo.py
from ingesta.referencias import cargar_depositos, cargar_estructura, cargar_articulos, marcar_logistica
from ingesta.stock import ingestar_stock
from ingesta.movimientos import ingestar_movimientos, ingestar_libro_movimientos

PROY = "cliente_x"

cargar_depositos("data/depositos.xlsx", PROY)
cargar_estructura("data/estructura.xlsx", PROY)
cargar_articulos("data/articulos.xlsx", PROY)

ingestar_stock("data/stock_2026-03-31.xlsx", PROY, fecha_snapshot="2026-03-31")

# Movimientos: un archivo con varias pestañas (una por mes) → una sola llamada
ingestar_libro_movimientos("data/2026.xlsx", PROY)

# Marcar centros de distribución (una vez)
marcar_logistica(PROY, ["CDC", "CR2"])
```

### Movimientos con varias pestañas (un Excel por año)

Si tu archivo tiene una pestaña por mes (`01-26`, `02-26`, …), usá
`ingestar_libro_movimientos` (o, en la UI, marcá **"Archivo con varias
pestañas"**). Hace **una ingesta por pestaña**: cada mes se detecta y deduplica
por separado, y lee una hoja a la vez (liviano en memoria). Una pestaña que
falle (mes ya cargado, u hoja que no es de datos) no corta las demás.

### Recargar / actualizar un mes (carga incremental)

El dedupe es **por mes** (`YYYY-MM`). Recargar un mes que ya existe:

- **sin `forzar`** → lo rechaza (no duplica, pero tampoco actualiza),
- **con `forzar=True`** → **borra el mes completo y lo reinserta**; la carga
  anterior queda como `reemplazado` en `periodos_ingesta`. Los demás meses no
  se tocan.

> ⚠️ El archivo de recarga debe traer el **mes completo hasta la fecha**, no
> solo los días nuevos. La hora del corte no importa: el análisis filtra por la
> fecha real de cada movimiento.

**Caso típico — un libro anual donde solo cambia el mes en curso**
(`2026.xlsx`, y solo `07-26` crece). No recargues todo el libro; apuntá solo a
esa pestaña:

```python
from ingesta.movimientos import ingestar_movimientos, ingestar_libro_movimientos

ingestar_libro_movimientos("data/2026.xlsx", PROY)          # carga inicial (una vez)
ingestar_movimientos("data/2026.xlsx", PROY, sheet="07-26", forzar=True)  # actualización
```

Al arrancar el mes siguiente, cambiás a `sheet="08-26"`.

> En la UI el checkbox "varias pestañas" carga **todas** las hojas. Para
> actualizar un solo mes, o reprocesás todo el libro con "forzar", o hacés esa
> actualización puntual desde Python con `sheet=`.

## 6. Analizar la merma

### Página Análisis

1. Elegí la **sucursal** (o **⊕ Todas las sucursales**).
2. Elegí el **rango de fechas** (Desde / Hasta) — por defecto abarca todos los
   datos disponibles.
3. Elegí la **valorización**: *A costo* o *A precio de lista* (ver
   [sección 7](#7-cómo-se-calcula-la-merma)).
4. Elegí **Valorizar al (snapshot)**: la fecha del stock con la que se valoriza
   (entre los snapshots cargados; default el más reciente).
5. Apretá **Calcular merma**.

**Filtros** (se aplican al resultado, sin recalcular): Gran Super Rubro,
Rubro, Marca y búsqueda por código/descripción. Los KPIs y gráficos se
actualizan con el filtro.

**Qué ves:**
- **KPIs:** SKUs analizados, SKUs con merma, merma total ($ y unidades), venta
  total y **% de merma sobre ventas**.
- **Detalle por SKU:** una fila por SKU con rubro, una columna por **categoría
  de movimiento** (Ventas, Inventario, Dif. de camión, Ajustes, Remitido…) y el
  selector **Mostrar: Valorizado / Unidades / Ambos** para ver importes,
  cantidades o los dos. Los valores son **netos con signo** (positivo = entró
  stock, negativo = salió).
- **Dashboard:** composición de la merma por categoría (dona), top 15 SKUs por
  merma (barras apiladas) y merma vs % de merma por gran super rubro.
- **Comparativa por sucursal** (solo con "Todas"): tabla por sucursal (merma por
  categoría en $ y unidades, venta, %) + gráfico apilado con la línea de %.
  Respeta los filtros aplicados.
- **Exportar:** botones **⬇ Excel** / **⬇ CSV** debajo del detalle y de la
  comparativa (descargan el listado con los filtros aplicados), y
  **🖨 Reporte imprimible**: un HTML autocontenido con KPIs, composición,
  comparativa, merma por rubro y top SKUs — se abre en el navegador y se
  imprime o guarda como PDF con Ctrl+P.

> El **universo de SKUs** son los que tuvieron algún movimiento en el rango.

### Página Control de merma

Auditoría de la merma en tres vistas (mismos controles de período y
valorización):

- **Evolución mensual**: merma apilada por categoría + línea de % sobre venta,
  mes a mes, con la variación del último mes. Detecta tendencias.
- **Outliers**: los movimientos individuales de mayor impacto $ del período,
  con usuario, comprobante y % que representan de la merma bruta. Pares de
  igual magnitud y signo opuesto suelen ser anulaciones/correcciones.
- **Merma por usuario**: faltantes y sobrantes valorizados por usuario ×
  sucursal. Mucho volumen en ambos sentidos = correcciones cruzadas.

Todo descargable en Excel/CSV.

### Ingesta → ⚙ Depósitos

Editor para marcar los **centros de logística** (`es_logistica`) desde la UI.
Los CD se excluyen por defecto de la comparativa por sucursal (checkbox
"Excluir depósitos logísticos") y del análisis de salud de stock — no venden,
distorsionan el % de merma.

### Página Salud de stock

Cruza el stock (snapshot elegible) con la demanda reciente (ventana de 30 a
180 días) y clasifica cada SKU × sucursal:

| Estado | Significado |
|---|---|
| 🔴 Quiebre | Stock 0 con demanda → muestra la **venta perdida estimada $/día** |
| 🟠 Crítico | Cobertura por debajo del mínimo de la banda |
| 🟢 OK | Cobertura dentro de la banda saludable |
| 🔵 Sobrestock | Cobertura por encima del máximo → capital inmovilizado |
| ⚫ Muerto | Stock sin ventas en la ventana → liquidar / redistribuir |

La **banda de cobertura** (mín/máx en días) es ajustable. Los depósitos
marcados `es_logistica` se excluyen por defecto (los CD no venden). Filtros
por estado / gran super rubro / búsqueda, descarga Excel/CSV y gráficos:
conteo y capital por estado, top quiebres por venta perdida y stock muerto
por rubro.

### Página Min / Opt / Max

Políticas de inventario por SKU × sucursal (revisión periódica, order-up-to):

- **Ciclo de reposición estimado de los datos**: mediana de días entre
  llegadas (Remitido entrante), con fallback SKU global → rubro → default.
- **Clase ABC** dinámica (participación en la venta valorizada, 80/95 por
  sucursal) → nivel de servicio A 95% · B 90% · C 80%; **clase XYZ** por
  regularidad de la demanda (CV semanal).
- **Bandas**: seguridad = z·σd·√(lead+ciclo); Mín = demanda·lead + seguridad;
  Ópt = demanda·(lead+ciclo) + seguridad; Máx = Ópt + seguridad.
- **Estados**: 🔴 Reponer (con compra sugerida hasta el óptimo, redondeada a
  bultos cuando el bulto cabe en el óptimo) · 🟢 OK · 🔵 Exceso · ⚫ Sin demanda.
- Parámetros ajustables: lead time y ciclo default. KPIs, matriz ABC×XYZ,
  top compras sugeridas y descarga Excel/CSV.

### Página Forecast

Proyección mensual de unidades por nivel de agregación (total / gran super
rubro / rubro / sucursal — a nivel SKU la demanda es errática y un forecast
puntual sería ruido). **Métrica elegible**:

- **Ventas** (salida es_venta, valorizada a precio de lista), o
- **Transferencias recibidas** (Remitido entrante, valorizado a costo) — para
  planificar el abastecimiento. Con "Superponer" se dibuja la serie real de
  la otra métrica: si las transferencias caen antes que las ventas, la caída
  es de **abastecimiento**; si las ventas caen con transferencias normales,
  es de **demanda**.

> Nota: a nivel "Total", las transferencias recibidas suman los dos
> escalones (proveedor→CD y CD→sucursal); por sucursal la lectura es directa.

Características del modelo:

- Modelo transparente: índices estacionales + tendencia robusta (Theil-Sen)
  **amortiguada** — una caída reciente no se extrapola al infinito.
- Los **meses atípicos** (lotes mayoristas, anulaciones masivas) se detectan
  por MAD, no dominan el ajuste y se marcan con ✕ en el gráfico.
- El **MAPE de backtest** (re-predecir los últimos 3 meses reales) se muestra
  como medida honesta del error esperado. Banda de confianza ~95%.
- El valor $ se deriva de las unidades × precio de lista actual promedio.
- Requiere ≥ 12 meses de historia por grupo (ideal 24 para estacionalidad).

### Página Sucursales

Mismo rango y valorización, pero calcula la comparativa de **todas las
sucursales** en una sola pasada: métricas globales, tabla por sucursal (merma
por categoría en $ y unidades, venta, %), gráfico apilado con línea de % y
descarga Excel/CSV. Útil para detectar qué locales concentran la merma.

> Ojo: los **centros de distribución** aparecen en la comparativa con venta
> baja o negativa (no venden; reciben). Al comparar % de merma entre locales,
> mirá las sucursales de venta.

### Página Rubros

Pareto de merma por **rubro** (o super rubro / gran super rubro). Usa lo que
calculaste en **Análisis**, así que primero corré esa página. Muestra las barras
de merma por rubro ordenadas y la curva de **% acumulado** (regla 80/20).

## 7. Cómo se calcula la merma

**Merma = pérdida no explicada.** Por SKU y por categoría se suma la
`diferencia` (neta) de los movimientos del rango, y se valoriza contra el
**stock** (snapshot más reciente ≤ fecha hasta).

- **Categorías de merma** (`es_merma`, suman al numerador): **Inventario (INV)**,
  **Dif. de camión (MD)** y **Ajustes (CS)** — solo su **parte negativa**
  (faltante).
- **Categoría de venta** (`es_venta`, denominador): **Ventas**
  (FA/FB/NCA/NCB/FCA/NCCA). La venta del período = unidades netas vendidas
  (la salida) valorizadas desde el stock. Las devoluciones (NCA/NCB) descuentan.
- **No son ni merma ni venta**: **Remitido** (RE/RI/RDC) — flujos legítimos.
- **Valorización — siempre desde el stock**:
  - *A costo* → unidades × `costo` del stock.
  - *A precio de lista* → unidades × `lista_1` del stock.
  Aplica tanto al numerador (merma) como al denominador (venta), en ambos modos.
- **% merma = merma total valorizada / venta del período × 100.**

El mapeo `tipo → categoría`, `es_merma` y `es_venta` viven en la tabla
`tipos_categoria`, **editable sin tocar código** (ver
[sección 8](#8-mantenimiento)). Un subtipo que no esté en el catálogo cae en la
columna **"(sin categoría)"** y no suma a merma ni a venta (para que nada se
pierda en silencio).

## 8. Mantenimiento

### Empezar de cero (borrar datos)

```bash
python setup_db.py --project cliente_x --reset   # confirmás con "s"
```

Borra y recrea todas las tablas y **resetea el catálogo `tipos_categoria`** a
los valores por defecto (si lo habías editado, se pierde).

### Marcar centros de logística

El flag `es_logistica` distingue los centros de distribución (CDC, CR2) de las
sucursales de venta. Se marca a mano una vez y **se preserva** en las recargas
de depósitos:

```python
from ingesta.referencias import marcar_logistica
marcar_logistica("cliente_x", ["CDC", "CR2"])          # marcar
marcar_logistica("cliente_x", "CDC", es_logistica=False)  # desmarcar
```

### Editar el catálogo de tipos

```python
import duckdb
conn = duckdb.connect("projects/cliente_x/data.duckdb")
print(conn.execute("SELECT * FROM tipos_categoria ORDER BY orden, tipo").df())
# agregar/ajustar un subtipo (tipo, tipomov, categoria, es_merma, orden, fecha)
conn.execute("INSERT OR REPLACE INTO tipos_categoria VALUES ('XX','REM','Remitido',FALSE,5,now())")
conn.close()
```

## 9. Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| "La sucursal no existe en depositos" al cargar stock | Cargá **depositos** antes que stock. |
| Columnas de stock ignoradas | El encabezado no matchea ninguna *Abreviación* de depósito. Revisá la columna `Abreviación`. |
| "El período ya fue cargado" | El mes ya existe. Usá `forzar=True` (o el checkbox "forzar") para reemplazarlo. |
| Un SKU sin `% Merma` | No tiene ventas en el rango (denominador 0). |
| Columna **"(sin categoría)"** con valores | Hay subtipos de movimiento que no están en `tipos_categoria`. Agregalos al catálogo. |
| Merma valorizada en 0 pese a haber faltantes | El SKU no tiene snapshot de stock ≤ fecha hasta → no se puede valorizar. Cargá un stock que cubra el período. |
| La página Rubros dice "Calculá primero el análisis" | Corré **Análisis** antes; Rubros reutiliza ese resultado. |
