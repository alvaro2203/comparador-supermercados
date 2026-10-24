# TODO

Lista revisada el 24 de octubre de 2026 tras auditar la base de datos (precios del 18 al 23 de
octubre), el volcado JSON del 23 de octubre y el código. Las tareas ya hechas (migraciones, EAN,
`report`, `dump`, versionado...) están en el historial de git y sus decisiones en el README.

Prioridades:

- **P0** puede perder datos o fallar sin avisar. Va antes que nada.
- **P1** bloquea el comparador o deja huecos en el histórico.
- **P2** mejora la calidad de los datos.
- **P3** funcionalidad nueva.

Estado de los datos el 23 de octubre: 4.274 productos activos, todos con ficha y `gtin`; 622
categorías; 4 días con precios (18, 20, 21 y 23); 0 errores en las 7 ejecuciones; etiquetas
(`Product.label`) únicas en todo el catálogo activo; 1 precio sin unidad de referencia (explicado
en P2).

---

## P0

No hay tareas P0 abiertas.

## P1 · Histórico continuo

- [ ] **Programar la ejecución diaria.**
  - **Situación:** faltan el 19 y el 22 de octubre. Cada día sin ejecutar es un hueco que no se
    puede recuperar.
  - **Plan:** GitHub Actions cuando el scraper esté pulido. Antes hay que resolver:
    - **Dónde vive la base de datos.** Cada ejecución de Actions empieza en una máquina vacía, y
      `data/supermarkets.db` no está en git. Opciones: una base de datos PostgreSQL externa
      (basta con cambiar `SCRAPER_DATABASE_URL`), subir y bajar el `.db` como artefacto o en una
      release, o un runner propio (tu PC). La caché de Actions no sirve: se borra a los 7 días sin
      uso.
    - **Si Mercadona responde a las IP de GitHub.** Los runners están en centros de datos de
      Azure, casi siempre fuera de España. Probarlo con un workflow manual que solo haga
      `run --dry-run` antes de confiar en él.
    - **Horario.** El `cron` de Actions va en UTC y puede retrasarse; en repos públicos sin
      actividad se desactiva a los 60 días. El día de los precios no se ve afectado, porque lo
      decide `SCRAPER_TIMEZONE`.
    - **El repositorio aún no tiene remoto.** Hay que publicarlo en GitHub primero, y así también
      empieza a correr el CI.
  - **Alternativa inmediata:** `scripts/register_windows_task.ps1` en tu PC, hasta tener Actions.
- [ ] **Copia de seguridad de la base de datos antes de cada ejecución** (antes en P3).
  - **Por qué sube de prioridad:** el histórico no se puede volver a descargar, y todo está en un
    único fichero. Además, todas las tablas cuelgan de `supermarkets` con `ON DELETE CASCADE`: un
    `DELETE` a mano de esa fila borraría todo el histórico de la cadena.
  - **Hacer:** copia rotada (como los logs) en el runner diario, con la API de copia de SQLite
    (`sqlite3 .backup` o `VACUUM INTO`), no copiando el fichero mientras se escribe. Si se elige
    PostgreSQL externo, sus copias sustituyen a esta tarea.
- [ ] **Guardar el JSON en bruto en la ejecución diaria.**
  - **Por qué:** con el JSON guardado, el fallo de los precios «por 100 g» se habría corregido
    reprocesando los días afectados con `run --from-raw`. Sin él, esos días solo se pueden arreglar
    a medias (ver P2).
  - **Hacer:** que `scripts/run_daily.ps1`, `run_daily.sh` y el futuro workflow usen
    `run --save-raw`. Ocupa unos 5,5 MB al día (unos 2 GB al año), así que va junto con la tarea de
    comprimir y rotar de P3.

## P2 · Calidad de los datos

- [ ] **El IVA está vacío en el 73 % de los precios.**
  - **Situación:** el 23 de octubre, 3.120 de 4.275 precios tienen `vat_rate` NULL. El scraper lee
    `price_instructions.iva`, que Mercadona solo rellena en 1.155 productos. El campo
    `tax_percentage` («10.000», «21.000», «4.000») viene en todos, y coincide con `iva` siempre que
    los dos existen.
  - **Hacer:** leer `tax_percentage` (y `iva` solo como respaldo), con test y fixture con `iva`
    null. Mismo fallo que el de `is_new` frente a `is_new_arrival`.
  - **Histórico:** los precios antiguos se quedan con NULL, salvo que se decida rellenarlos con el
    IVA actual del producto (el IVA de los alimentos ha cambiado varias veces desde 2023, así que
    sería una suposición).
- [ ] **Recalcular los precios de referencia antiguos que se pueden comprobar.**
  - **Situación:** del 18 al 21 de octubre quedan 1.580 precios con `reference_unit = 'unknown'`
    (formatos «por 100 g» y «por 100 ml», antes del arreglo). En 1.424 de ellos, el valor guardado
    por 10 coincide exactamente con el `bulk_amount` (€/kg o €/l) que se guardó en la misma fila,
    así que el valor correcto es seguro, no adivinado.
  - **Hacer:** una migración de datos (o un comando puntual) que corrija solo esas filas, con
    `reference_unit` en kg o l, y deje como `unknown` las ~160 restantes (sobre todo conservas con
    peso escurrido). Reescribe histórico: **necesita tu visto bueno**, y antes una copia de la base
    de datos.
- [ ] **`is_available` marca como no disponible un producto que todavía se vende.**
  - **Situación:** el scraper pone `is_available = false` si viene `unavailable_from`, pero esa
    fecha es futura (3 productos, entre el 24 y el 27 de octubre): el producto deja de venderse a
    partir de ese día. Además, `unavailable_weekdays` (7 productos que no se venden algunos días
    de la semana) no se guarda.
  - **Hacer:** decidir si `is_available` compara `unavailable_from` con el momento de la captura,
    y si merece la pena guardar las dos cosas. Poco urgente: afecta a 10 productos.
- [ ] **Varias ejecuciones el mismo día.**
  - **Situación:** el 23 de octubre hubo 4 ejecuciones. Cada una vuelve a contar los mismos 45
    cambios contra el día anterior, así que sumar `price_changes` en `history` cuenta de más. Y un
    producto que desaparece en una ejecución posterior conserva el precio de la anterior: es el
    caso de la crema K-Skin (`18627`), retirada a las 21:00, que además es el único precio
    `unknown` del día (lo guardó la ejecución de las 15:51, anterior al arreglo).
  - **Hacer:** documentarlo en el README («los contadores de una ejecución se refieren al día, no
    a la ejecución»), o que `report` solo cuente los productos activos al final del día. Con la
    ejecución diaria programada casi no pasará.

## P3 · Funcionalidad nueva

- [ ] **Segunda cadena** (Carrefour, DIA…), con el skill `add-supermarket`. Es la prueba real de que el
  modelo y el emparejamiento por EAN funcionan.
- [ ] **Comparar marcas blancas.** Hacendado (1.796), Deliplus (561) y Bosque Verde (306) son el
  62 % del catálogo activo de Mercadona y no tienen equivalente por EAN en otras cadenas. Otros 514
  productos no tienen marca: son producto fresco (carne, fruta y verdura, panadería, pescado). El
  cruce por EAN cubrirá como mucho 1.097 productos (una cuarta parte del catálogo); el resto hay que
  emparejarlo por categoría, tipo de producto y precio de referencia. Hay otras marcas propias más
  pequeñas (Colorcor, Compy, Delikuit…) que reducen aún más esa cifra.
- [ ] **API REST de solo lectura**, después de la segunda cadena: búsqueda, histórico de un producto y
  comparación por EAN.
- [ ] **Comprimir y rotar el archivo JSON en bruto.** A 5,5 MB al día, guardarlo a diario son unos
  2 GB al año. Guardar con gzip (medido: 5,3 MB → 0,46 MB, 11 veces menos, unos 170 MB al año) y borrar los volcados de más de
  N días (`SCRAPER_RAW_RETENTION_DAYS`).
- [ ] **Duración real al reprocesar con `--from-raw`.** El reloj congelado hace que la ejecución dure
  «0.0s» y que `history` la muestre con la hora de la captura. Medir la duración con el reloj real
  y usar el congelado solo para el día de los precios.
- [ ] **Guardar los distintivos de Mercadona** (`badges`): `requires_age_check` (211 productos:
  alcohol y similares) y `is_water` (38). Útiles como filtros en el comparador; hoy se descartan.

## Herramientas

- [ ] **Primera versión:** `uv run cz bump --yes` desde `scraper/` → `v0.2.0` y `CHANGELOG.md`.
- [ ] **CI, cuando el repositorio esté en GitHub:**
  - Comprobar los mensajes de commit de cada PR (`cz check --rev-range origin/main..HEAD`).
  - Cambiar `uv sync --frozen` por `uv sync --locked`: `--frozen` no detecta un `uv.lock`
    desactualizado respecto a `pyproject.toml`.
