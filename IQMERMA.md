# IQMerma — Documento de referencia

> **Versión del documento:** 1.1 · **Fecha:** 2026-10-05 · **Base:** branch `desarrollo-compliance`
> (sale de `visualizaciones` @ `bbaf0f9`). La estrategia hacia ISO 9001 / Lean Six Sigma está en
> [ESTRATEGIA_COMPLIANCE.md](ESTRATEGIA_COMPLIANCE.md).
>
> ⚠️ **Uso interno.** Este documento incluye datos reales del cliente (Yuriti S.A.), cifras
> medidas, cuentas y rutas de la máquina. Revisalo antes de pasárselo a terceros. **Nunca**
> contiene secretos: contraseñas, claves de cookie ni el contenido de `auth/usuarios.yaml` o
> `auth/smtp.json`.

**Para qué sirve:** es el norte del proyecto. Dice qué es IQMerma, qué hace y qué no hace,
por qué calcula como calcula y cómo se protegen las No Conformidades. Se lee al empezar una
sesión de trabajo, sea de una persona o de un modelo, y se le pasa a quien tenga que
entender el sistema.

**Cómo mantenerlo:**
- Al cerrar un cambio relevante, actualizá la sección que corresponda:
  - §3: funcionalidad nueva.
  - §4: un límite nuevo o que deja de serlo.
  - §5: una decisión de cálculo nueva o cambiada.
  - §6/§7: seguridad.
  - §9: estado.
- Cada cambio de este documento suma una línea al [changelog](#12-changelog-del-documento).
- Si algo no está confirmado en el código, se marca **(a validar)**. No se afirma.
- El detalle vive en los docstrings de cada módulo. Este documento resume y enlaza, no copia.

---

## Índice
1. [Qué es IQMerma](#1-qué-es-iqmerma)
2. [Arquitectura](#2-arquitectura)
3. [Funcionalidades actuales](#3-funcionalidades-actuales)
4. [Qué NO hace](#4-qué-no-hace-límites-explícitos)
5. [Decisiones de cálculo](#5-decisiones-de-cálculo)
6. [No Conformidades: seguridad y compliance](#6-no-conformidades-seguridad-y-compliance)
7. [Seguridad general](#7-seguridad-general)
8. [Entorno y operación](#8-entorno-y-operación)
9. [Estado y pendientes](#9-estado-y-pendientes)
10. [Glosario](#10-glosario)
11. [Mapa de documentos](#11-mapa-de-documentos)
12. [Changelog del documento](#12-changelog-del-documento)

---

## 1. Qué es IQMerma

IQMerma es una **suite local de análisis de inventario para retail multi-sucursal**. Su
núcleo es la **merma**, es decir la pérdida de inventario que nada explica. Nació para
reemplazar el flujo de Excel y Power Pivot con el que el cliente calculaba la merma. Hoy
cubre cinco dominios: merma, inventario, distribución, comercial y forecasting. A eso suma
un módulo de **auditoría (No Conformidades, ISO 9001 §10.2)** para formalizar los desvíos
que se detectan en las sucursales.

- **Datos de entrada:** exports en Excel del ERP del cliente:
  - movimientos;
  - snapshots de stock por sucursal;
  - depósitos;
  - estructura de rubros;
  - artículos.

  IQMerma **solo lee** esos exports y no se conecta al ERP.
- **Usuarios:**
  - el analista/administrador (Mathias), que usa la instancia admin: ingesta y todas las
    páginas;
  - la gente del cliente (coordinación, calidad, encargados), que entra por web con login a
    una instancia de solo lectura, salvo No Conformidades según los roles de cada usuario.
- **Cliente actual:** **Yuriti S.A.**, una cadena de bazar/decoración en Argentina
  (proyecto `projects/Yuriti_S.A`). Datos del proyecto:
  - 29 depósitos marcados como `es_logistica`;
  - unos 120 mil SKUs en catálogo, de los que alrededor de 29 mil tuvieron alguna venta;
  - cerca de 5 millones de movimientos;
  - historia desde 2024-01.
- **Nombre:** "**IQMerma**" es el nombre de la suite (logo, pie de los mails). "**MermaIQ**"
  es el nombre anterior y todavía aparece en el código, en el título de la página, en el
  README y en la cookie. Los dos nombran lo mismo.

**Principios que atraviesan todo el código:**
1. **Nada de caja negra.** Cada modelo o regla deja registrado qué decidió y por qué, por
   ejemplo qué modelo ganó y con qué error.
2. **No mostrar lo que no sirve.** Una proyección con historia insuficiente o un backtest
   desastroso se excluye y no se maquilla.
3. **Nada se pierde en silencio.** Un subtipo sin mapear cae en "(sin categoría)", un mes
   atípico se reporta y un grupo excluido aparece con su motivo.
4. **Medir antes de reemplazar.** Un método nuevo convive con el baseline hasta que una
   evaluación con la misma métrica demuestra que lo mejora.

---

## 2. Arquitectura

### Stack
| Capa | Tecnología |
|---|---|
| Datos analíticos | DuckDB 1.5.3: un `projects/<p>/data.duckdb` por cliente |
| Datos de auditoría (NC) | SQLite: `projects/<p>/no_conformidades.sqlite` (WAL, `busy_timeout`) |
| Procesamiento | Python 3.12, **pandas 3.0.3 (fijado)**, NumPy |
| UI | Streamlit 1.58 · Plotly 6 |
| Forecast | statsmodels (Holt-Winters/SARIMA), scikit-learn (kNN), LightGBM, Prophet + `holidays` |
| Reportes | Jinja2 → HTML autocontenido con gráficos SVG propios (`core/graficos.py`) |
| PDF | Edge/Chrome headless con `--print-to-pdf` (`core/pdf.py`) |
| Excel | openpyxl |
| Auth web | streamlit-authenticator 0.4.2 (bcrypt) |

### Dos instancias de la misma app
| | `ui/app.py` (**admin**) | `ui/app_cliente.py` (**cliente**) |
|---|---|---|
| Acceso | Local, sin login | Web con login, expuesta por túnel ngrok (`iniciar_cliente.cmd`) |
| Puerto | 8501 | 8502 |
| DuckDB | Lectura y escritura (ingesta) | `setup_db.activar_modo_solo_lectura()` → `read_only=True` |
| Páginas | Todas, más Ingesta, Calidad de datos, Últimos movimientos y Pedido general (v1) | Las de `core/auth.PAGINAS_DISPONIBLES`, filtradas por el `paginas:` de cada usuario |
| Proyectos | Selector abierto | Solo los del `proyectos:` del usuario |
| NC | Actor con todos los roles | Actor con los `roles_nc` del usuario |

### Por qué hay dos bases
DuckDB toma un **lock exclusivo** al escribir. La instancia cliente la abre en solo lectura
para no competir con una ingesta en curso. Las NC, en cambio, necesitan **escrituras de
varios usuarios** desde la instancia cliente. Por eso viven en un SQLite separado, con WAL.
Ese archivo **tiene que estar en disco local** y nunca en un recurso compartido SMB, porque
el locking de SQLite no es confiable ahí.

### Capas del código
```
ingesta/        Excel del ERP → DuckDB (movimientos, stock, referencias)
setup_db.py     schema + catálogo tipos_categoria + modo solo lectura
core/           motores sin dependencias de UI
  comun.py        conexión, valorización desde snapshot, jerarquía, catálogos
  merma/  inventario/  distribucion/  comercial/  forecasting/
  calidad_datos.py
  no_conformidades.py · sop.py · nc_anexos.py · nc_mail.py · nc_contactos.py
  reporte.py · graficos.py · pdf.py · auth.py
ui/             Streamlit: app.py / app_cliente.py (routers), comun.py (fachada + caché),
                paginas/<pagina>.py (una función render por página)
templates/      Jinja2 de reportes y de la NC (base.html con branding del cliente)
projects/<p>/   data.duckdb, no_conformidades.sqlite, branding.json, logo, sop/, adjuntos (fuera de git)
auth/           usuarios.yaml, smtp.json (fuera de git) · usuarios.ejemplo.yaml (versionado)
tests/          test_no_conformidades.py (unittest, 47 tests)
```

### Ingesta
- **Movimientos** (`ingesta/movimientos.py`): un Excel por año con una pestaña por mes.
  Detecta el período, verifica duplicados contra `periodos_ingesta` y permite recargar un
  mes (`forzar=True`).
- **Stock** (`ingesta/stock.py`): columnas pivotadas por sucursal. Se despivotea y los
  snapshots se **acumulan, nunca se pisan**. La vista `stock_actual` apunta al último.
- **Referencias** (`ingesta/referencias.py`): upsert por clave. Lo que deja de aparecer en
  el archivo no se borra, se conserva el histórico.
- **Orden de carga:** depósitos → estructura → artículos → stock → movimientos (detalle en
  GUIA_USO §5).

---

## 3. Funcionalidades actuales

Estado: ✅ en uso · 🧪 en prueba · 🔒 solo admin.

### General
| Página | Qué responde | Motor | Estado |
|---|---|---|---|
| Inicio | Rango de datos disponible, últimas ingestas | `ui/paginas/inicio.py` | ✅ |
| Ingesta | Carga de archivos, ⚙ depósitos (es_logistica), usuarios web y permisos | `ingesta/*`, `core/auth.py` | ✅ 🔒 |
| Calidad de datos | ¿Qué días y sucursales tienen muchos menos movimientos de lo normal? (candidatos a gap de ingesta) | `core/calidad_datos.py` | ✅ 🔒 |

### Merma
| Página | Qué responde | Motor | Estado |
|---|---|---|---|
| Análisis (pestañas: comparativa por sucursal, Pareto por rubro) | Merma por SKU y categoría, % sobre venta, % de SKUs contados, en un período y sucursal | `core/merma/analisis.py` | ✅ |
| Control de merma | Evolución mensual, movimientos outlier por impacto $, ranking de ajustes por usuario o motivo (control de errores y fraude) | `core/merma/control.py` | ✅ |
| Avance de inventario | ¿Qué tan avanzado está el conteo físico por sucursal y rubro? Unidades contadas y diferencia | `core/merma/avance.py` | ✅ |
| Matriz por sucursal | Diferencias de inventario y ajustes (u y $), SKUs inventariados y venta por rubro/marca/SKU × sucursal. Export Excel | `core/merma/analisis.py` | ✅ |
| Transformaciones | Detalle línea a línea de las TR: baja del SKU original y alta del SKU de liquidación | `core/merma/transformaciones.py` | ✅ |
| Últimos movimientos | Fecha del último movimiento de cada tipo de comprobante, por SKU, en una sucursal | `core/merma/ultimos_movimientos.py` | ✅ 🔒 |

### Inventario
| Página | Qué responde | Motor | Estado |
|---|---|---|---|
| Salud de stock | Clasificación de cada SKU × sucursal en quiebre / crítico / ok / sobrestock / muerto por cobertura | `core/inventario/salud.py` | ✅ |
| Serie de stock | Reconstrucción diaria del stock, quiebres, inventarios y ajustes marcados | `core/inventario/series.py` | ✅ |
| Cruce por sucursal | Evolución de stock de un recorte de catálogo en varias sucursales | `core/inventario/series.py` | ✅ |
| Evolución interanual | El mismo recorte comparado entre años, a precio constante, con meses parciales recortables | `core/inventario/interanual.py` | ✅ |
| Perfil de reposición | Cadencia de remitos, días en quiebre o bajo una barrera, tiempo hasta el quiebre desde el último remito | `core/inventario/reposicion.py` | ✅ |
| Pedido desde sucursal | Hoja de pedido por artículo para una sucursal, con cantidad recomendada para una cobertura | `core/inventario/reposicion.py` | ✅ |
| Pedido general de distribución 2 | Hoja de distribución multi-sucursal (pull) y total a despachar desde el CD | `core/inventario/pedido_general.py` | 🧪 (la v1 sigue registrada solo en admin) |
| Min / Opt / Max | Políticas de revisión periódica por SKU × sucursal, ABC/XYZ, estado reponer/ok/exceso | `core/inventario/politicas.py` | ✅ |

### Distribución
| Página | Qué responde | Motor | Estado |
|---|---|---|---|
| Transferencias | Sugerencia de transferencias laterales: excedentes de una sucursal a otra que necesita reponer | `core/distribucion/transferencias.py` | ✅ |
| Diferencias de camión | Faltantes y sobrantes al recibir (REM/MD), contra el volumen remitido | `core/distribucion/diferencias.py` | ✅ |

### Comercial
| Página | Qué responde | Motor | Estado |
|---|---|---|---|
| Márgenes | Margen teórico y bruto, **merma como % del margen**, evolución de costos (necesita al menos 2 snapshots) | `core/comercial/margenes.py` | ✅ |
| Comprobantes y canasta | Comprobantes por sucursal, ticket promedio, perfil del SKU en el comprobante, afinidad | `core/comercial/comprobantes.py` | ✅ |

### Forecasting (página Forecast)
| Sección | Qué hace | Motor | Estado |
|---|---|---|---|
| Forecast mensual por grupo | Competencia de modelos por grupo (total / gran super rubro / rubro / marca / sucursal), con selector "ver otro modelo" | `core/forecasting/demanda.py` | ✅ |
| Detalle por SKU | Demanda estimada vs. stock (baseline), segmento de demanda y método elegible (Automático / Baseline / Croston-TSB / LightGBM) | `demanda.py`, `segmentacion.py`, `intermitente.py`, `pooled.py` | ✅ |
| Forecast semanal con feriados AR | Prophet por total / gran super rubro / sucursal | `core/forecasting/semanal.py` | ✅ |
| Forecast semanal por SKU | Prophet para una lista acotada de SKUs × sucursales, con comparación año a año por semana ISO | `core/forecasting/semanal_sku.py` | ✅ |
| Evaluación comparativa | ¿El método nuevo mejora al baseline? WMAPE por segmento y curva real vs. predicho | `core/forecasting/evaluacion.py` | ✅ |

### Auditoría
| Página | Qué hace | Motor | Estado |
|---|---|---|---|
| No conformidades | Ciclo completo de una NC: borrador → emisión con número y hash → envío por mail con PDF → descargo → verificación de eficacia → cierre. También observaciones, SOP versionado, anexos con datos de IQMerma, agenda de contactos, verificación de integridad y backup | `core/no_conformidades.py` + `sop.py`, `nc_anexos.py`, `nc_mail.py`, `nc_contactos.py` | ✅ v1 (mail probado solo con SMTP simulado) |

### Transversal
- **Reportes imprimibles con la marca del cliente:** casi todas las páginas tienen uno
  (`core/reporte.py`). Es HTML autocontenido, sin JS ni pedidos a la red. Toma logo, color y
  pie de `projects/<p>/branding.json`. Se imprime con Ctrl+P o sale como PDF.
- **Export a Excel** en las tablas principales.
- **Catálogo `tipos_categoria`** editable sin tocar código.

---

## 4. Qué NO hace (límites explícitos)

- **No es un ERP ni escribe en él.** Solo lee exports. No corrige datos de origen: si falta
  un movimiento en el ERP, IQMerma lo ve como un desfase.
- **No hay conexión en vivo.** Los datos están tan frescos como la última ingesta.
- **No hay stock histórico real**, solo los snapshots cargados. La serie diaria es una
  reconstrucción: es exacta si los movimientos están completos y no es una medición.
- **La merma no incluye las diferencias de camión (MD)** desde el 2026-07-15, a pedido del
  cliente. Remitos y transferencias tampoco son merma.
- **No valoriza con el costo transaccional del movimiento.** Siempre usa el snapshot de
  stock (ver §5.2).
- **No tiene surtido por sucursal.** El denominador "catálogo" del avance de inventario es
  el mismo para todas las sucursales.
- **Forecast:**
  - Los métodos nuevos por SKU (Croston/TSB y LightGBM) **no reemplazaron** al baseline en
    producción. La vista "Automático" los mezcla según la Fase 5, pero el cambio de fondo
    es una decisión de producto pendiente.
  - SARIMA no corre en nivel `marca`.
  - Prophet semanal no corre en `rubro` ni en `marca`.
  - El forecast semanal por SKU exige una lista acotada (como mucho 150 combinaciones).
  - Grupos con WMAPE de backtest mayor a 150 % se excluyen, no se muestran.
  - No hay forecast de merma, solo de ventas y transferencias.
- **Calidad de datos** encuentra *candidatos* a gap por sucursal y día. **No confirma** gaps
  ni los detecta a nivel SKU. Eso se hace a mano, cruzando snapshot contra reconstrucción.
- **No Conformidades:**
  - no hay WhatsApp;
  - no hay recordatorios automáticos de vencimientos;
  - **no se registra el acuse de recibo** (se pide en el mail, pero no se carga en el
    sistema);
  - todavía no hay KPIs, reincidencia, métrica Y ni evidencias automáticas (eso es la v2);
  - todavía no salió ningún mail real.
- **La instancia cliente no tiene ingesta** ni la pestaña Configuración de NC, y no puede
  hacer backup de NC.
- **No hace conteo móvil ni es un WMS, por decisión.** IQMerma es de solo lectura sobre el
  inventario (ADR-1). El conteo móvil, si llega, va a ser una app separada con contrato de
  datos (ADR-2). Un WMS no se construye (ADR-3). Ver
  [ESTRATEGIA_COMPLIANCE.md](ESTRATEGIA_COMPLIANCE.md) §2.
- **No es multi-tenant en un servidor.** Corre en una PC Windows del analista, y los
  clientes entran por un túnel.

---

## 5. Decisiones de cálculo

Formato: **qué** → **por qué** → *dónde*.

### 5.1 Fórmula central de merma
`% merma = merma valorizada / venta valorizada del período × 100`
- **Merma (numerador):** el neto con signo de las categorías `es_merma` (Inventario = INV y
  Ajustes = CS, y además Transformación = TR donde esté en el catálogo, ver §5.3) **por
  SKU**, negado: un faltante suma y un ajuste positivo resta. **Se
  compensan dentro del mismo código.** Un egreso de INV y un ingreso de CS del mismo SKU se
  cancelan, y un SKU puede quedar con merma negativa, que es un sobrante neto. En la UI se
  muestra con barra verde.
  → Una corrección de inventario cargada como ajuste no debe contarse dos veces.
  *`core/merma/analisis.py`*
- **Venta (denominador):** las unidades netas de las categorías `es_venta`
  (FA/FB/NCA/NCB/FCA/NCCA), valorizadas. Las notas de crédito descuentan.
- **Universo de SKUs:** los que tienen algún movimiento en el rango y la sucursal.
- **Anclaje:** por **selección de período** (sucursal + rango). No hay un "evento de
  conteo" (refactor v2, MIGRACION_V2).

### 5.2 Valorización: siempre desde el snapshot de stock
- Unidades × `costo` o × `lista_1` del snapshot más reciente ≤ `fecha_hasta` (o el que se
  elija). En control hay además un modo mixto: ventas a lista, el resto a costo.
- **Por qué no el costo del movimiento:** cada subtipo trae algo distinto. CS, INV y MD
  traen 0; FA, FB, NCA y NCB traen precio de venta; RE, RI y NCR traen costo.
- El precio es **global por SKU** en el ERP (una sola columna), así que no se filtra por
  sucursal.
- Interanual, comprobantes y transformaciones usan **precio constante** (un solo snapshot
  para todos los años). Así se compara volumen y mix, no inflación.

### 5.3 Catálogo `tipos_categoria` (editable sin código)
| Categoría | Subtipos (`tipo`) | Rol |
|---|---|---|
| Ventas | FA, FB, NCA, NCB, FCA, NCCA | denominador (`es_venta`) |
| Inventario | INV | **merma** |
| Ajustes | CS | **merma** |
| Transformación | TR | **merma** en el catálogo por defecto (`setup_db.DEFAULT_TIPOS_CATEGORIA`), con detalle en `core/merma/transformaciones.py`. ⚠️ Al 2026-10-05, el catálogo de `Yuriti_S.A` **todavía no tiene la fila TR**: la agrega la migración `setup_db._migrar`, que solo corre con `python setup_db.py --project Yuriti_S.A` (sin `--reset`). Hasta entonces TR cae en "(sin categoría)" |
| Dif. de camión | MD | informativa (fue merma hasta el 2026-07-15) |
| Remitido | RE, RI, RDC | informativa |
| (sin categoría) | todo subtipo sin mapear | no suma a nada, pero se ve |

El mapeo se hace **por `tipo`**. `tipomov` es informativo.

### 5.4 Particularidades de los datos del ERP (no romper)
- **Fechas swapeadas por Excel en locale US:** los días ≤ 12 quedaron como datetime con
  día y mes invertidos, y los días ≥ 13 como texto DD/MM. `ingesta/movimientos.py::_parsear_fechas`
  lo corrige usando el nombre de la pestaña (`11-25` = nov-2025) como período esperado.
  **Nunca** se parsea con un `pd.to_datetime(dayfirst=True)` simple: ese bug desparramó
  meses y perdió la mitad de los datos.
- **El inventario físico viene como `TIPOMOV='AJU'`, `TIPO='INV'`.** Todo filtro de
  inventario va por `tipo`.
- **Los códigos de SKU tienen ceros a la izquierda** ("0418886"). Siempre se leen como
  texto.
- **En MD, `diferencia` no sirve.** El ERP exporta el egreso negativo solo en ese tipo. El
  sentido se reconstruye desde `ingreso`/`egreso` (`core/distribucion/diferencias.py`).
- **Comprobante = (numero, codigodepo, tipo).** La numeración se repite entre tipos.

### 5.5 Reconstrucción de stock diaria
`stock(d) = stock(D) + acum(d) − acum(D)`, donde D es la fecha del snapshot y `acum` es la
suma acumulada de `diferencia`. Se devuelve como **función escalón**: una fila por día con
movimiento.
- **Diagnóstico de "stock desfasado":** antes de tocar `series.py`, se compara cada
  snapshot contra la reconstrucción. Si hay un offset constante durante semanas que después
  cae a 0 sin un movimiento que lo explique, es un **gap del ERP**, no un bug.
  Caso confirmado: SKU 2969010 en el depósito 011 (Rodríguez Busto), +6 entre el 11/07 y el
  22/08/2026. El cliente confirmó el gap y lo corrigió en el ERP.

### 5.6 Inventario, políticas y salud
- **Salud:** `cobertura = stock / venta_diaria`, sobre una ventana de 90 días. Estados:
  - quiebre: stock ≤ 0 con demanda;
  - crítico: cobertura menor a 7 días;
  - sobrestock: cobertura mayor a 60 días;
  - muerto: hay stock pero no hubo ventas.

  Los depósitos `es_logistica` se excluyen por defecto.
- **Min/Opt/Max:** revisión periódica, order-up-to.
  - Fórmulas:
    - `SS = z·σd·√(L+T)`
    - `min = d·L + SS`
    - `opt = d·(L+T) + SS`
    - `max = opt + SS`
  - T es la mediana entre remitos. Si no se puede calcular cae en cascada: SKU×sucursal →
    SKU → rubro → default.
  - L vale 7 días por defecto.
  - z sale de la clase ABC: A = 1,65, B = 1,28, C = 0,84.
  - XYZ por CV semanal: 0,5 y 1,0.
- **Transferencias:** el donante cede hasta quedar en su óptimo y el receptor recibe hasta
  su óptimo. La asignación es greedy, empezando por la clase A. El stock logístico queda
  afuera.
- **Avance de inventario:** "contado" significa que hubo al menos una línea INV en el rango
  (presencia, no neto). El denominador puede ser "movimientos" o "catálogo".
  ⚠️ **Lectura de INV verificada en datos (2026-10-05):** el ERP genera líneas INV también
  cuando el conteo coincide (`diferencia = 0`, con `ingreso = egreso = 0`), y ninguna línea
  trae ingreso y egreso positivos a la vez. **La línea INV no contiene la cantidad contada
  ni la teórica**, solo el delta del ajuste. Por eso "unidades contadas = SUM(ingreso)" en
  `avance_conteo` en realidad suma los sobrantes ajustados: es un issue abierto
  (ESTRATEGIA_COMPLIANCE §5.4). La IRA solo puede calcularse con tolerancia absoluta
  (§4.1 de la estrategia).

### 5.7 Forecasting
- **El nivel agregado tiene señal; el SKU individual casi siempre es ruido.** El mensual
  compite por grupo:
  - `estacional_robusto`: Theil-Sen + estacionalidad multiplicativa, amortiguado con
    φ = 0,8. Es el piso que nunca falla.
  - `holt_winters`.
  - `sarima`: necesita 30 meses de historia **total**, y no corre en `marca` por costo
    (unos 217 s contra 80 s).
  - Gana el de menor **MAPE** de backtest.
  - Con 3 a 11 meses de historia se le presta el patrón de los 5 vecinos más parecidos
    (kNN), con una etiqueta explícita. Con menos de 3 meses no se proyecta.
  *`core/forecasting/demanda.py`*
- **Limpieza de atípicos sobre el RESIDUO** de estación + tendencia (mediana ± 3,5·MAD), no
  sobre el valor crudo. Así **diciembre no se recorta**: el pico de Navidad es real. Los
  meses atípicos se reportan.
- **Segmentación de SKU:** ADI/CV² (Syntetos-Boylan, umbrales 1,32 y 0,49) en continuo,
  intermitente, errático, lumpy, sin historia suficiente (menos de 6 meses con venta) y sin
  ventas. Medido en Yuriti: 3.584 / 2.435 / 8.807 / 5.011 / 9.635 / 91.272.
- **Intermitente:** Croston vs. TSB a mano en NumPy, compitiendo por **MAE** (MAPE no sirve
  con tantos ceros).
- **Continuo, errático y lumpy:** **un único LightGBM pooled** (tweedie), con lags
  1/2/3/6/12 y medias móviles 3/6. El SKU no entra como categórica. La proyección es
  recursiva y la banda de error se calcula por SKU.
- **Semanal:** Prophet con feriados AR más el Día de la Madre (3er domingo de octubre) y el
  Día del Niño (3er domingo de agosto), con ventana de ±3 días. Se excluye el grupo si el
  **WMAPE es mayor a 150 %**. Caso típico: NAVIDAD, que es todo o nada y que Fourier no
  puede representar.
- **WMAPE** (no MAPE) en todo lo que tiene muchos ceros: pooled, semanal y evaluación.
- **Fase 5, medida en Yuriti y sin maquillar:**

  | Segmento | Baseline | Método nuevo | Resultado |
  |---|---|---|---|
  | continuo/errático/lumpy | 112 % | **72 %** (LightGBM) | gana el modelo nuevo |
  | intermitente | **161 %** | 175 % (Croston/TSB) | gana el baseline |

  El baseline de la evaluación es una aproximación mensual del baseline diario de
  producción, no la misma función.
- **Librerías:** nada de `statsforecast` ni `mlforecast`, porque fuerzan `pandas<3`.

### 5.8 Calidad de datos
Para cada sucursal se cuentan los movimientos por día y se comparan contra la mediana
histórica **del mismo día de la semana**, con una ventana de 9 ocurrencias. Hay alerta si
el día queda por debajo del 15 % de esa mediana, y la mediana tiene que ser de al menos 30.
Se resalta cuando faltan pocas sucursales (2 o menos) y el resto operó normal.
*`core/calidad_datos.py`*

### 5.9 Plazos de una NC
- Los días hábiles excluyen el domingo, el sábado (configurable) y los feriados nacionales
  más los de la provincia de cada depósito (`provincia_por_deposito`), más `feriados_extra`.
  El paquete `holidays` no trae feriados de Santiago del Estero.
- Los plazos **recién corren desde ENVIADA** (la recepción). La NC impresa solo muestra
  plazos relativos.

| Tipo (severidad) | Contención | Descargo | Implementación máx. |
|---|---|---|---|
| AMARILLO (1) | — | 10 días hábiles | la propone la sucursal |
| NARANJA (2) | vence con el descargo | 5 días hábiles | 60 días corridos |
| ROJO (3) | 48 h corridas | 3 días hábiles | 30 días corridos |

---

## 6. No Conformidades: seguridad y compliance

Marco: **ISO 9001 §10.2** (no conformidad y acción correctiva), **§7.5** (información
documentada) y el **SOP Rio Shop v2** del cliente (§8.1 observaciones, §8.2 escala de NC,
§6 correo oficial).

### 6.1 Ciclo de vida
```
BORRADOR → EMITIDA → ENVIADA → RESPONDIDA → EN_VERIFICACION → CERRADA
                        ↑           │                       ↘ CERRADA_NO_EFICAZ (+ NC nueva)
                        └───────────┘ descargo observado
cualquier estado salvo CERRADA* → ANULADA
```
- Las transiciones válidas están en `TRANSICIONES` y **además las impone un trigger SQL**
  (`nc_transicion`), como segunda barrera.
- **Escala AMARILLO < NARANJA < ROJO.** El SOP vigente (§8.2) dice Amarillo/Rojo/Negro; se
  le avisó al usuario y decidió mantener esta escala. Es una diferencia conocida y aceptada.
- El descargo exige corrección inmediata, causa raíz, **categoría 6M** (Método, Mano de
  obra, Material, Máquina, Medición, Medio) y al menos una acción correctiva con
  responsable y fecha compromiso.
- **Observaciones** (SOP §8.1): un registro simple numerado `OBS-NNDDDMMAAAA`, con un
  correlativo propio por sucursal y mes. Son append-only y quedan en la cadena de eventos.

### 6.2 Identidad y numeración
- Número `NNDDDMMAAAA` (correlativo por sucursal y mes + depósito + mes + año). Se asigna
  **al emitir** dentro de `BEGIN IMMEDIATE`, para evitar carreras, y **nunca se reutiliza**.
  Los borradores no consumen número.

### 6.3 Congelamiento e integridad del documento
- **Al emitir** se congelan todas las columnas de contenido (`_CONGELADAS`): sucursal,
  tipo, plazos, requisito del SOP, evidencia, desvío, acciones, línea base de merma y el
  HTML. El trigger `nc_congelada` aborta cualquier UPDATE posterior.
- Se calculan dos huellas:
  - `sha256_contenido`: hash del contenido canónico, que incluye los hashes de los anexos.
    **Es la huella que se imprime**, porque el SHA del HTML no puede ir dentro del mismo
    HTML.
  - `sha256_html`: hash del HTML emitido. También queda en el evento EMITIDA.
- **Reimprimir y enviar verifican** el HTML guardado contra el hash del evento
  (`_html_verificado`). Si no coincide, se bloquea.
- Las NC emitidas **no se borran** (`nc_no_delete`). Solo se borran borradores.
- Los plazos (`recepcion_ts`, contención, descargo) solo se escriben en las transiciones
  permitidas: al enviar, o al observar el descargo. Hay triggers para cada caso.

### 6.4 Registro append-only y cadena de hashes
- `nc_eventos`, `nc_respuesta`, `nc_acciones_correctivas`, `nc_verificacion`,
  `nc_adjuntos`, `observaciones` y `nc_envios` son **de solo agregado**: los triggers
  abortan cualquier UPDATE o DELETE.
- Cada evento guarda `hash = sha256(hash_prev + evento_canónico)`. Eso forma una
  **cadena** donde alterar un evento rompe todos los siguientes.
  - `verificar_cadena(proyecto)` recorre la cadena y lista las inconsistencias.
  - `ancla(proyecto)` devuelve el último hash y la cantidad de eventos, para anotarlo
    afuera como testigo.
  - `verificar_html_emitidos`, `verificar_anexos` y `verificar_adjuntos` comprueban los
    hashes de los artefactos.
- Los adjuntos (PDF enviado, evidencias) se guardan en disco con su sha256.

### 6.5 Anexos con datos de IQMerma
- Anexo `serie_stock`: la serie diaria de los códigos en la sucursal, con inventarios y
  ajustes marcados.
- Anexo `merma`: la merma de la sucursal en el período, con los peores SKU. Su total sirve
  como línea base.
- Se generan en BORRADOR y se guardan **tal cual**: HTML, parámetros, fecha de corte de los
  datos y hash. **Al emitir quedan congelados**, con triggers que bloquean insertar,
  modificar o borrar. **Una reingesta del ERP no cambia los números de una NC emitida.** Lo
  que se vio en la vista previa es lo que se emite.

### 6.6 Roles y separación de funciones
| Rol | Permite |
|---|---|
| `nc_emitir` | crear/emitir/enviar, observar o aceptar descargos, cargar el SOP, observaciones, contactos |
| `nc_responder` | transcribir el descargo de la sucursal |
| `nc_verificar` | verificar la eficacia y cerrar |
| `nc_anular` | anular una NC ya notificada |

- Los permisos viajan en un `Actor` (username, nombre, roles), y **cada función de core
  los exige** (`_exigir`). Que la UI muestre un botón no alcanza para operar.
- Con `verificador_distinto=True`, que es el valor por defecto, **quien verifica la
  eficacia no puede ser quien cargó el descargo**.
- Anular una NC EMITIDA lo puede hacer `nc_emitir` o `nc_anular`. Después de ENVIADA hace
  falta `nc_anular` y **registrar cómo se le avisó a la sucursal**. El motivo es siempre
  obligatorio. Se puede reemitir como borrador nuevo, y el borrador guarda `reemplaza_a`.
- En admin el actor tiene todos los roles. En la instancia cliente tiene los `roles_nc` del
  YAML. Sin roles, el usuario ve la página en solo lectura.

### 6.7 SOP como fuente de criterios
- `core/sop.py` parsea el .docx del SOP sin python-docx. Reconstruye la numeración
  automática de Word a partir de los estilos de título e ignora el índice, que está
  desactualizado respecto del cuerpo.
- Cada versión entra como BORRADOR, se revisa y **al activarla queda congelada** (§7.5).
  Los triggers bloquean la edición de artículos y de los metadatos de una versión ACTIVA o
  REEMPLAZADA. La NC guarda el requisito citado y la versión del SOP.

### 6.8 Notificación por mail
- `core/nc_mail.py` usa el SMTP de Gmail desde `inventariopermanentesanjuan@gmail.com`.
- La contraseña de aplicación sale de la variable `MERMAIQ_SMTP_PASSWORD` o de
  `auth/smtp.json`, que **está fuera de git**. Nunca va en `nc_config.json`.
- Flujo:
  1. Componer el mail desde plantillas con marcadores, con Para/CC/CCO sugeridos por la
     agenda.
  2. Editar y previsualizar.
  3. Enviar. **El PDF se regenera desde el HTML congelado, verificando su hash.**
  4. **Solo si el servidor acepta** se registra el envío. EMITIDA pasa a ENVIADA y desde
     ahí corren los plazos: el SOP §6 declara oficial el correo de la organización.
  5. Cada intento, bueno o fallido, queda en `nc_envios` y en la cadena. El PDF enviado
     queda como adjunto con hash.
- Si las fechas cambian entre la redacción y el envío, se bloquea y pide "Regenerar texto".
- El mail lleva un pie fijo, "Enviado a través de la suite IQMerma", y pide acuse dentro de
  `acuse_horas` (48 h por defecto). El acuse **todavía no se registra**.
- La agenda de contactos (`core/nc_contactos.py`, tabla `contactos`) **no es evidencia**:
  se edita libremente. Lo que importa, a quién se mandó cada NC, queda en `nc_envios`.

### 6.9 Conservación (ISO §7.5.3)
- `backup(proyecto)` hace una copia consistente del SQLite (API de backup) y un zip con los
  adjuntos y los SOP, en `projects/<p>/nc_backups/`. Solo se hace desde admin.
- Tests: `tests/test_no_conformidades.py` (unittest, 47 tests) cubre el ciclo, los
  triggers, la cadena, los plazos y el mail simulado.

---

## 7. Seguridad general

- **Separación admin/cliente:**
  - la instancia expuesta a internet **no importa Ingesta** ni ninguna página de escritura
    sobre DuckDB;
  - abre DuckDB con `read_only=True`;
  - cada usuario ve solo sus `proyectos:` y `paginas:` (`core/auth.PAGINAS_DISPONIBLES` es
    la única fuente de verdad del catálogo).
  - La única escritura posible desde el cliente es sobre el SQLite de NC, controlada por
    roles (§6.6).
- **Login:** streamlit-authenticator con cookie (`mermaiq_cliente`, 30 días). Las
  contraseñas nuevas se guardan en texto plano solo hasta el primer login: ahí
  `auto_hash=True` las reemplaza por bcrypt. `core/auth.py` nunca hashea por su cuenta,
  para no pisar un hash. **Riesgo conocido:** un usuario creado que nunca entró queda con
  la contraseña en claro en `auth/usuarios.yaml`.
- **Túnel:** ngrok con dominio fijo (`elastic-obedient-trodden.ngrok-free.dev` → puerto
  8502), lanzado por `iniciar_cliente.cmd` desde el inicio de sesión de Windows, porque Task
  Scheduler está bloqueado por política.
- **Secretos fuera de git** (`.gitignore`): `projects/`, `*.duckdb`, `auth/usuarios.yaml`,
  `auth/smtp.json`, `exports/`. Solo se versiona `auth/usuarios.ejemplo.yaml`.
- **Datos del cliente:** viven solo en `projects/<p>/` en la máquina local y nunca van al
  repo.
- **SQLite de NC en disco local**, nunca en SMB.
- **PDF:** el navegador headless corre con un perfil temporal propio. El navegador se puede
  forzar con `MERMAIQ_NAVEGADOR`.
- **Reportes HTML:** no tienen JS ni recursos remotos (el logo va embebido), y el texto de
  los gráficos SVG se escapa siempre.

---

## 8. Entorno y operación

- **Máquina:** Windows 11 corporativo por RDP.
- **Device Guard bloquea los .exe sin firma de AppData.** Por eso se corre siempre con
  `python -m streamlit run ui/app.py`, o con `iniciar.bat` / `iniciar_cliente.cmd`.
- **Lock de DuckDB:** no hay que correr la app admin durante una ingesta por script
  ("Conflicting lock is held").
- **Consola cp1252:** los scripts que imprimen ✓ o emoji necesitan
  `PYTHONIOENCODING=utf-8`.
- **Puertos:** 8501 admin, 8502 cliente. Las pruebas de UI van en 8503 o el que esté libre.
  No hay que matar un 8501 que ya esté corriendo sin preguntar.
- **`pandas==3.0.3` está fijado.** Antes de instalar un paquete se corre
  `pip install --dry-run`. `statsforecast`, `mlforecast`, `fugue` y `utilsforecast` lo
  bajan a 2.x; ya pasó y hubo que revertirlo. Con miles de series se prefiere un hand-roll
  en NumPy.
- **Tests:** `python -m unittest discover tests` (no hay pytest). Para la UI se usa
  `streamlit.testing.v1.AppTest`.
- **Nuevo proyecto:** `python setup_db.py --project <nombre>`. Ojo: `--reset` borra todo.
- **En el proyecto real, antes de usar NC:**
  1. Cargar y activar el SOP.
  2. Configurar `admin_nombre`, `provincia_por_deposito` y `feriados_extra`.
  3. Configurar el SMTP.
- **Costos de cómputo medidos** (sirven para decidir qué cachear o poner detrás de un
  botón):
  - forecast mensual: unos 119 s en rubro, ajustando todos los candidatos;
  - LightGBM pooled: unos 38 s;
  - Croston/TSB: unos 10 s;
  - evaluación: unos 100 s;
  - Prophet en rubro: unos 16 min (por eso está excluido).

---

## 9. Estado y pendientes

**Al 2026-10-05:**
- El trabajo pendiente quedó en **6 commits lógicos** en `visualizaciones`
  (`0b30f1b`…`bbaf0f9`). Todavía **no están pusheados**.
- **PRs abiertos sin mergear:**
  - #1, `fixes-v2` → main (refactor v2);
  - #2, `visualizaciones` → `fixes-v2`.
- Camino propuesto, pendiente de OK:
  1. Push.
  2. Mergear el PR #1 con merge commit (no squash).
  3. Retargetear el PR #2 a `main` y actualizar su descripción.
  4. **`/code-review` obligatorio** del PR #2.
  5. Mergear el PR #2 con merge commit.
- La branch de trabajo nueva es **`desarrollo-compliance`**. El roadmap está en
  ESTRATEGIA_COMPLIANCE §7. La Fase 1 es KPIs de NC + IRA + objetivos de calidad.
- `projects/cliente_x` es un duplicado de prueba; falta que el usuario confirme si se
  borra. `nombre_cliente` y `test` también son proyectos de prueba (a validar).

**No Conformidades:**
- [ ] Probar un envío de mail real.
- [ ] Registrar el acuse de recibo.
- [ ] WhatsApp: primero `wa.me`, después la API oficial.
- [ ] Recordatorios de vencimiento con una tarea programada (Task Scheduler bloqueado: buscar
      una alternativa).
- [ ] v2: evidencias automáticas, métrica Y, KPIs y reincidencia.

**Datos / cliente:**
- [ ] Validar los picos FB de 2025 (de 330 a 490 mil unidades en feb/dic): ¿canal
      mayorista? Hoy el forecast los trata como atípicos.
- [ ] Canal MELI con 161 % de merma y excluido del semanal: validar.
- [ ] Recargar pestañas parciales cuando cierre el mes (`forzar=True`).
- [ ] Selector de pestañas en la UI de ingesta.
- [ ] Agregar TR (Transformación) al `tipos_categoria` de `Yuriti_S.A` corriendo
      `python setup_db.py --project Yuriti_S.A`, sin la app abierta. Antes, confirmar con el cliente que TR cuenta como merma.

**Decisiones de producto abiertas:**
- [ ] ¿Reemplazar `estimar_demanda_stock_sku` por LightGBM pooled en
      continuo/errático/lumpy? El dato de la Fase 5 ya está.
- [ ] ¿Promover "Pedido general de distribución 2" y retirar la v1?

**Menores:** el warning de Streamlit "Could not serialize pd.DataFrame.attrs" en la
evaluación es cosmético.

---

## 10. Glosario

| Término | Significado |
|---|---|
| **Merma** | Pérdida de inventario no explicada: faltantes de inventario (INV) y ajustes (CS), más transformaciones (TR) según el catálogo, netos por SKU |
| **Snapshot** | La foto de stock por sucursal a una fecha (`stock_sucursal`), con costo y lista_1. Fuente de la valorización |
| **Depósito / sucursal** | `codigodepo` de 3 dígitos. `es_logistica` marca los centros de distribución, que no son sucursales de venta |
| **tipo / tipomov** | Subtipo y tipo de movimiento del ERP. Se categoriza por `tipo` |
| FA/FB/FCA | Facturas A/B/crédito A (venta) |
| NCA/NCB/NCCA | Notas de crédito (devolución, descuentan venta) |
| INV | Conteo de inventario físico (bajo TIPOMOV AJU) |
| CS | Ajuste de stock |
| MD | Diferencia de camión (REM) |
| RE/RI/RDC | Remitos (reposición/transferencia) |
| TR | Transformación (un SKU pasa a ser el SKU de liquidación) |
| **Cobertura** | Días de stock al ritmo de venta actual |
| **ABC / XYZ** | Clase por participación en la venta / por regularidad (CV) de la demanda |
| **ADI / CV²** | Intervalo medio entre ventas / variabilidad del tamaño de venta (segmentación de SKU) |
| **MAPE / WMAPE / MAE** | Error porcentual medio / error porcentual ponderado por volumen / error absoluto medio |
| **Backtest** | Se reserva el último tramo de la serie, se predice sin verlo y se mide el error |
| **NC** | No Conformidad (ISO 9001 §10.2) |
| **OBS** | Observación (SOP §8.1), un registro más liviano que una NC |
| **SOP / POE** | Procedimiento Operativo Estandarizado del cliente |
| **Descargo** | La respuesta de la sucursal: corrección, causa raíz (6M) y acciones correctivas |
| **Contención** | La acción inmediata para frenar el desvío (plazo según el tipo) |
| **Verificación de eficacia** | El control posterior de que la acción funcionó. Si no funcionó: CERRADA_NO_EFICAZ y NC nueva |

---

## 11. Mapa de documentos

| Documento | Para qué |
|---|---|
| **IQMERMA.md** (este) | El norte: qué es, qué hace, decisiones, seguridad, estado |
| [README.md](README.md) | Stack, estructura y setup rápido |
| [GUIA_USO.md](GUIA_USO.md) | Manual paso a paso: instalación, carga de datos, cómo leer cada página |
| [ESTRATEGIA_COMPLIANCE.md](ESTRATEGIA_COMPLIANCE.md) | Hacia dónde va: ADRs (solo lectura, conteo móvil aparte, no WMS), brecha ISO 9001, LSS, generalización, minería, roadmap. Sin datos del cliente |
| [MIGRACION_V2.md](MIGRACION_V2.md) | Histórico del refactor v1 → v2 y decisiones de negocio originales |
| Docstrings de `core/**` | El detalle técnico de cada motor (fórmulas, parámetros, casos medidos) |
| `tests/test_no_conformidades.py` | La especificación ejecutable del módulo NC |
| `requirements.txt` | Dependencias, con el porqué de cada pin |

---

## 12. Changelog del documento

| Fecha | Versión | Cambio |
|---|---|---|
| 2026-10-05 | 1.0 | Primera versión: funcionalidades, límites, decisiones de cálculo, seguridad y compliance de NC, entorno, estado |
| 2026-10-05 | 1.1 | Enlace a ESTRATEGIA_COMPLIANCE.md; límite conteo móvil / WMS; lectura real de las líneas INV (sin cantidad contada); estado de commits, PRs y branch |
