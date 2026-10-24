# Guía: entorno de pruebas (PostgreSQL en el CI y base `staging`)

Objetivo: dejar de ensayar en producción. Hoy, cualquier operación sobre datos (un `UPDATE`, una
migración, un `copy-db`) se prueba directamente en la base de Neon que alimenta la ejecución diaria,
y el CI solo prueba en SQLite aunque producción es PostgreSQL.

Son dos piezas independientes. Haz primero la A: es automática y protege cada PR.

| Pieza | Qué protege | Quién la hace |
|---|---|---|
| **A. Tests en PostgreSQL en el CI** | Que una migración o una consulta que funciona en SQLite no falle en producción | Código: Claude. Ajustar el ruleset: tú |
| **B. Base `staging` en Neon** | Ensayar SQL, migraciones y código de una rama con escrituras reales, sin tocar producción | Neon y GitHub: tú. Workflow: Claude |

Lo que **no** se hace, a propósito: un descargador de pruebas que llame a Mercadona a diario. Duplicaría
las peticiones a un sitio real y no aporta nada que no den los fixtures, `--dry-run` y `--from-raw`.

---

## A. Tests en PostgreSQL en el CI

Idea: el fixture `engine` de los tests usa hoy una SQLite temporal. Si existe la variable
`SCRAPER_TEST_DATABASE_URL`, usará esa base PostgreSQL, vaciada antes de cada test. El CI levanta un
PostgreSQL desechable (un *service container* de GitHub, gratis y sin cuentas) y ejecuta contra él los
tests de integración.

### A1. Fixture `engine` con PostgreSQL opcional (código)

En `scraper/tests/conftest.py`:

- Si `SCRAPER_TEST_DATABASE_URL` no está definida, todo sigue igual (SQLite temporal).
- Si está definida:
  - **Rechazar cualquier host que no sea local** (`localhost`, `127.0.0.1` o el nombre del servicio
    del CI). Es la salvaguarda para que un test nunca pueda vaciar Neon por una variable mal puesta.
  - Antes de cada test, `DROP SCHEMA public CASCADE` y `CREATE SCHEMA public`, y después
    `upgrade_schema()`: cada test empieza con una base vacía en la última migración.
- Los tests que crean su propia SQLite (`test_cli.py`, el destino de `test_data_copy.py`) no cambian.
- Hecho: nuevo fixture `sqlite_engine` (siempre SQLite) para los tres tests de
  migraciones que solo tienen sentido en SQLite: la reconstrucción por lotes de `products`, la línea
  de comandos de Alembic y el rollback de una migración fallida. El resto usa `engine` y pasa en
  ambas bases (74 tests de integración en PostgreSQL 17).

**Comprobar:** sin la variable, `uv run pytest` sigue igual (168 tests). Con ella apuntando a un host
como `ep-...neon.tech`, el primer test falla con un mensaje claro sin conectarse.

### A2. Comprobar el esquema también en PostgreSQL (código)

`test_migrations_build_exactly_the_schema_declared_in_tables` usa el fixture `engine`, así que con
la variable definida ya compara el esquema migrado con `tables.py` en PostgreSQL, sin variante
aparte. Es lo que se hizo a mano el 24 de octubre (0 diferencias).

### A3. Probarlo en tu PC (opcional, con Docker Desktop)

```powershell
docker run -d --rm --name scraper-pg-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper -p 55432:5432 postgres:17-alpine
cd D:\Programacion\comparador-supermercados\scraper
uv sync --extra postgres
$env:SCRAPER_TEST_DATABASE_URL = "postgresql+psycopg://postgres:test@localhost:55432/scraper"
uv run pytest tests/integration --no-cov
Remove-Item Env:SCRAPER_TEST_DATABASE_URL
docker stop scraper-pg-test
```

### A4. Job nuevo en `.github/workflows/ci.yml` (código)

Un job `postgres`, solo en Linux (los service containers no existen en Windows):

```yaml
  postgres:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17-alpine
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: scraper
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      SCRAPER_TEST_DATABASE_URL: postgresql+psycopg://postgres:test@localhost:5432/scraper
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: "scraper/uv.lock"
      - run: uv sync --locked --extra postgres
      - run: uv run pytest tests/integration --no-cov
```

La cobertura mínima la sigue exigiendo el job `quality`; este solo comprueba que todo pasa en
PostgreSQL. La contraseña `test` es de un contenedor que solo existe durante el job: no es un secreto.

### A5. Documentación (código)

- `CLAUDE.md` ("Commands" y la línea del CI) y README "Quality toolchain": cómo ejecutar los tests en
  PostgreSQL y que el CI lo hace.
- `.claude/rules/scraper/testing.md`: la regla de `SCRAPER_TEST_DATABASE_URL` y que nunca apunta a
  Neon.

### A6. Hacer obligatorio el check nuevo (tú)

Cuando la PR de A1-A5 haya ejecutado el job al menos una vez:
**Settings → Rules → Rulesets → `proteger-main` → Require status checks → Add checks** → busca
`postgres` y añádelo. Guarda.

**Comprobar:** en la siguiente PR aparecen tres checks obligatorios: `quality (ubuntu-latest)`,
`quality (windows-latest)` y `postgres`.

---

## B. Base `staging` en Neon

Idea: una **rama de Neon** es una copia de producción que se crea en segundos y solo ocupa lo que
cambies en ella. Una rama fija `staging` sirve para ensayar, y se reinicia desde producción cuando se
quede vieja. GitHub Actions podrá elegir contra qué base ejecuta, y producción solo aceptará código
de `main`.

> **Antes de empezar:** mira en el panel de Neon cuántas ramas y cuánto espacio permite hoy el plan
> gratuito. No está comprobado. Producción ya es una rama (`main` o `production`); `staging` sería la
> segunda.

### B1. Crear la rama `staging` (tú, en Neon)

1. Panel del proyecto → **Branches → New branch**.
2. Nombre: `staging`. Parent: la rama principal (la de producción). Datos: *Current point in time*.
3. **Connect**, con la rama `staging` seleccionada. Copia su cadena de conexión: tiene **otro host**
   distinto del de producción. Igual que antes: conexión directa (sin `-pooler`) y cambia el principio
   a `postgresql+psycopg://`.

**Comprobar:** en el SQL Editor, con la rama `staging` seleccionada,
`select count(*) from price_snapshots;` da lo mismo que en producción.

### B2. Entornos de GitHub con su propio secreto (tú, en GitHub)

1. **Settings → Environments → New environment** → `production`.
   - **Environment secrets → Add secret:** `SCRAPER_DATABASE_URL` con la cadena de **producción**.
   - **Deployment branches and tags → Selected branches and tags → Add rule:** `main`. Así, el código
     de una rama sin fusionar nunca puede escribir en producción, ni por error.
2. **New environment** → `staging`.
   - **Environment secrets:** `SCRAPER_DATABASE_URL` con la cadena de **staging**.
   - Sin restricción de ramas: es para probar cualquier rama.
3. **No borres todavía** el secreto de repositorio (`Settings → Secrets and variables → Actions`).
   Se borra en B4, cuando el workflow ya use los entornos.

### B3. Workflow `daily-scrape` con destino elegible (código)

En `.github/workflows/daily-scrape.yml`:

- Nuevo input manual `target`, de tipo *choice*, con `staging` y `production`, **por defecto
  `staging`**: un lanzamiento manual nunca va a producción salvo que lo elijas.
- `environment: ${{ inputs.target || 'production' }}` en el job: las ejecuciones programadas (sin
  inputs) siguen yendo a producción.
- El nombre del artefacto incluye el destino, para no confundir los JSON de una prueba con los de
  producción.

### B4. Probar y retirar el secreto antiguo (tú)

1. **Actions → daily-scrape → Run workflow**, rama `main`, `target: staging`, `dry_run` desmarcado.
   Debe acabar en verde, y en Neon el día de hoy debe aparecer en `staging`, no en producción.
2. Lanza otra vez desde una rama cualquiera con `target: production`. **Debe fallar** antes de
   ejecutar nada ("branch is not allowed to deploy to production"). Es la prueba de que la protección
   funciona.
3. Si los dos salen como se espera, borra el secreto de repositorio `SCRAPER_DATABASE_URL`. A partir
   de aquí, cada entorno tiene el suyo.
4. Al día siguiente, comprueba que la ejecución programada de las 05:30 UTC escribió en
   **producción**.

> Cada ejecución contra `staging` sin `dry_run` hace las mismas ~150 peticiones a Mercadona que una
> real. Úsala para probar una rama, no a diario. Para tener datos frescos en `staging` sin llamar a
> Mercadona, usa B5.

### B5. Recetas de uso

**Ensayar SQL** (por ejemplo, el relleno del IVA):
1. En Neon, reinicia `staging` desde producción (**Branches → staging → Reset from parent**) para que
   tenga los datos de hoy.
2. Ejecuta la consulta en el SQL Editor con la rama `staging` seleccionada y revisa el resultado.
3. Si es correcto, repítela con la rama de producción seleccionada.

**Probar el código de una rama con escrituras reales:** *Run workflow* → tu rama → `target: staging`.

**Reprocesar los JSON de producción en `staging`, sin llamar a Mercadona:**
1. Descarga el artefacto `raw-json-...` de la ejecución de producción en Actions y descomprímelo en
   `scraper/data/raw/`.
2. Desde tu PC:
   ```powershell
   cd D:\Programacion\comparador-supermercados\scraper
   $env:SCRAPER_DATABASE_URL = "postgresql+psycopg://...cadena de staging..."
   uv run supermarket-scraper run --from-raw data\raw\mercadona\<fecha>
   Remove-Item Env:SCRAPER_DATABASE_URL
   ```

**Probar una migración nueva sobre datos reales:** reinicia `staging`, lanza la rama con la migración
contra `staging` y revisa con `report` y `history` antes de fusionar.

### B6. Documentación (código)

- `docs/ejecucion-diaria.md`: los entornos, el input `target` y la protección de `production`.
- README "Daily execution": una línea sobre `staging`.
- `TODO.md`: marcar las dos tareas como hechas.

---

## Orden recomendado

1. A1 → A5 en una rama, en una sola PR. Claude escribe el código.
2. A6: añadir el check `postgres` al ruleset.
3. B1 y B2 en Neon y GitHub.
4. B3 en otra rama y otra PR. Claude escribe el workflow.
5. B4: pruebas y retirada del secreto antiguo.
6. A partir de aquí, cualquier SQL sobre producción pasa antes por B5. El primero, el relleno del IVA.

## Problemas conocidos

| Síntoma | Causa | Qué hacer |
|---|---|---|
| El job `postgres` falla con `connection refused` | El contenedor aún no estaba listo | Revisa las opciones `--health-*` del servicio |
| Un test falla solo en `postgres` | Diferencia real entre SQLite y PostgreSQL | Es justo lo que se busca. Arreglarlo antes de fusionar |
| `branch is not allowed to deploy to production` | Lanzaste una rama que no es `main` contra producción | Correcto: usa `target: staging` |
| La ejecución programada falla con `Could not parse SQLAlchemy URL` | El secreto no está en el entorno `production` | Revisa B2.1 |
| `staging` se queda sin espacio | Ha acumulado cambios propios | *Reset from parent* en Neon |
