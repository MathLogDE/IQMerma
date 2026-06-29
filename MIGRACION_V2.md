# Migración v1 → v2 (análisis por selección)

Estado del refactor del schema y plan de remediación. v2 redefine el modelo:
el inventario físico deja de ser una tabla propia y el análisis deja de
anclarse en un "evento de conteo".

## Decisiones de negocio tomadas

1. **Anclaje del análisis:** por **selección de período** (sucursal + rango de
   fechas). Desaparece `fecha_conteo` como ancla y la ventana-por-SKU.
2. **Inventario:** entra como `movimientos` con `tipomov='INV'`. La merma de
   inventario = `ABS(diferencia)` cuando `diferencia < 0`. No se reportan
   `stock_sistema`/`stock_real`. Si hay varios inventarios de un SKU en el
   rango, se **suman** y recién ahí se toma el signo.
3. **Valorización:** siempre desde `stock_sucursal` (snapshot as-of `<= fecha_hasta`),
   **nunca** el costo del movimiento (los subtipos son inconsistentes: CS/INV/MD
   traen 0, NCA/FA/FB/NCB traen precio de venta, RI/RE/NCR traen costo). Dos modos
   seleccionables: `costo` y `lista_1`.
4. **Vista pivot por categoría:** por SKU, una columna `$` por categoría de
   movimiento (suma **neta con signo**, valorizada). Las categorías agrupan
   subtipos (`tipo`) del ERP vía la tabla configurable `tipos_categoria`.
   Catálogo definitivo (merma = pérdida no explicada):
   - Ventas (no merma) = FA, FB, NCA, NCB, FCA, NCCA
   - Inventario (**merma**) = INV
   - Dif. de camión (**merma**) = MD
   - Ajustes (**merma**) = CS
   - Remitido (no merma) = RE, RI, RDC
   - Subtipos sin mapear → "(sin categoría)", no suman a merma.
5. **Universo de SKUs:** `DISTINCT codigo` de `movimientos` ∪ `ventas` en el
   rango/sucursal (incluye SKUs con solo ventas).
6. **Denominador (% merma):** por ahora `venta_neta` del archivo `ventas`
   (enchufable a futuro hacia los movimientos VTA).

## Preguntas abiertas (bloquean parte del trabajo)

- [x] **Catálogo `tipo → categoría`**: definido y cargado (ver decisión 4).
      Mapeo por `tipo` solo (`tipomov` queda informativo).
- [ ] ¿El export de `movimientos` ya trae filas `INV`, o el inventario siempre
      viene en un archivo de conteo separado? (define si `conteos.py` se
      reescribe o se elimina).
- [ ] ¿De dónde sale `es_logistica`? (columna en archivo de depósitos vs. config manual).

## Plan de remediación

### Hecho
- [x] **Consolidar `setup_db.py`**: borrados `core/setup_db.py` (duplicado) e
      `ingesta/setup_db.py` (v1 obsoleto). Único canónico: `setup_db.py` (raíz).
- [x] **`movimientos.py`**: eliminado el paso `costo_sku_historial` (tabla que v2
      ya no crea). Carga `movimientos` sin cambios adicionales.
- [x] **Limpieza de basura**: borrados `core/test_parity.py`, `agregar_stock.py`,
      `mv ver.txt`.
- [x] **Tabla `tipos_categoria`**: agregada al schema + sembrado de catálogo parcial
      por defecto (idempotente, solo si está vacía). Editable sin tocar código.
- [x] **`core/merma.py`**: reescrito al motor pivot-por-categoría, data-driven desde
      `tipos_categoria`. Firma nueva:
      `calcular_merma(proyecto, codigodepo, fecha_desde, fecha_hasta, modo_valorizacion, criterio_ventas)`.
      Devuelve DataFrame ancho (una col $ por categoría) + `df.attrs["categorias"]`.
      Helper `listar_categorias(proyecto)`. **Verificado con smoke test** (modos costo
      y lista_1, universo con SKU solo-ventas, subtipo sin mapear aislado).

- [x] **`ui/app.py`**: reescrito a selección por rango de fechas + modo de
      valorización; render dinámico de columnas-categoría; sacadas las queries
      que leían `conteos` (Inicio ahora cuenta inventarios INV; selectores de
      sucursal/fecha desde movimientos+ventas). La opción de ingesta "conteo" se
      quitó (pendiente migrar `conteos.py`). API `use_container_width` →
      `width="stretch"`. **Verificado con `streamlit.testing.AppTest`** (las 5
      páginas renderizan sin excepción; Análisis calcula correctamente).

### Pendiente
- [ ] **`conteos.py`** (requiere respuesta): reescribir para cargar INV en
      `movimientos` (`diferencia = stock_real − stock_sistema`, `tipo='INV'`) **o**
      eliminar si el INV ya viene en el export de movimientos. Revisar dedupe.
- [ ] **`referencias.py`** (requiere respuesta): poblar `es_logistica` en
      `cargar_depositos`. Ojo: hoy el `INSERT OR REPLACE` resetea la columna a FALSE
      en cada recarga.
- [ ] Para aplicar el catálogo nuevo a una DB ya existente: el seed solo corre
      si `tipos_categoria` está vacía. En un proyecto ya creado, recargar con
      `python setup_db.py --project <x> --reset` o re-sembrar la tabla a mano.

## Notas técnicas
- `ventas.py` y `stock.py` ya están alineados con v2 — no requieren cambios.
- Los módulos hacen `from setup_db import …` sin asegurar la raíz en `sys.path`;
  funcionan porque `ui/app.py` la inserta. Conviene que cada módulo la asegure
  por sí mismo (retoque menor) para no depender del llamador.
