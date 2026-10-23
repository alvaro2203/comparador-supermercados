# TODO

Lista de tareas salida de la revisión de arquitectura de las migraciones (Alembic) y del análisis de
las dos primeras ejecuciones reales contra Mercadona (18 y 20 de octubre de 2026).

Prioridades:

- **P0** puede perder datos o fallar sin avisar. Va antes que nada.
- **P1** bloquea el comparador o deja huecos en el histórico.
- **P2** mejora la calidad de los datos.
- **P3** funcionalidad nueva.

---

## P0 · Arreglar las migraciones (revisión de arquitectura)

La base de datos actual no corre peligro mientras solo exista la migración `0001`. Todo esto tiene que
estar resuelto **antes de crear la `0002`**.

- [x] **`alembic upgrade` desde la terminal puede borrar el histórico de precios.**
  - **Problema:** la rama de `migrations/env.py` que se usa cuando no se le pasa conexión migra con las
    foreign keys activadas. En SQLite, una migración en modo batch sobre `products` hace `DROP TABLE`, y
    el `ON DELETE CASCADE` vacía `price_snapshots`. El revisor lo reprodujo: quedaron 0 precios. Sin
    `SCRAPER_DATABASE_URL`, esa rama apunta a `data/supermarkets.db`.
  - **Arreglo:** que `env.py` use el mismo `_migration_connection` que `upgrade_schema()`. Añadir un
    test que pase por esa rama con una migración que reconstruya `products` y compruebe que los precios
    siguen ahí.
- [x] **Las migraciones no son atómicas en SQLite.**
  - **Problema:** el driver `pysqlite` no abre la transacción antes de un `CREATE` o un `DROP`. Una
    migración que falla a medias deja tablas creadas, y todas las ejecuciones siguientes fallan con
    "table already exists".
  - **Arreglo:** en la conexión de migración, poner `isolation_level = None` y emitir `BEGIN` a mano.
    Añadir un test que compruebe que una migración fallida no deja tablas nuevas.
- [x] **Un fallo de migración sale con código 1 ("partial") y con traceback.**
  - **Problema:** systemd trata el código 1 como éxito (`SuccessExitStatus=1`), así que el fallo
    pasaría desapercibido a diario.
  - **Arreglo:** que el CLI capture `SQLAlchemyError` y `alembic.util.CommandError` alrededor de
    `upgrade_schema`, muestre el error en una línea y devuelva `ExitCode.FAILED` (2). Añadir un test en
    `test_cli.py`.
- [x] **La adopción de una base de datos antigua solo comprueba la tabla `supermarkets`.**
  - **Problema:** una base de datos creada a medias quedaría marcada como `0001` aunque le falten
    tablas.
  - **Arreglo:** marcarla solo si existen las seis tablas de la versión inicial. Si no, dar un error
    claro.
- [x] **`env.py` ignora en silencio un valor de `connection` que no sea un `Connection`.**
  - **Problema:** en ese caso migraría la base de datos por defecto.
  - **Arreglo:** lanzar `TypeError` cuando la clave existe pero el valor no es un `Connection`.
- [x] **No se valida la integridad al terminar.**
  - **Problema:** las migraciones corren con las foreign keys desactivadas, y al volver a activarlas
    nadie comprueba los datos.
  - **Arreglo:** ejecutar `PRAGMA foreign_key_check` antes de confirmar la transacción, y abortar si
    devuelve alguna fila.
- [x] **Docstring engañoso en `script.py.mako`.**
  - **Problema:** el `downgrade` promete "sin borrar datos", pero autogenerate lo rellena con
    `drop_column`.
  - **Arreglo:** dejar un docstring neutro.
- [x] **Corregir la documentación.**
  - En `persistence.md` y el README, limitar la frase sobre las foreign keys a lo que es cierto hoy, o
    reescribirla cuando esté hecho el primer punto.
  - Avisar de que `alembic upgrade head` no adopta bases de datos antiguas: hay que lanzar
    `supermarket-scraper init-db` primero.
  - Mencionar `env.py` como segundo punto de entrada en "Architecture", en el README y en CLAUDE.md.

## P1 · Histórico continuo y EAN completos

- [ ] **Programar la ejecución diaria.**
  - **Problema:** el 19 de octubre no tiene datos, y las ejecuciones se lanzaron a mano a horas
    distintas (01:33 y 20:27, hora de Madrid). El 18 de octubre fue la primera carga.
  - **Hacer:** registrar la tarea con `scripts/register_windows_task.ps1` para que corra a las 06:30,
    después de que Mercadona actualice los precios.
  - **Aplazado:** se hará con GitHub Actions cuando el scraper esté pulido.
- [x] **Completar los EAN cuanto antes.**
  - **Situación:** solo 1.000 de 4.296 productos (23 %) tienen EAN, marca y detalles, porque
    `SCRAPER_DETAILS_LIMIT=500` limita la descarga por ejecución. A ese ritmo se tardan unas 9
    ejecuciones, y mientras tanto no se puede cruzar casi nada entre cadenas.
  - **Hacer:** un comando de relleno puntual, por ejemplo `enrich --all`, que descargue los ~3.300
    restantes sin saltarse los límites de cortesía. Con 0,25 s entre peticiones son unos 15 minutos.
    Otra opción es subir el límite solo durante unos días.
  - **Hecho:** comando `uv run supermarket-scraper enrich [CODE ...] [--limit N]`. Confirma cada
    100 productos (si se corta, la siguiente ejecución sigue donde lo dejó) y se para si falla un
    lote entero. `run` usa el mismo código para sus 500 diarios.
  - **Resultado (21 oct):** 3.282 de 3.283 en 14 minutos. 4.280 de 4.281 productos activos (99,98 %)
    tienen EAN y `gtin`; todos los EAN son válidos. El que falta es el producto `8300` («Pintalabios
    Creamy Deliplus 219 marrón chocolate»): su ficha devuelve **404** aunque sigue en el catálogo.
  - **Hecho:** cada fallo de detalle se escribe en el log (`Detail enrichment failure`, con
    `scope` y `reason`) y `enrich` imprime los motivos (hasta 20). Antes no quedaba rastro de ellos.
- [x] **Normalizar los EAN.**
  - **Situación:** 996 EAN tienen 13 dígitos. Nutella y Heineken traen 8 dígitos, que es un EAN-8
    válido. El gnocchi Hacendado (12 dígitos) y el tequila Olmeca (11) parecen haber perdido ceros por
    la izquierda.
  - **Hacer:** guardar una forma normalizada (GTIN-14, rellenando con ceros por la izquierda) en una
    columna nueva, con su migración, y conservar el valor original. Validar el dígito de control y
    marcar los códigos que no lo cumplan.
  - **Hecho:** columna `products.gtin` (migración `0002`, con relleno de los EAN existentes) y
    `normalize_gtin` en el dominio. `gtin` es NULL si el EAN falla el dígito de control, así que
    `ean IS NOT NULL AND gtin IS NULL` lista los inválidos. Comprobado sobre una copia de la base de
    datos: los 1.000 EAN actuales son válidos, también los de 8, 11 y 12 dígitos (no estaban
    corruptos: eran EAN-8 y UPC sin el cero inicial).
- [x] **Elegir la región de precios.**
  - **Situación:** los precios de Mercadona dependen del almacén (`MERCADONA_WAREHOUSE`, por defecto
    `mad1`).
  - **Hacer:** decidir qué región o regiones interesan antes de acumular meses de histórico. Si son
    varias, el almacén tiene que formar parte de la clave del precio, con cambio de esquema incluido.
  - **Decidido:** solo Madrid (`mad1`). Sin cambio de esquema. Documentado en README, CLAUDE.md y
    `.env.example`, con el aviso de que cambiar el almacén en una base de datos existente mezcla
    regiones en el histórico.

## P2 · Calidad de los datos

- [x] **EAN compartidos por varios productos de una misma cadena.**
  - **Situación:** 13 EAN aparecen en 2 productos cada uno (26 en total). Son panes de la panadería:
    la pieza entera y su versión rebanada, con ids como `9638.1` y `9638.2`. El EAN no identifica un
    producto único dentro de Mercadona.
  - **Hacer:** comprobar si pasa solo con el pan o también en otras categorías. Decidir cómo trata el
    comparador un `gtin` con varios productos (devolver todos, o elegir por nombre y formato) y
    dejarlo documentado antes de cruzar con la segunda cadena.
  - **Comprobado (23 oct):** solo pasa en la panadería. Son 13 pares `X.1`/`X.2` (entero y
    rebanado) con el mismo EAN, el mismo tamaño y siempre el mismo precio.
  - **Decidido:** el comparador devuelve todos los productos de un `gtin` como variantes. Documentado
    en README, CLAUDE.md y las reglas; un test fija que `gtin` no es único.
- [x] **Productos activos cuya ficha devuelve 404.**
  - **Situación:** el `8300` está en el catálogo pero su ficha no existe. Cada `run` lo vuelve a
    pedir entre sus 500 diarios, y `enrich` también.
  - **Hacer:** decidir qué hacer con los productos sin ficha (marcarlos, o dejar de reintentarlos
    durante unos días). Mientras haya pocos, no es urgente.
  - **Decidido: no hacer nada.** El 8300 dio 404 el 21 de octubre y ese mismo día Mercadona lo
    retiró del catálogo: el 404 era el aviso previo a la retirada. El 23 de octubre no queda ningún
    producto activo sin ficha. Reabrir si los fallos de `Detail enrichment failure` se acumulan.
- [x] **Productos sustituidos por otro identificador.**
  - **Situación:** la «Papilla 8 cereales con galleta María Nestlé» se retiró con el id `35602` y
    reapareció con el id `18497` y otra imagen. El histórico de precios queda partido en dos productos.
  - **Hacer:** detectar sucesores (mismo EAN o, si falta, el mismo nombre y formato el mismo día) y
    enlazarlos, por ejemplo con `replaced_by_product_id`.
  - **Comprobado (23 oct):** la papilla no es el mismo artículo: pasó de 725 g a 480 g y cambió de
    EAN. Los otros 4 candidatos por nombre (napolitana 130 → 147 g, refresco, pañales) también
    cambian de tamaño y de EAN. Según GS1, otro tamaño es otro producto.
  - **Hecho:** columna `products.replaced_by_product_id` (migración `0003`) que cada `run` rellena
    solo cuando un producto retirado y uno nuevo comparten `gtin` (un único candidato, visto por
    primera vez después de la última vez que se vio el retirado). Por nombre no se enlaza. Con los
    datos actuales da 0 enlaces. Los cambios de tamaño se comparan por precio de referencia (otra
    tarea de P2).
- [x] **`is_new_arrival` nunca está activo con datos reales.**
  - **Situación:** el 20 de octubre aparecieron 9 productos nuevos y ninguno trae `is_new = true`. La
    fixture de los tests sí trae uno.
  - **Hacer:** investigar con el agente `supermarket-api-researcher` si Mercadona ha dejado de enviar
    el campo o lo envía en otro sitio. Mientras tanto, calcular las «novedades» a partir de
    `first_seen_at`.
  - **Causa (23 oct):** fallo del scraper. Mercadona sí marca las novedades, pero en el campo
    `is_new_arrival` del producto; el scraper leía `price_instructions.is_new`, que siempre viene a
    `false`. Comprobado con 4 páginas reales (26 novedades). No hizo falta el agente.
  - **Hecho:** el scraper lee `is_new_arrival`, obligatorio en el esquema (si desaparece, sale como
    `SourceSchemaError`, no como un `false` silencioso). Fixtures corregidas a la forma real.
    `is_new_arrival` es el distintivo de Mercadona (dura semanas); `first_seen_at` es cuándo lo vimos
    por primera vez. Los productos actuales se marcarán en el próximo `run`.
- [x] **Nombres repetidos.**
  - **Situación:** varios aceites Hacendado comparten nombre (hasta 3 productos con «Aceite de oliva
    virgen extra Hacendado»). Seguramente son tamaños distintos, pero no está comprobado.
  - **Hacer:** confirmarlo, y que cualquier listado muestre siempre el nombre junto con el formato
    (`packaging`, `unit_size`).
  - **Confirmado (23 oct):** 262 nombres repetidos en 631 productos activos; ninguno con la misma
    presentación.
  - **Hecho:** `Product.label` («Aceite de oliva virgen extra Hacendado (Garrafa 5 l)», «(Pack-6 6 x
    1,5 l)», «(Paquete 26 ud)»). Con el catálogo real da 4.275 etiquetas distintas para 4.275
    productos activos. Documentado como la forma obligatoria de mostrar un producto.
- [x] **Distinguir los cambios de precio reales de las promociones.**
  - **Situación:** hubo 80 cambios en dos días (53 subidas y 27 bajadas, +2,4 % de media). El contador
    `price_changes` no separa una subida de tarifa de la entrada o salida de una promoción.
  - **Hacer:** clasificar cada cambio (tarifa, inicio de promoción, fin de promoción) y guardarlo en
    `scrape_runs` o calcularlo al consultar.
  - **Hecho:** `Price.change_from` clasifica en subida, bajada, inicio y fin de promoción. Cada `run`
    guarda el desglose en `scrape_runs` (migración `0004`) y lo muestran el informe y `history`
    (columnas `up`, `down`, `promo+`, `promo-`; `-` en ejecuciones anteriores).
  - **Resultado con los datos guardados:** del 18 al 20 de octubre, los 80 cambios fueron 35 subidas,
    16 bajadas, 11 promociones que empiezan y 18 que acaban. De las «53 subidas» que conté al
    principio, 18 eran solo el fin de una promoción.
- [x] **Comparar por precio de referencia.**
  - **Situación:** el aguacate (−29 %) y el calabacín (−28 %) son producto fresco y oscilan mucho.
  - **Hacer:** que las comparaciones y alertas usen `reference_amount` (€/kg, €/l) en lugar del precio
    del envase.
  - **Fallo encontrado (23 oct):** unos 530 precios al día (12 %) tenían `reference_unit = unknown`.
    Mercadona da la referencia «por 100 g» o «por 100 ml» en envases pequeños, y el parser no lo
    entendía: el orégano quedaba con 4,00 «por algo» en lugar de 40,00 €/kg.
  - **Hecho:** `MeasureUnit.parse_quantity` («100 g» → 0,1 kg, «75 cl», «12 ud»...) y el scraper
    divide por esa cantidad, así que `reference_amount` es siempre por kg, l, unidad, docena o metro.
    Verificado con 214 productos reales: ninguno `unknown` y coincide con el `bulk_price` de
    Mercadona. Documentado que comparaciones y alertas usan `reference_amount`; aún no existen, se
    aplicará en `report`, la API y las alertas.
  - **No hecho a propósito:** no he reescrito los ~2.100 precios antiguos afectados (18-23 oct). Para
    unos 200 no se puede saber el formato original; quedan como `unknown` (no comparables) en vez de
    con un valor adivinado.

## P3 · Funcionalidad nueva

- [ ] **Comando `report`.** Un resumen de la última ejecución frente a la anterior: cambios de precio
  con los mayores porcentajes, altas, bajas, promociones y cobertura de EAN. Es el análisis que hoy se
  hace a mano con SQL.
- [ ] **Comando `dump`.** Guardar los JSON en bruto de cada ejecución en `data/raw/<fecha>/`, para
  poder reprocesarlos sin volver a llamar a la API y para depurar cambios de la API.
- [ ] **Copia de seguridad diaria** de `supermarkets.db` antes de cada ejecución, rotando las copias
  como los logs. El histórico no se puede volver a descargar.
- [ ] **Alertas** cuando una ejecución termine `partial` o `failed` (correo, Telegram o una
  notificación del sistema). Hoy solo se ve en los logs y en Task Scheduler.
- [ ] **Segunda cadena** (Carrefour, DIA…), con el skill `add-supermarket`. Es la prueba real de que el
  modelo y el emparejamiento por EAN funcionan.
- [ ] **Comparar marcas blancas.** Hacendado, Deliplus o Bosque Verde no tienen equivalente por EAN en
  otras cadenas. Habrá que emparejar por categoría, tipo de producto y precio de referencia.
- [ ] **API REST de solo lectura**, después de la segunda cadena: búsqueda, histórico de un producto y
  comparación por EAN.
