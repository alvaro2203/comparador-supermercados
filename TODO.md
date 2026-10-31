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
- [ ] **Recalcular los precios de referencia antiguos que se pueden comprobar.** Migración escrita;
  falta ensayarla en `staging` y aplicarla en producción.
  - **Situación:** del 18 al 23 de octubre quedan 1.587 precios con `reference_unit = 'unknown'`
    (formatos «por 100 g», «por 100 ml», «por lavado» y «por docena», antes del arreglo).
  - **Hecho (visto bueno dado el 31 de octubre):** migración de datos `0006`. Corrige una fila solo
    si los datos guardados confirman el valor: un día posterior, el mismo producto, al mismo precio
    de estante, tiene una unidad conocida y una referencia igual a la antigua por un factor de
    formato (×10, ×1 o ÷12); o, si no, la antigua ×10 coincide con su propio `bulk_amount` y la
    unidad (la última conocida del producto o su `size_format`) es kg o l. No toca nada más.
  - **Medido:** en producción (solo lectura), 1.577 de las 1.587 filas cumplen la primera regla o la
    de `bulk_amount` con unidad posterior; las 10 restantes (3 productos retirados antes del arreglo)
    entran por `size_format`. En una copia de la SQLite local: 1.587 corregidas, 0 `unknown`, 0
    filas con otros cambios; 63 quedan distintas de `bulk_amount` por el peso escurrido, como es debido.
  - **Pendiente:** fusionar la PR; antes, ensayarla en `staging` (reiníciala desde producción y
    ejecuta `init-db` contra ella; ver la PR) y comprobar con la consulta de unidades por día.
- [x] **`is_available` marca como no disponible un producto que todavía se vende.**
  - **Situación:** el scraper ponía `is_available = false` si venía `unavailable_from`. Medido en
    producción el 31 de octubre: es un instante (`2026-10-27T23:00:00+00:00`, medianoche en Madrid)
    que limita las fechas de entrega de la tienda online, no la venta. Los tres productos con fecha
    (melocotón, lima y mandarinas) salieron del catálogo en la ejecución del día anterior a esa fecha
    y `delist` ya los marcó. `unavailable_weekdays` son días sin reparto (1 = lunes: pescado fresco
    los lunes, marisco de lunes a jueves), y esos 7 productos tienen precio todos los días.
  - **Hecho (decidido el 31 de octubre):** `is_available` es solo `published`; los dos campos no se
    guardan (README, «Availability»). Sin migración: la primera ejecución con el arreglo sobrescribe
    el indicador, como en los espárragos `69643`, el único producto activo y «no disponible» hoy.
- [x] **Varias ejecuciones el mismo día.**
  - **Situación:** el 23 de octubre hubo 4 ejecuciones y el 24, 2; desde la ejecución programada,
    una al día. `new_products` y `delisted` ya eran de cada ejecución (el 23: 12, 1, 0, 0), pero
    `price_changes` comparaba siempre con el día anterior y repetía los mismos 45 cambios en cada
    una, así que sumarlos en `history` contaba de más. Además, 4 precios son de productos que una
    ejecución posterior del mismo día retiró (`18627` el 23; `27842`, `33053` y `33640` el 24).
  - **Hecho (decidido el 31 de octubre):** cada ejecución compara con el último precio guardado
    hasta su día, incluido el que va a sobrescribir: una repetición sin cambios cuenta 0, y todos los
    contadores de `history` son de su ejecución y se pueden sumar. `report` sigue comparando días.
    Los 4 precios se quedan: se vieron de verdad, y `report` los da como retirados el día siguiente.
    Las ejecuciones antiguas no se recalculan (README, «Price changes»).

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
