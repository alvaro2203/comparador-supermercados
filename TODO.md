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

- [ ] **`alembic upgrade` desde la terminal puede borrar el histórico de precios.**
  - **Problema:** la rama de `migrations/env.py` que se usa cuando no se le pasa conexión migra con las
    foreign keys activadas. En SQLite, una migración en modo batch sobre `products` hace `DROP TABLE`, y
    el `ON DELETE CASCADE` vacía `price_snapshots`. El revisor lo reprodujo: quedaron 0 precios. Sin
    `SCRAPER_DATABASE_URL`, esa rama apunta a `data/supermarkets.db`.
  - **Arreglo:** que `env.py` use el mismo `_migration_connection` que `upgrade_schema()`. Añadir un
    test que pase por esa rama con una migración que reconstruya `products` y compruebe que los precios
    siguen ahí.
- [ ] **Las migraciones no son atómicas en SQLite.**
  - **Problema:** el driver `pysqlite` no abre la transacción antes de un `CREATE` o un `DROP`. Una
    migración que falla a medias deja tablas creadas, y todas las ejecuciones siguientes fallan con
    "table already exists".
  - **Arreglo:** en la conexión de migración, poner `isolation_level = None` y emitir `BEGIN` a mano.
    Añadir un test que compruebe que una migración fallida no deja tablas nuevas.
- [ ] **Un fallo de migración sale con código 1 ("partial") y con traceback.**
  - **Problema:** systemd trata el código 1 como éxito (`SuccessExitStatus=1`), así que el fallo
    pasaría desapercibido a diario.
  - **Arreglo:** que el CLI capture `SQLAlchemyError` y `alembic.util.CommandError` alrededor de
    `upgrade_schema`, muestre el error en una línea y devuelva `ExitCode.FAILED` (2). Añadir un test en
    `test_cli.py`.
- [ ] **La adopción de una base de datos antigua solo comprueba la tabla `supermarkets`.**
  - **Problema:** una base de datos creada a medias quedaría marcada como `0001` aunque le falten
    tablas.
  - **Arreglo:** marcarla solo si existen las seis tablas de la versión inicial. Si no, dar un error
    claro.
- [ ] **`env.py` ignora en silencio un valor de `connection` que no sea un `Connection`.**
  - **Problema:** en ese caso migraría la base de datos por defecto.
  - **Arreglo:** lanzar `TypeError` cuando la clave existe pero el valor no es un `Connection`.
- [ ] **No se valida la integridad al terminar.**
  - **Problema:** las migraciones corren con las foreign keys desactivadas, y al volver a activarlas
    nadie comprueba los datos.
  - **Arreglo:** ejecutar `PRAGMA foreign_key_check` antes de confirmar la transacción, y abortar si
    devuelve alguna fila.
- [ ] **Docstring engañoso en `script.py.mako`.**
  - **Problema:** el `downgrade` promete "sin borrar datos", pero autogenerate lo rellena con
    `drop_column`.
  - **Arreglo:** dejar un docstring neutro.
- [ ] **Corregir la documentación.**
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
- [ ] **Completar los EAN cuanto antes.**
  - **Situación:** solo 1.000 de 4.296 productos (23 %) tienen EAN, marca y detalles, porque
    `SCRAPER_DETAILS_LIMIT=500` limita la descarga por ejecución. A ese ritmo se tardan unas 9
    ejecuciones, y mientras tanto no se puede cruzar casi nada entre cadenas.
  - **Hacer:** un comando de relleno puntual, por ejemplo `enrich --all`, que descargue los ~3.300
    restantes sin saltarse los límites de cortesía. Con 0,25 s entre peticiones son unos 15 minutos.
    Otra opción es subir el límite solo durante unos días.
- [ ] **Normalizar los EAN.**
  - **Situación:** 996 EAN tienen 13 dígitos. Nutella y Heineken traen 8 dígitos, que es un EAN-8
    válido. El gnocchi Hacendado (12 dígitos) y el tequila Olmeca (11) parecen haber perdido ceros por
    la izquierda.
  - **Hacer:** guardar una forma normalizada (GTIN-14, rellenando con ceros por la izquierda) en una
    columna nueva, con su migración, y conservar el valor original. Validar el dígito de control y
    marcar los códigos que no lo cumplan.
- [ ] **Elegir la región de precios.**
  - **Situación:** los precios de Mercadona dependen del almacén (`MERCADONA_WAREHOUSE`, por defecto
    `mad1`).
  - **Hacer:** decidir qué región o regiones interesan antes de acumular meses de histórico. Si son
    varias, el almacén tiene que formar parte de la clave del precio, con cambio de esquema incluido.

## P2 · Calidad de los datos

- [ ] **Productos sustituidos por otro identificador.**
  - **Situación:** la «Papilla 8 cereales con galleta María Nestlé» se retiró con el id `35602` y
    reapareció con el id `18497` y otra imagen. El histórico de precios queda partido en dos productos.
  - **Hacer:** detectar sucesores (mismo EAN o, si falta, el mismo nombre y formato el mismo día) y
    enlazarlos, por ejemplo con `replaced_by_product_id`.
- [ ] **`is_new_arrival` nunca está activo con datos reales.**
  - **Situación:** el 20 de octubre aparecieron 9 productos nuevos y ninguno trae `is_new = true`. La
    fixture de los tests sí trae uno.
  - **Hacer:** investigar con el agente `supermarket-api-researcher` si Mercadona ha dejado de enviar
    el campo o lo envía en otro sitio. Mientras tanto, calcular las «novedades» a partir de
    `first_seen_at`.
- [ ] **Nombres repetidos.**
  - **Situación:** varios aceites Hacendado comparten nombre (hasta 3 productos con «Aceite de oliva
    virgen extra Hacendado»). Seguramente son tamaños distintos, pero no está comprobado.
  - **Hacer:** confirmarlo, y que cualquier listado muestre siempre el nombre junto con el formato
    (`packaging`, `unit_size`).
- [ ] **Distinguir los cambios de precio reales de las promociones.**
  - **Situación:** hubo 80 cambios en dos días (53 subidas y 27 bajadas, +2,4 % de media). El contador
    `price_changes` no separa una subida de tarifa de la entrada o salida de una promoción.
  - **Hacer:** clasificar cada cambio (tarifa, inicio de promoción, fin de promoción) y guardarlo en
    `scrape_runs` o calcularlo al consultar.
- [ ] **Comparar por precio de referencia.**
  - **Situación:** el aguacate (−29 %) y el calabacín (−28 %) son producto fresco y oscilan mucho.
  - **Hacer:** que las comparaciones y alertas usen `reference_amount` (€/kg, €/l) en lugar del precio
    del envase.

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
