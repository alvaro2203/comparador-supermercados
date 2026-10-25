# TODO

Lista revisada el 24 de octubre de 2026 tras auditar la base de datos (precios del 18 al 23 de
octubre), el volcado JSON del 23 de octubre y el código, y actualizada el 25 de octubre (P1 y
Herramientas). Las tareas ya hechas antes del 24 de octubre (migraciones, EAN, `report`, `dump`...)
están en el historial de git y sus decisiones en el README.

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

- [x] **Programar la ejecución diaria.**
  - **Hecho:** `.github/workflows/daily-scrape.yml` ejecuta `run --save-raw` cada día a las 05:30 UTC
    contra PostgreSQL en Neon. Primera ejecución programada sin ayuda: 25 de octubre de 2026,
    comprobada en Neon. El histórico de 4 días se copió con `copy-db`. Guía:
    `docs/ejecucion-diaria.md`.
  - **Sigue abierto:** faltan el 19 y el 22 de octubre, y no se pueden recuperar.
- [ ] **Copia de seguridad de la base de datos.**
  - **Situación:** el histórico no se puede volver a descargar. Con Neon, la base ya no es un único
    fichero en tu PC, pero sigue siendo una sola copia: un `DELETE` a mano sobre `supermarkets`
    borraría en cascada todo el histórico de la cadena. La rama `staging` no es una copia de
    seguridad: se reinicia desde producción y se usa para ensayar.
  - **Hacer:** comprobar en el panel de Neon qué restauración ofrece el plan gratuito (ventana de
    tiempo y cómo se usa) y probarla una vez sobre `staging`. Si no basta, añadir un volcado
    periódico (`pg_dump`) como artefacto o en otro almacenamiento. Mientras tanto,
    `scraper/data/supermarkets.db` es una copia local hasta el 23 de octubre: no la borres.
- [x] **Tests contra PostgreSQL en el CI.**
  - **Hecho (PR #3):** con `SCRAPER_TEST_DATABASE_URL`, el fixture `engine` usa esa base PostgreSQL,
    vaciada antes de cada test y solo si el host es local (`local_test_database_url`). El job
    `postgres` de `ci.yml` ejecuta los 74 tests de integración contra un PostgreSQL 17 desechable.
    Tres tests propios de SQLite usan `sqlite_engine`.
  - **Comprobar una vez:** que `postgres` está como check obligatorio en el ruleset `proteger-main`
    (paso A6 de `docs/entorno-de-pruebas.md`).
- [x] **Base de pruebas `staging`.**
  - **Hecho y probado el 25 de octubre:** rama `staging` en Neon; entornos `production` (solo desde
    `main`) y `staging` en GitHub, cada uno con su `SCRAPER_DATABASE_URL`; el secreto de repositorio
    se retiró. `daily-scrape` tiene el input `target` (por defecto `staging` en los lanzamientos
    manuales; las programadas van a producción). Probado: una ejecución a `staging` no toca
    producción, y una rama que no es `main` no puede escribir en producción. Uso del día a día:
    `docs/entorno-de-pruebas.md`, B5.
  - **No se hace:** un descargador de pruebas diario. Duplicaría las peticiones a Mercadona; los
    fixtures, `--dry-run` y `--from-raw` ya lo cubren.
- [x] **Guardar el JSON en bruto en la ejecución diaria.**
  - **Hecho:** el workflow usa `run --save-raw` y sube los JSON como artefacto durante 14 días
    (478 KB comprimidos). Para guardarlos más tiempo, ver la tarea de comprimir y rotar en P3.

## P2 · Calidad de los datos

- [x] **El IVA está vacío en el 73 % de los precios.**
  - **Situación:** el 23 de octubre, 3.120 de 4.275 precios tenían `vat_rate` NULL. El scraper leía
    `price_instructions.iva`, que Mercadona solo rellena en 1.155 productos. El campo
    `tax_percentage` («10.000», «21.000», «4.000») viene en todos, y coincide con `iva` siempre que
    los dos existen.
  - **Hecho (PR #2):** el scraper lee `tax_percentage`, con `iva` de respaldo
    (`PriceInstructions.vat_rate`). Es opcional: sin ninguno de los dos, el producto se guarda con IVA
    NULL. Reprocesando el volcado del 23 de octubre: 0 de 4.274 precios sin IVA (antes 3.119).
  - **Histórico rellenado el 25 de octubre**, primero en `staging` y luego en producción: los días
    anteriores al 25 copian el IVA que el mismo producto tiene el 25 (solo filas NULL):
    ```sql
    UPDATE price_snapshots s
    SET vat_rate = ref.vat_rate
    FROM price_snapshots ref
    WHERE ref.product_id = s.product_id
      AND ref.captured_on = DATE '2026-10-25'
      AND ref.vat_rate IS NOT NULL
      AND s.vat_rate IS NULL
      AND s.captured_on < DATE '2026-10-25';
    ```
  - **Quedan sin IVA a propósito** los productos retirados antes del 25 (40 precios el 18, 4 el 24):
    no hay ningún día con IVA del que copiarlo, y adivinarlo sería inventar datos. Los precios ya
    incluyen el IVA, así que no afecta a la comparación. Tu SQLite local (hasta el 23) no se tocó.
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

- [x] **Primera versión:** `v0.2.0` y `CHANGELOG.md`, el 24 de octubre.
- [x] **Versiones automáticas en cada fusión a `main`.**
  - **Hecho (PR #4):** `.github/workflows/release.yml` ejecuta `cz bump` en cada push a `main`. Con
    `feat`, `fix`, `refactor` o `perf` desde la última etiqueta, sube la versión, actualiza el
    CHANGELOG, hace el commit `bump:`, crea la etiqueta y publica la release; con solo `docs`, `ci`,
    `chore`... no hace nada. Hace push con una clave de despliegue (entorno `release`, solo `main`)
    que es la excepción del ruleset. Primera release automática: `v0.3.0`, el 25 de octubre.
    Configuración: `docs/flujo-github.md`.
- [ ] **Comprobar el título de cada PR.**
  - **Situación:** con *Squash and merge*, el título de la PR es el único commit que llega a `main`, y
    de él depende la versión. El hook `commit-msg` solo revisa los commits de tu PC; un título sin
    formato no genera versión ni aparece en el CHANGELOG, y nada lo avisa.
  - **Hacer:** un workflow pequeño, en los eventos de PR (abrir, editar el título, nuevos commits),
    que falle si el título no sigue Conventional Commits (con `cz check --message`), y añadirlo como
    check obligatorio en el ruleset.
- [ ] **`uv sync --locked` también en el job `quality` del CI.** Es el único que sigue con
  `uv sync --frozen`, que no detecta un `uv.lock` desactualizado respecto a `pyproject.toml`. Los
  demás workflows (`postgres`, `daily-scrape`, `release`) ya usan `--locked`.
