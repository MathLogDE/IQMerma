# IQMerma — Estrategia de compliance y expansión

> **Versión:** 1.0 · **Fecha:** 2026-10-05 · **Branch:** `desarrollo-compliance`
>
> Este documento **no contiene datos del cliente**: ni nombre, sucursales, cifras, cuentas o
> dominios. Se puede compartir. Al cliente actual se lo llama **cliente piloto (retail de
> bazar/decoración)**. Todo lo referido a minería es una hipótesis y lleva la marca
> **(a validar)**.
>
> Complementa a [IQMERMA.md](IQMERMA.md), que describe qué es IQMerma hoy. Este documento
> describe hacia dónde va y por qué.

---

## Índice
1. [Visión](#1-visión)
2. [Decisiones de arquitectura (ADR)](#2-decisiones-de-arquitectura-adr)
3. [Brecha ISO 9001:2015 por cláusula](#3-brecha-iso-90012015-por-cláusula)
4. [Lean Six Sigma](#4-lean-six-sigma)
5. [Qué está atado al cliente piloto y cómo generalizarlo](#5-qué-está-atado-al-cliente-piloto-y-cómo-generalizarlo)
6. [Minería (a validar)](#6-minería-a-validar)
7. [Roadmap](#7-roadmap)
8. [Riesgos y preguntas abiertas](#8-riesgos-y-preguntas-abiertas)
9. [Changelog](#9-changelog)

---

## 1. Visión

**Hoy:** IQMerma es un analizador de merma e inventario para un retail multi-sucursal. Lee
los exports del ERP, calcula merma, salud de stock, políticas, forecast, y gestiona No
Conformidades con trazabilidad de nivel auditoría.

**Hacia dónde:** **control de inventario y compliance como servicio**, independiente del
ERP. La propuesta de valor tiene tres partes:
1. **Medir** la exactitud y la pérdida de inventario con métricas defendibles ante un
   auditor: IRA, merma, cartas de control.
2. **Formalizar** los desvíos y su corrección según ISO 9001 §10.2, con evidencia inmutable.
3. **Mejorar** con Lean Six Sigma: causas raíz, reincidencia, proyectos DMAIC.

**Segmentos:**
| Segmento | Estado | Por qué encaja |
|---|---|---|
| Retail multi-sucursal | Piloto en producción | Merma alta, muchas bocas, conteos descentralizados |
| Proveedores mineros (distribuidores de repuestos e insumos, contratistas con almacén en faena) | **Hipótesis (a validar)** | Inventario de alto valor, trazabilidad exigida por el cliente minero, sitios remotos, auditorías frecuentes |

**Qué IQMerma no quiere ser:** ni un ERP ni un WMS. Es la capa de **control** que se apoya
sobre el sistema de registro que el cliente ya tiene (ver ADR-1 y ADR-3).

---

## 2. Decisiones de arquitectura (ADR)

### ADR-1 · IQMerma es de solo lectura sobre el inventario
- **Contexto.** IQMerma ingiere exports del ERP. Cualquier escritura sobre stock la
  convertiría en un sistema de registro, con la responsabilidad operativa y contable que eso
  implica.
- **Decisión.** El ERP o el WMS del cliente es **el** sistema de registro del inventario.
  IQMerma solo escribe **registros de compliance**: NC, observaciones, versiones de SOP,
  auditorías, planes de conteo. Los guarda en su propio store, separado de la base
  analítica. El precedente es el SQLite de NC, con WAL, append-only y cadena de hashes, que
  ya está en producción.
- **Consecuencias.**
  - Nunca hay conflicto de "dos verdades" sobre el stock.
  - Las correcciones se hacen siempre en el ERP, y IQMerma verifica que se hayan hecho.
  - La base analítica puede seguir siendo DuckDB, con lock exclusivo de escritura, porque
    solo la escribe la ingesta.
- **Se revisa si** un cliente no tiene ningún sistema de registro de inventario. En ese caso
  se recomienda uno existente, no se construye (ver ADR-3).

### ADR-2 · El conteo móvil es una app separada, offline-first, con contrato de datos
- **Contexto.** Hoy los conteos los registra el ERP del cliente e IQMerma ve solo el ajuste
  resultante. Una app de conteo propia daría el dato crudo, que es lo que permite una IRA
  correcta (ver §4.1), conteo ciego y evidencia del conteo. Sin embargo, la UI actual
  (Streamlit) no sirve para esto:
  - cada interacción recarga la página;
  - no funciona sin conexión;
  - escanear códigos con la cámara es incómodo;
  - las escrituras concurrentes de muchos contadores chocan con el lock de la base analítica;
  - los sitios remotos tienen conectividad intermitente (relevante para minería, a validar).
- **Decisión.** Una **app separada**, offline-first, que se comunica con IQMerma solo a
  través de un **contrato de datos bidireccional**:

  | Dirección | Contenido |
  |---|---|
  | IQMerma → app: **plan de conteo** | Ciclo, SKUs y ubicaciones a contar (programa cíclico ABC, §4.6), conteo **ciego** (sin mostrar el stock teórico), contadores asignados, tolerancias |
  | app → IQMerma: **resultados** | SKU, depósito, ubicación (opcional), **cantidad contada**, usuario, dispositivo, timestamp, número de recuento, foto o nota |

- **Los resultados son una fuente nueva, "conteo", distinta de `INV`.** `INV` es el
  **ajuste** que el ERP registra después de un conteo. No es el conteo.
- **Control nuevo, la conciliación conteo ↔ ajuste.** Cada conteo de la app tiene que
  terminar en un ajuste `INV` coherente en el ERP: el mismo SKU y depósito, en una ventana de
  fechas, con una cantidad igual a contado − teórico. Hay tres tipos de discrepancia:
  - conteo sin ajuste: el ERP no se corrigió;
  - ajuste sin conteo: un ajuste sin respaldo físico, que es una señal de fraude o de error;
  - cantidades distintas: el ajuste no coincide con lo contado.

  Cada discrepancia genera una observación o una NC sugerida. Este control es en sí mismo
  evidencia para ISO 9001 §8.5.1 y §9.1.
- **La IRA se calcula sobre la fuente "conteo"**, no sobre los ajustes.
- **Stack: decisión diferida.** La opción de referencia es una PWA (almacenamiento local en
  IndexedDB y escaneo por cámara) más un backend liviano con su propia base transaccional. No
  se elige hasta entrar en la Fase 4.
- **Consecuencias.**
  - IQMerma no cambia de naturaleza: sigue analítica y de solo lectura (ADR-1).
  - La app puede venderse o desplegarse aparte.
  - El contrato tiene que versionarse.
- **Se revisa si** el ERP del cliente ya trae una app de conteo que exporta el dato crudo. En
  ese caso se integra con ella y no se construye.

### ADR-3 · No construimos un WMS
- **Contexto.** Tener ubicaciones y conteo móvil acerca la idea de un WMS: recepción,
  putaway, picking, ubicaciones, lotes, series, despacho.
- **Decisión.** **No.** Motivos:
  - Es un mercado maduro, con opciones comerciales y de código abierto.
  - Un WMS es sistema de registro, con responsabilidad operativa directa: si falla, el
    almacén no despacha.
  - El alcance es varias veces el de IQMerma entera.
  - Contradice ADR-1: dejaríamos de ser el control independiente sobre el sistema de registro.
- **Consecuencias.** Lo que sí se suma, cuando haga falta, es la **lectura** de ubicación,
  lote y serie en el esquema canónico de ingesta (§5.1), para medir y trazar sin operar.
- **Se revisa solo si** varios clientes no tienen ningún control de ubicaciones **y** la app
  de conteo demuestra demanda concreta. Aun así, primero se evalúa **integrar** un WMS
  existente.

---

## 3. Brecha ISO 9001:2015 por cláusula

La columna **¿Le toca a IQMerma?** evita forzar mapeos. Muchas cláusulas son
responsabilidad de la organización y no del software. Ahí IQMerma, como mucho, aporta un
dato o un registro.

| Cláusula | ¿Le toca a IQMerma? | Qué hay hoy | Qué falta | Propuesta |
|---|---|---|---|---|
| **4.1–4.3** Contexto, partes interesadas, alcance | **No** | — | — | La organización lo define. IQMerma no tiene nada que agregar |
| **4.4** Procesos del SGC | **Parcial** | El SOP versionado describe los procesos de inventario | Mapa de procesos con sus indicadores | Vincular cada artículo del SOP a los KPIs que lo miden (§4) |
| **5** Liderazgo, política | **No** | — | — | Responsabilidad de la dirección. IQMerma solo alimenta la revisión (9.3) |
| **6.1** Riesgos y oportunidades | **Parcial** | Salud de stock (quiebres, sobrestock), control de merma (outliers) y calidad de datos ya detectan riesgos operativos | Registro de riesgos con probabilidad, impacto, dueño y tratamiento | Registro simple de riesgos de inventario por sucursal/proceso, que se alimenta de las señales existentes. La metodología la define la organización |
| **6.2** Objetivos de calidad | **Sí** | Métricas sueltas (merma %, avance de inventario) sin meta | Objetivos medibles con meta, responsable, plazo y seguimiento | Objetivos por sucursal (merma %, IRA, NC vencidas) con semáforo contra la meta. **Fase 1** |
| **7.1.5** Recursos de seguimiento y medición | **Parcial** | Calidad de datos (gaps de ingesta), el cálculo trazable en los docstrings y los anexos congelados con fecha de corte | Validación formal de cada métrica (qué mide, fuente, limitaciones) | Ficha técnica por KPI. Ver el sesgo de IRA en §4.1 como primer caso |
| **7.2** Competencia | **Parcial** | La causa 6M "Mano de obra" de las NC | Registro de capacitación de los contadores | Fuera del núcleo. Como mucho, cruzar la reincidencia con la capacitación declarada. La gestión de RR.HH. es de la organización |
| **7.5** Información documentada | **Sí** | El SOP versionado y congelado al activarse; NC emitidas inmutables (sha256 + triggers); cadena de hashes de eventos; backup (7.5.3) | Política de retención y registro de lectura o toma de conocimiento del SOP | Acuse de lectura del SOP por usuario cuando cambia de versión; retención configurable |
| **8.4** Control de proveedores externos | **Parcial** | Diferencias de camión: faltantes y sobrantes al recibir | Evaluación de proveedores (externos y CD propio) con KPI de cumplimiento | Ranking de diferencias por origen como insumo. La evaluación la hace la organización |
| **8.5.1** Control de la producción y la prestación del servicio | **Sí** (proceso de conteo) | Avance de inventario y el SOP como criterio | Evidencia del método de conteo (ciego, recuento) | Conciliación conteo ↔ ajuste (ADR-2), Fase 4. Antes, el programa de conteo cíclico (§4.6) |
| **8.5.2** Identificación y trazabilidad | **Parcial** | Trazabilidad por SKU, depósito y comprobante | Lote, serie, vencimiento | Sumar lote y serie al esquema canónico (§5.1) solo para **leer**. Clave para minería (a validar) |
| **8.7** Control de salidas no conformes | **No / parcial** | Los ajustes (CS) y las transformaciones registran mercadería dañada o liquidada | — | Es del proceso operativo. IQMerma solo lo mide |
| **9.1** Seguimiento, medición, análisis | **Sí** | Análisis de merma, control, salud, Pareto, forecast | KPIs formales, SPC, IRA | IRA (con su validación, §4.1), cartas de control (§4.2), KPIs de NC. **Fases 1–2** |
| **9.2** Auditoría interna | **Parcial** | Las observaciones (OBS) y las NC son los hallazgos | Programa de auditoría (qué, dónde, cuándo, quién), checklist por artículo del SOP | Programa anual por sucursal × artículo del SOP. Los hallazgos se cargan como OBS o NC. **Fase 2** |
| **9.3** Revisión por la dirección | **Parcial** | Los datos existen dispersos | Reporte con las entradas de 9.3.2 | Reporte imprimible que junta objetivos, NC, auditorías, desempeño de proveedores y riesgos. La revisión en sí la hace la dirección. **Fase 2** |
| **10.2** No conformidad y acción correctiva | **Sí** | Ciclo completo: emisión con número y hash, descargo con causa raíz 6M, verificación de eficacia por otra persona, cierre o NC nueva, anulación con motivo | Acuse de recibo registrado, recordatorios de vencimiento, reincidencia, eficacia medida con métrica Y | Fase 1: acuse y recordatorios. Fase 2: reincidencia → DMAIC (§4.5) |
| **10.3** Mejora continua | **Parcial** | — | Proyectos de mejora con seguimiento | Charter DMAIC generado desde la reincidencia (§4.5) |

---

## 4. Lean Six Sigma

### 4.1 IRA (Inventory Record Accuracy)
**Definición:** el porcentaje de SKU-ubicaciones contados cuyo registro coincide con el
físico dentro de una tolerancia. La tolerancia se fija por clase ABC; por ejemplo, A más
estricta que C.

**Verificación en los datos del piloto (2026-10-05)**, antes de comprometer el KPI:
- **El ERP sí genera líneas `INV` cuando el conteo coincide** (`diferencia = 0`) y son la
  gran mayoría de las líneas de inventario. Por lo tanto **el denominador está completo**:
  se ven los aciertos, no solo los errores. La IRA por presencia (línea con diferencia 0 =
  acierto) **no está sesgada a la baja** por ese motivo.
- **Pero la línea `INV` no trae la cantidad contada ni la teórica.** En las líneas con
  diferencia 0, `ingreso` y `egreso` valen los dos 0, y ninguna línea tiene los dos valores
  positivos a la vez. `ingreso`/`egreso` son solo el **delta del ajuste**, no
  "contado/teórico".

**Consecuencias:**
1. **Hoy se puede calcular** una IRA con **tolerancia en unidades absolutas**
   (`|diferencia| ≤ k`), incluida la exacta (k = 0).
2. **No se puede calcular** una IRA con **tolerancia porcentual**, porque requiere el stock
   teórico al momento del conteo. Se podría aproximar con la reconstrucción diaria de stock,
   pero esa estimación depende de que los movimientos estén completos. Si se usa, va
   marcada como aproximación.
3. **Supuesto a validar con el cliente:** que cada línea con diferencia 0 corresponda a un
   SKU **efectivamente contado**, y no a un cierre masivo del ERP que marca todo el catálogo.
4. La IRA "de verdad", con cantidad contada, conteo ciego y recuento, llega con la fuente
   "conteo" (ADR-2).
5. **Issue derivado:** `core/merma/avance.py` asume que en las líneas INV `ingreso` es la
   cantidad contada y `egreso` el stock teórico. Los datos lo contradicen: "unidades
   contadas = SUM(ingreso)" en realidad suma solo los sobrantes ajustados. Ver la lista de
   issues en §5.4.

### 4.2 Control estadístico de procesos (SPC)
| Carta | Variable | Subgrupo | Para qué |
|---|---|---|---|
| **I-MR** (individuos y rango móvil) | Merma % **mensual** por sucursal | Un punto por mes | Separar la variación normal de un cambio real (causa especial) |
| **p** (proporción de defectuosos) | Defectos de IRA (1 − IRA) | **Un punto por ciclo de conteo**, n = SKUs contados en el ciclo | Detectar si la exactitud empeora más allá del ruido muestral |

- **Nada semanal sobre merma:** los inventarios no son semanales y la merma semanal es
  puro ruido (picos de ajuste).
- **Reglas de Nelson** para detectar señales: un punto fuera de 3σ, 8 seguidos del mismo
  lado de la media, y otras. Una señal **sugiere** una OBS o una NC. Nunca la emite sola,
  porque decide una persona.
- Hacen falta al menos unos 20 puntos para estimar los límites. Con historia mensual, hay
  que avisar en la UI mientras no alcancen.

### 4.3 DPMO y nivel sigma del conteo
- Defecto = línea de conteo fuera de tolerancia. Oportunidad = línea contada.
- `DPMO = defectos / oportunidades × 10⁶`. El nivel sigma sale de la tabla estándar.
- Sirve como lenguaje común con clientes que ya trabajan en LSS. No reemplaza a la IRA.

### 4.4 Pareto de causas raíz (6M)
Ya existe el dato: cada descargo de NC exige una categoría 6M (Método, Mano de obra,
Material, Máquina, Medición, Medio). Falta el Pareto por sucursal y período, cruzado con el
requisito del SOP incumplido.

### 4.5 DMAIC desde la reincidencia
- **Regla de reincidencia** (parámetros configurables): la misma sucursal y el mismo
  requisito del SOP, en N NC durante X meses.
- Al dispararse, se propone un **charter DMAIC** prellenado:
  - **Define:** el problema, con la sucursal, el requisito y las NC vinculadas.
  - **Measure:** la línea base. Sale del **anexo de merma** que ya se congela en cada NC.
  - **Métrica Y:** el KPI que se usará para verificar la eficacia (merma %, IRA, NC
    vencidas).
  - **Analyze:** el Pareto 6M de esas NC.
  - **Improve / Control:** quedan como seguimiento. La verificación de eficacia usa la
    métrica Y.

### 4.6 Programa de conteo cíclico ABC
- La frecuencia de conteo se fija por clase. Por ejemplo, A mensual, B trimestral y C
  semestral; los valores los define la organización.
- IQMerma ya calcula la clase ABC (políticas de stock) y el avance de inventario. Falta el
  **programa**: qué contar en cada ciclo y el seguimiento de cumplimiento.
- Ese programa es, a futuro, el **plan de conteo** que IQMerma le manda a la app (ADR-2).

---

## 5. Qué está atado al cliente piloto y cómo generalizarlo

### 5.1 Ingesta → adaptadores y esquema canónico
**Hoy:** la ingesta asume el formato particular del Excel del ERP del piloto:
- un archivo por año con una pestaña por mes (`MM-AA`);
- la corrección de fechas que Excel invirtió (día/mes) al abrirlas con configuración
  regional de EE.UU.;
- las columnas `TIPOMOV`/`TIPO` del ERP;
- el stock pivotado con una columna por abreviatura de sucursal.

**Propuesta:** una capa de **adaptadores**, `ingesta/adaptadores/<erp>.py`, que traduzca cada
formato a un **esquema canónico**:

| Entidad | Campos canónicos | Futuro (solo lectura) |
|---|---|---|
| Movimiento | fecha, sku, depósito, **tipo canónico**, ingreso, egreso, comprobante, usuario, motivo | ubicación, lote, serie, unidad de medida |
| Snapshot de stock | fecha, sku, depósito, cantidad, costo, precio | ubicación, lote |
| Conteo (ADR-2) | ciclo, sku, depósito, cantidad contada, usuario, dispositivo, timestamp, recuento | ubicación, lote, serie |

- **Tipos canónicos propuestos:** VENTA, DEVOLUCIÓN, CONTEO_AJUSTE, AJUSTE,
  TRANSFERENCIA, RECEPCIÓN, CONSUMO, TRANSFORMACIÓN.
- La tabla `tipos_categoria`, que ya es configurable, mapea los subtipos del ERP a los
  tipos canónicos.
- Las particularidades del piloto (fechas invertidas, ceros a la izquierda en los SKU, el
  inventario registrado como ajuste) quedan **dentro de su adaptador**.

### 5.2 Denominador de merma configurable
**Hoy:** `merma % = merma valorizada / venta valorizada`. La venta sale de las categorías
marcadas `es_venta` (`core/merma/analisis.py`).

**Problema:** un almacén minero o de MRO **no vende desde el almacén**: despacha consumos
internos o a faena. Ahí la venta no existe o no representa la actividad (a validar).

**Propuesta:** el denominador se configura por proyecto:

| Denominador | Fórmula | Para |
|---|---|---|
| Ventas (actual) | merma / venta valorizada | Retail |
| Salidas o consumos | merma / salidas valorizadas | Distribución, almacén de consumos |
| Valor de inventario promedio | merma / stock valorizado promedio del período | MRO, repuestos (estándar de shrinkage sobre inventario) |

El flag `es_venta` se generaliza a un **rol "denominador"** en `tipos_categoria`. Para MRO,
la merma sobre el valor de inventario se lee **junto con** la IRA.

### 5.3 Valorización
**Hoy:** costo o precio de lista global por SKU, tomado del snapshot de stock.
**A evaluar (a validar):** costo promedio ponderado por almacén, multimoneda (por ejemplo
USD en minería) y costo por lote.

### 5.4 Issues de valores fijos en el código
Formato listo para pasar a GitHub Issues.

1. **Centros de distribución fijos.**
   - Módulo: `core/comun.py` (`DEPOSITOS_DISTRIBUCION`).
   - Problema: lista fija de códigos de depósito del piloto.
   - Propuesta: derivarla del flag `es_logistica` de `depositos`, que ya existe.
2. **Remitente de mail por defecto.**
   - Módulo: `core/nc_mail.py` (`MAIL_DEFAULT`).
   - Problema: trae la cuenta del piloto como valor de fábrica.
   - Propuesta: dejarlo vacío por defecto y exigir configurarlo por proyecto.
3. **País fijo en el calendario de las NC.**
   - Módulo: `core/no_conformidades.py` (`feriados`).
   - Problema: `"AR"` está escrito en el código.
   - Propuesta: país configurable por proyecto, con las subdivisiones por depósito como hoy.
4. **Eventos comerciales argentinos en el forecast semanal.**
   - Módulo: `core/forecasting/semanal.py`.
   - Problema: Día de la Madre y Día del Niño (fechas de Argentina) están fijos; para
     minería son irrelevantes.
   - Propuesta: lista de eventos por proyecto y país.
5. **Número de NC con depósito de 3 dígitos.**
   - Módulo: `core/no_conformidades.py` (formato `NNDDDMMAAAA`).
   - Problema: asume códigos de depósito de 3 dígitos.
   - Propuesta: formato de numeración configurable, sin romper los números ya emitidos.
6. **Parser del SOP atado a Word.**
   - Módulo: `core/sop.py`.
   - Problema: reconstruye la numeración automática de los títulos de Word.
   - Propuesta: mantenerlo y sumar una carga alternativa (artículos en planilla) para SOP
     en otros formatos.
7. **Lectura de INV en el avance de inventario.**
   - Módulo: `core/merma/avance.py` (`avance_conteo`).
   - Problema: asume `ingreso` = contado y `egreso` = teórico, lo que los datos contradicen
     (§4.1).
   - Propuesta: renombrar las métricas a lo que realmente miden (sobrantes y faltantes
     ajustados) y documentarlo.
8. **Catálogo de subtipos del ERP del piloto.**
   - Módulo: `setup_db.py` (`DEFAULT_TIPOS_CATEGORIA`).
   - Problema: el catálogo por defecto es el del ERP del piloto.
   - Propuesta: plantillas de catálogo por ERP, una por adaptador.

### 5.5 Lo que ya es configurable (se reutiliza)
- `tipos_categoria`: mapeo de subtipos a categorías, merma y venta.
- `CONFIG_DEFAULT` de NC: tipos, severidades, plazos, áreas, impactos y plantillas.
- Branding por proyecto: logo, color, datos y pie de los reportes.
- Roles de NC y permisos de páginas por usuario.

### 5.6 Despliegue
**Hoy:** corre en una PC Windows corporativa, y la instancia de clientes se expone por un
túnel de desarrollo. Eso **no pasa** el cuestionario de seguridad de una empresa B2B, y
menos el de una minera (a validar). El hosting propio es **condición para la Fase 4** (§7).
También es una decisión pendiente: dónde, con qué aislamiento entre clientes y cómo se hacen
los backups.

---

## 6. Minería (a validar)

### 6.1 Hipótesis de valor
- **Trazabilidad por lote y serie:** repuestos críticos, componentes con número de serie y
  certificados.
- **Repuestos críticos:** un quiebre detiene equipos. Pesa más la exactitud que la merma.
- **Sitios remotos con conectividad intermitente:** justifica que la app de conteo sea
  offline-first (ADR-2).
- **Consignación en faena:** stock del proveedor en el almacén del cliente minero, que hay
  que conciliar.
- **Auditorías del cliente minero:** el proveedor tiene que demostrar control, lo que encaja
  con NC, IRA y evidencia inmutable.
- **ERP tipo SAP** en el cliente minero, y ERP variados en los proveedores.
- **Normas además de ISO 9001:** posiblemente ISO 14001 y 45001, y requisitos contractuales
  propios.

### 6.2 Criterio de validación
Las entrevistas **corren en paralelo desde la Fase 1** y no esperan a la generalización.
- **Muestra:** 5 proveedores mineros.
- **Se considera validado si** al menos **3 de 5** confirman el dolor (diferencias o
  trazabilidad, conteo en sitios remotos o presión de auditoría) **y** al menos **1** acepta
  un piloto.
- Si no se cumple, minería queda archivada y el foco sigue en retail y distribución.

### 6.3 Guía de preguntas para las entrevistas
**Operación**
- ¿Cuántos almacenes o sitios manejan? ¿Cuántos están en faena o en zonas remotas?
- ¿Cuántos SKUs activos? ¿Qué parte es crítica (si falta, para un equipo)?
- ¿Qué conectividad hay en cada sitio?

**Sistemas**
- ¿Qué ERP y qué WMS usan, si tienen? ¿Y el cliente minero?
- ¿Pueden exportar movimientos y stock? ¿En qué formato y con qué frecuencia?
- ¿Manejan ubicaciones (bins) en el sistema?

**Conteo hoy**
- ¿Cómo cuentan: en papel, con colectora, con planilla? ¿Cada cuánto? ¿Quién cuenta?
- ¿El conteo es ciego? ¿Hacen recuento cuando hay diferencia?
- ¿Cómo concilian el conteo con el sistema? ¿Quién aprueba los ajustes?

**Trazabilidad**
- ¿Manejan lotes, series o vencimientos? ¿Les exigen certificados por pieza?
- ¿Tienen stock en consignación? ¿Cómo lo concilian con el cliente?

**Compliance**
- ¿Qué certificaciones tienen (9001, 14001, 45001)? ¿Quién los audita y cada cuánto?
- ¿Qué KPIs de inventario les exige el contrato con la minera?
- ¿Cómo registran hoy las no conformidades y las acciones correctivas?

**Dolores**
- ¿Cuál es su mayor problema de inventario: diferencias, quiebres de críticos,
  obsolescencia, consignación?
- ¿Cuánto les cuesta, en plata o en penalidades contractuales?

**Seguridad y despliegue**
- ¿Aceptan una solución en la nube o tiene que ser on-premise?
- ¿Qué requisitos de TI y de seguridad tienen? ¿Hay cuestionario?

**Comercial**
- ¿Quién decide la compra? ¿Hay presupuesto asignado a este problema?
- ¿Estarían dispuestos a un piloto acotado (un sitio, 2–3 meses)?

---

## 7. Roadmap

| Fase | Contenido | Criterio de salida |
|---|---|---|
| **0 · Cierre** | Commits lógicos del trabajo pendiente, `/code-review` y merge de los PRs, branch `desarrollo-compliance`, este documento | PRs mergeados a `main`, branch creada, documento aprobado |
| **1 · Piloto retail: medir** | **KPIs de NC**: abiertas, vencidas, tiempo de ciclo, % cerradas eficaces, reincidencia por sucursal y requisito, Pareto 6M. **IRA** por sucursal y clase con tolerancia absoluta (§4.1). **Objetivos de calidad** con meta (6.2 / 9.1). **Pendientes de NC**: acuse registrado y recordatorios | KPIs visibles en la app y en un reporte imprimible. Supuesto de IRA validado con el cliente. Primer mail de NC real enviado |
| **2 · Piloto retail: controlar y mejorar** | Carta **I-MR** de merma mensual y carta **p** de IRA por ciclo. Reincidencia → **charter DMAIC**. **Programa de auditoría interna** (9.2) con OBS. **Reporte de revisión por la dirección** (9.3). Programa de conteo cíclico ABC | Una revisión por la dirección hecha con el reporte de IQMerma |
| **3 · Generalización** | Adaptadores y esquema canónico (§5.1), denominador configurable (§5.2), issues de valores fijos (§5.4) | Un segundo proyecto cargado con otro formato sin tocar `core/`. **Es requisito para cualquier segundo cliente** |
| **En paralelo desde la Fase 1** | Entrevistas con proveedores mineros (§6) | Criterio de §6.2 cumplido o descartado |
| **4 · Conteo móvil** | Contrato de conteo versionado, MVP de la app offline, fuente "conteo", **conciliación conteo ↔ ajuste**, lote y serie en el esquema canónico | Un ciclo de conteo completo hecho con la app y conciliado |

**Condiciones para entrar a la Fase 4 (se necesitan las dos):**
1. Minería validada con el criterio de §6.2, o un caso equivalente en retail que pida conteo
   móvil.
2. **Hosting propio**, fuera de la PC corporativa y del túnel de desarrollo (§5.6).

---

## 8. Riesgos y preguntas abiertas

| Riesgo / pregunta | Impacto | Mitigación |
|---|---|---|
| Que las líneas INV con diferencia 0 no sean SKUs realmente contados | IRA inflada | Validarlo con el cliente piloto antes de publicar el KPI (§4.1) |
| Pocos puntos mensuales para las cartas de control | Límites inestables | Avisar en la UI y recalcular los límites a medida que crece la historia |
| Se dispersa el foco entre retail, minería y app móvil | Nada se termina | Fases con criterio de salida. La Fase 4 tiene condiciones explícitas |
| Infraestructura actual (PC corporativa, túnel) | Bloquea ventas B2B | Hosting propio antes de la Fase 4 (§5.6) |
| Dependencia del formato de export del ERP | Cada cliente nuevo cuesta caro | Fase 3: adaptadores y esquema canónico |
| Que la escala de NC del SOP del piloto difiera de la configurada | Inconsistencia documental | Ya es configurable (`CONFIG_DEFAULT`). Documentar la decisión por cliente |
| Normas mineras más allá de 9001 | Alcance desconocido | Preguntarlo en las entrevistas (§6.3) antes de diseñar nada |

**Preguntas abiertas:**
- ¿El cliente piloto quiere objetivos de calidad formales por sucursal? ¿Quién fija las
  metas?
- ¿Qué tolerancias de IRA por clase acepta el piloto?
- ¿Hosting en la nube pública, en un VPS o en un servidor del cliente?

---

## 9. Changelog

| Fecha | Versión | Cambio |
|---|---|---|
| 2026-10-05 | 1.0 | Primera versión: visión, ADR-1/2/3, brecha ISO 9001, LSS (con la verificación de IRA en datos), generalización, minería, roadmap |
