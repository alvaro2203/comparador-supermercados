# Guía: ejecución diaria con GitHub Actions y Neon

Objetivo: que el scraper se ejecute solo cada día y guarde los precios en una base de datos
PostgreSQL en la nube (Neon), sin depender de que tu PC esté encendido.

```
GitHub Actions (cada día 05:30 UTC)  ──►  Mercadona
        │
        └──► supermarket-scraper run  ──►  Neon (PostgreSQL)  ◄── tu PC (report, history)
                     └──► JSON en bruto  ──►  artefacto de GitHub (14 días)
```

El workflow ya está escrito en `.github/workflows/daily-scrape.yml`. Lo que falta son las cuentas, el
secreto y las pruebas. Cada paso dice cómo comprobar que ha salido bien. **No pases al siguiente si
el anterior falla.**

## Estado de cada pieza

| Pieza | Estado |
|---|---|
| Workflow `daily-scrape.yml` | Escrito. El YAML es válido, pero nunca se ha ejecutado en GitHub. |
| Que Mercadona responda a las IP de GitHub | Comprobado (paso 4): el `dry_run` del 24 de octubre descargó el catálogo completo. |
| Migraciones en PostgreSQL | Comprobado en un PostgreSQL 17 local (paso 5). Falta verlo en Neon. |
| Copiar el histórico de SQLite a Neon | Comando `copy-db` escrito y probado en local; **sin ejecutar contra Neon** (paso 6). |

---

## Paso 1. Subir el workflow a GitHub

Desde la raíz del repositorio, en la rama `main`:

```powershell
git status
git add .github/workflows/daily-scrape.yml docs/ejecucion-diaria.md scraper/README.md
git commit -m "ci: add daily scrape workflow"
git push
```

El mensaje debe seguir Conventional Commits o el hook lo rechaza.

**Comprobar:** en GitHub, pestaña **Actions**. Debe aparecer el workflow `scraper-ci` ejecutándose
(Linux y Windows) y, en la lista de la izquierda, `daily-scrape`. Si `scraper-ci` falla, no sigas:
copia el error y arréglalo primero. Es la primera vez que el CI corre fuera de tu PC.

> **Ojo con el horario.** Los workflows programados solo corren desde `main`. A partir del push, el
> próximo día a las 05:30 UTC GitHub lanzará una ejecución **real**. Si para entonces no tienes el
> secreto del paso 3, esa ejecución fallará y te llegará un email. No pasa nada grave, pero
> conviene tener el secreto puesto antes.

## Paso 2. Crear la base de datos en Neon

1. Entra en <https://neon.tech> y regístrate (puedes usar la cuenta de GitHub). No pide tarjeta.
2. **Create project.** Nombre: `comparador-supermercados`. Región: **Europe (Frankfurt)**, la más
   cercana a España. La versión de PostgreSQL por defecto vale.
3. En el panel del proyecto, pulsa **Connect**. Aparece una cadena como esta:

   ```
   postgresql://usuario:CLAVE@ep-algo-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```

4. **Desmarca "Connection pooling"** si está activado. Queremos la conexión directa: el host no
   debe llevar `-pooler` en el nombre.
5. Cambia el principio de la cadena. Debe empezar por `postgresql+psycopg://` (con `+psycopg`):

   ```
   postgresql+psycopg://usuario:CLAVE@ep-algo-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```

   Sin el `+psycopg`, SQLAlchemy busca otro driver que no está instalado y falla.

**Comprobar:** tienes una cadena completa, empieza por `postgresql+psycopg://`, no contiene
`-pooler` y termina en `?sslmode=require`.

> **La cadena incluye la contraseña.** No la pegues en ningún fichero del repositorio, ni en un
> chat, ni en una issue. El repositorio es público.

## Paso 3. Guardar la cadena como secreto en GitHub

1. En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**.
2. Nombre: `SCRAPER_DATABASE_URL` (exactamente así).
3. Valor: la cadena del paso 2. **Add secret**.

**Comprobar:** el secreto aparece en la lista. GitHub nunca vuelve a mostrar su valor; si te
equivocas, lo editas y lo pegas de nuevo. En los logs de Actions su valor sale como `***`.

## Paso 4. Probar que Mercadona acepta a GitHub (sin escribir nada)

Los runners de GitHub están en centros de datos de Microsoft, normalmente fuera de España.
Mercadona podría bloquearlos o redirigirlos. Se prueba antes de depender de ello.

1. **Actions → daily-scrape → Run workflow.**
2. Deja marcada la casilla **dry_run** y pulsa el botón verde.
3. Espera 2-4 minutos y abre la ejecución.

`dry_run` valida todo el catálogo pero no escribe en la base de datos.

**Comprobar:**
- **Verde.** En el log del paso `supermarket-scraper run` verás `[mercadona] SUCCEEDED (dry run)`
  con unas 4.270 productos y 0 errores. Mercadona acepta a GitHub: sigue.
- **Rojo con errores 403, 429, timeouts o catálogo vacío.** Mercadona bloquea esas IP. El plan de
  Neon + Actions no sirve tal cual. Dímelo con el log y buscamos otra vía (por ejemplo, un runner
  propio en tu PC o una máquina pequeña con IP española).

El artefacto `raw-json-<número>` de esa ejecución contiene los JSON descargados. Puedes bajarlo
para ver que el volcado es completo (unos 154 ficheros).

## Paso 5. Comprobar las migraciones en PostgreSQL (hecho)

Probado el 24 de octubre de 2026 con un PostgreSQL 17 en Docker:

- Las 5 migraciones se aplican de una vez (`0001` → `0005`) y el esquema resultante coincide con
  `tables.py` (0 diferencias).
- 36 tests de persistencia, caso de uso, sucesores e informe pasan contra PostgreSQL.
- `report` y `history` dan el mismo resultado que en SQLite, y reprocesar el volcado real del 23 de
  octubre (`run --from-raw`) sobre PostgreSQL no duplica ningún precio.

Sin comprobar: **Neon en concreto** (red, SSL, suspensión del cómputo). Eso se ve en el paso 6.

## Paso 6. Copiar el histórico a Neon

Tu `scraper/data/supermarkets.db` (4 días: 18, 20, 21 y 23 de octubre) se copia con el comando
`copy-db`. Solo lee tu fichero local, nunca lo modifica, y se niega a copiar si Neon ya tiene datos.
Antes de la copia real hay un ensayo que hace todo y lo deshace.

En PowerShell:

```powershell
cd D:\Programacion\comparador-supermercados\scraper
uv sync --extra postgres
$neon = "postgresql+psycopg://...tu cadena de Neon..."

uv run supermarket-scraper copy-db --to $neon --dry-run   # ensayo: copia, verifica y deshace
uv run supermarket-scraper copy-db --to $neon             # copia real

Remove-Variable neon
```

**Comprobar:** ambos comandos terminan con una tabla de filas copiadas. Deben salir (a 24 de
octubre): `supermarkets 1`, `categories 622`, `products 4315`, `product_categories 4583`,
`price_snapshots 17125`, `scrape_runs 7`. Si el ensayo da error, no hagas la copia real y envía el
mensaje.

Qué comprueba el comando antes de confirmar: que ambas bases están en la misma migración, que el
destino está vacío, que cada tabla tiene las mismas filas y que la suma de los importes coincide.
Cualquier diferencia deshace toda la copia. Después puedes comprobarlo con
`$env:SCRAPER_DATABASE_URL = $neon` y `uv run supermarket-scraper report`, que debe dar lo mismo
que en local.

> **No borres ni muevas `scraper/data/supermarkets.db`.** Sigue siendo tu copia de seguridad hasta
> que la nube lleve varias semanas funcionando.

## Paso 7. Primera ejecución real

Cuando los pasos 4 y 5 estén bien (y el 6 resuelto):

1. **Actions → daily-scrape → Run workflow.**
2. **Desmarca `dry_run`** y lanza.
3. Tarda unos 2-4 minutos (la primera vez descarga también hasta 500 fichas de producto).

**Comprobar:** el job termina en verde. Luego mira los datos de dos maneras:

- En Neon, **SQL Editor**:
  ```sql
  select status, products_count, errors_count from scrape_runs order by started_at desc limit 3;
  select count(*) from price_snapshots;
  ```
  Debe haber una fila `succeeded` con unos 4.270 productos, y unos 4.270 precios.
- Desde tu PC, con la cadena en una variable (solo vale para esa ventana de PowerShell):
  ```powershell
  cd D:\Programacion\comparador-supermercados\scraper
  $env:SCRAPER_DATABASE_URL = "postgresql+psycopg://...tu cadena..."
  uv sync --extra postgres
  uv run supermarket-scraper history mercadona
  uv run supermarket-scraper report
  ```
  `report` necesita dos días con datos, así que hasta el segundo día solo mostrará los totales.

> Cierra la ventana de PowerShell cuando acabes, o ejecuta `Remove-Item Env:SCRAPER_DATABASE_URL`,
> para no ejecutar luego algo contra la base de la nube sin querer.

## Paso 8. Dejarlo en automático

Desde el paso 1 el `cron` ya está activo. Nada más que hacer, salvo dos cosas:

1. **No registres la tarea de Windows** (`scripts/register_windows_task.ps1`) mientras esto funcione,
   o tendrías dos ejecuciones al día en bases distintas.
2. **Cambia el valor por defecto de `dry_run`** de `true` a `false` en `daily-scrape.yml` cuando
   estés seguro. Hoy lo protege contra ejecuciones manuales accidentales; las programadas siempre
   son reales.

## Día a día

- **Si todo va bien** no recibes nada. Mira de vez en cuando la pestaña Actions o ejecuta
  `report` desde tu PC.
- **Si un día falla** GitHub te envía un email (código 1, "partial", o 2, "failed"). Abre la
  ejecución, mira el log y, si la causa es un cambio de la API de Mercadona, usa el skill
  `diagnose-scrape-run`. El artefacto con los JSON permite reproducir el fallo sin red:
  descárgalo, descomprímelo en `scraper/data/raw/mercadona/<fecha>/` y ejecuta
  `uv run supermarket-scraper run --from-raw data\raw\mercadona\<fecha> --dry-run`.
- **Los JSON de Actions se borran a los 14 días.** Si quieres conservarlos más, hay que decidir
  dónde (tarea de comprimir y rotar en el TODO).

## Problemas conocidos

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `ModuleNotFoundError: No module named 'psycopg2'` | La cadena no lleva `+psycopg` | Corrige el secreto (paso 2.5) |
| `could not translate host name` o `Network is unreachable` | Cadena mal copiada o proyecto de Neon suspendido | Revisa el host; abre Neon y comprueba que el proyecto está activo |
| `SSL SYSCALL error` o `connection closed` | El pooler de Neon (`-pooler` en el host) | Usa la conexión directa (paso 2.4) |
| `password authentication failed` | Contraseña cambiada o mal copiada | Genera otra en Neon y actualiza el secreto |
| Error 403 o catálogo vacío de Mercadona | Bloquean las IP de GitHub | Paso 4: buscar otra vía |
| El workflow no se ejecuta solo | Repositorio público sin actividad durante 60 días: GitHub desactiva los programados | Pestaña Actions → activarlo de nuevo, o hacer un commit |
| El run sale con código 1 | Parcial: hubo páginas o fichas que fallaron, pero los datos se guardaron | Mira el log; los fallos aparecen al final de la ejecución |
| Ejecución con retraso de 10-30 minutos | GitHub no garantiza la hora exacta de los `cron` | Es normal. El día de los precios lo decide `SCRAPER_TIMEZONE`, no la hora |

## Límites que conviene saber

- **Neon gratis: unos 500 MB.** Se estima ~1 MB/día, es decir, 1-2 años. Mira el consumo real en
  el panel de Neon tras una semana y recalcula.
- **GitHub Actions** es gratuito e ilimitado en repositorios públicos.
- **Neon suspende el cómputo** si no se usa; al conectar despierta en un segundo. Es normal.
- **El repositorio es público:** el código, los logs de Actions y los artefactos los puede ver
  cualquiera. Los logs no incluyen la cadena de conexión (GitHub la oculta), pero no pongas
  nunca la contraseña en el código ni en los mensajes de commit.

## Al terminar

Marca como hechas en `TODO.md` las tres tareas de P1: ejecución diaria, copia de seguridad (Neon
guarda un historial de la base de datos que permite restaurarla; compruébalo en su panel antes de
darla por buena) y guardado de JSON (ya lo hace el workflow).
