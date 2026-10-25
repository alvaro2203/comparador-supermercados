# Guía: trabajar con ramas y pull requests

Objetivo: que nada llegue a `main` sin pasar por el CI. Importa porque el `cron` de
`daily-scrape` ejecuta cada mañana el código de `main` contra la base de datos de producción en Neon.

## Flujo diario

```powershell
git switch main
git pull
git switch -c fix/vat-from-tax-percentage      # una rama por tarea; nombre: tipo/descripcion-corta
# ...trabajo y commits (Conventional Commits)...
git push -u origin fix/vat-from-tax-percentage
```

1. En GitHub aparece el aviso **Compare & pull request**. Ábrela.
2. **El título de la PR debe seguir Conventional Commits**, por ejemplo
   `fix(scraper): read VAT from tax_percentage`. Con *Squash and merge* ese título es el único
   commit que entra en `main`, y de él calcula Commitizen la versión.
3. Espera al CI (pestaña *Checks* de la PR): `quality (ubuntu-latest)` y `quality (windows-latest)`.
4. Con los dos en verde, **Squash and merge**, y borra la rama cuando GitHub lo proponga.
5. En tu PC:
   ```powershell
   git switch main
   git pull
   git branch -d fix/vat-from-tax-percentage
   ```

Un push a una rama que no es `main` no ejecuta nada. El CI se ejecuta al abrir la PR y en cada push
posterior a esa rama, y de nuevo en `main` tras fusionar si el cambio toca `scraper/` o `ci.yml`.

## Configuración única en GitHub

Hazla en este orden. El paso 1 va primero porque los checks obligatorios solo se pueden elegir
después de haberse ejecutado al menos una vez.

### 1. Subir el cambio de `ci.yml` con una PR (el primer ejercicio)

```powershell
git switch -c ci/run-on-every-pull-request
git add .github/workflows/ci.yml
git commit -m "ci: run the checks on every pull request"
git push -u origin ci/run-on-every-pull-request
```

Abre la PR con ese mismo título y espera a que los dos checks acaben en verde. No la fusiones aún
si quieres, pero con ella abierta ya existen los checks.

### 2. Dejar solo "Squash and merge"

**Settings → General → Pull Requests:**
- Marca **Allow squash merging** y, en *Default commit message*, elige **Pull request title**.
- Desmarca **Allow merge commits** y **Allow rebase merging**.
- Marca **Automatically delete head branches**.

### 3. Proteger `main` con un ruleset

**Settings → Rules → Rulesets → New ruleset → New branch ruleset.**

| Campo | Valor |
|---|---|
| Ruleset name | `proteger-main` |
| Enforcement status | **Active** |
| Bypass list | **Add bypass → Repository admin**, con *Bypass mode: Always allow*. Te deja hacer push directo a `main` en una emergencia. Las versiones ya no lo necesitan: usan la clave de despliegue de "Versiones automáticas". |
| Target branches | **Add target → Include default branch** |

Reglas (*Branch rules*), marca estas y deja el resto como está:

- **Restrict deletions** (viene marcada).
- **Block force pushes** (viene marcada).
- **Require a pull request before merging.** *Required approvals*: `0` (trabajas solo; GitHub no
  deja aprobar tu propia PR).
- **Require status checks to pass.** Pulsa *Add checks* y elige `quality (ubuntu-latest)` y
  `quality (windows-latest)`. Si no aparecen, es que no se han ejecutado todavía: vuelve al paso 1.

Pulsa **Create**.

**Comprobar:**
```powershell
git switch main
git commit --allow-empty -m "chore: probar proteccion"
git push
```
Como administrador con bypass, ese push se permitirá y GitHub mostrará un aviso de que se ha saltado
las reglas. Borra después ese commit vacío o déjalo. Para ver la protección en acción, abre una PR y
comprueba que el botón de fusionar está desactivado mientras el CI corre.

> Si prefieres no poder hacer push directo ni tú, pon *Bypass mode: Pull requests only* o quita la
> excepción. Las versiones automáticas no se ven afectadas.

## Versiones automáticas

Cada fusión a `main` lanza el workflow `release` (`.github/workflows/release.yml`). Si desde la última
etiqueta hay algún `feat`, `fix`, `refactor`, `perf` o cambio incompatible, el workflow:

- sube la versión en `pyproject.toml` y `uv.lock`;
- añade la sección nueva a `scraper/CHANGELOG.md`;
- hace el commit `bump: version X → Y` en `main` y crea la etiqueta `vY`;
- publica una *release* en la pestaña **Releases** de GitHub.

Si solo hay `docs`, `ci`, `chore`, `test`..., no hace nada. **No ejecutes `cz bump` en tu PC**; para
ver qué saldría en la próxima fusión, `uv run cz bump --dry-run` desde `scraper/` es seguro.

Tras cada fusión, antes de crear una rama nueva, haz `git switch main` y `git pull` para traerte el
commit `bump:`.

### Configuración única (en este orden)

El workflow necesita hacer push a `main`, que está protegida. Lo hace con una **clave de despliegue**
que es la única excepción del ruleset para ese push.

**1. Subir la etiqueta `v0.2.0`, antes de fusionar la PR del workflow.** Solo existe en tu PC. Sin ella
en GitHub, la primera release no sabe dónde empezó la anterior y repetiría todo el historial.

```powershell
cd D:\Programacion\comparador-supermercados
git push origin v0.2.0
```

Comprobar: en GitHub, **Code → Tags** aparece `v0.2.0`.

**2. Crear la clave** (en PowerShell; cuando pida *passphrase*, pulsa Enter dos veces, sin escribir
nada):

```powershell
ssh-keygen -t ed25519 -C "release-bot" -f "$env:TEMP\release_key"
```

Crea dos ficheros: `release_key` (privada) y `release_key.pub` (pública).

**3. Dar la clave pública al repositorio.** **Settings → Deploy keys → Add deploy key.**
- Title: `release-bot`.
- Key: el contenido de `release_key.pub` (`Get-Content "$env:TEMP\release_key.pub"`).
- Marca **Allow write access**. Sin esto, el push falla.

**4. Guardar la clave privada en un entorno que solo acepte `main`.**
**Settings → Environments → New environment** → `release`.
- **Deployment branches and tags → Selected branches and tags → Add rule:** `main`.
- **Environment secrets → Add secret:** nombre `RELEASE_DEPLOY_KEY`, valor el contenido **completo**
  de `release_key` (`Get-Content "$env:TEMP\release_key" -Raw`), incluidas las líneas
  `-----BEGIN OPENSSH PRIVATE KEY-----` y `-----END OPENSSH PRIVATE KEY-----`.

Después, borra los dos ficheros de tu PC:
`Remove-Item "$env:TEMP\release_key", "$env:TEMP\release_key.pub"`.

**5. Dejar que esa clave se salte el ruleset.** **Settings → Rules → Rulesets → `proteger-main` →
Bypass list → Add bypass → Deploy keys**, con *Always allow*. Guarda.

Ahora la excepción de administrador ya no hace falta para las versiones. Si quieres que tampoco tú
puedas hacer push directo a `main` (el accidente de `ci/staging-target`), cambia *Repository admin*
a *Pull requests only* o quítalo.

**6. Fusionar la PR del workflow y comprobar la primera release.** Al fusionar, **Actions → release**
debe acabar en verde y:
- en `main` aparece el commit `bump: version 0.2.0 → 0.3.0`, de `github-actions[bot]`;
- en **Releases** está `v0.3.0`, con la sección del CHANGELOG;
- en tu PC, `git switch main; git pull` y `uv run supermarket-scraper --version` muestra `0.3.0`.

Los nombres exactos de los botones de GitHub (sobre todo *Deploy keys* dentro de la *Bypass list*)
son de memoria: si no aparece esa opción, dímelo y buscamos la alternativa.

### Si algo falla

| Síntoma en **Actions → release** | Causa | Qué hacer |
|---|---|---|
| `Permission to ... denied` o `protected branch` al hacer `git push` | La clave no tiene escritura o no está en la *Bypass list* | Pasos 3 y 5 |
| `Load key ... invalid format` en el checkout | El secreto no tiene la clave privada completa | Paso 4, pegando también las líneas `BEGIN`/`END` |
| `Branch ... is not allowed to deploy to release` | Lo lanzaste a mano desde otra rama | Es correcto: solo desde `main` |
| La release `v0.3.0` incluye todo el historial desde el principio | Faltaba la etiqueta `v0.2.0` en GitHub | Edita el CHANGELOG a mano en una PR; la siguiente release ya será correcta |
| `NO_COMMITS_TO_BUMP` en el log y acaba en verde | La fusión solo tenía `docs`, `ci`, `chore`... | Es lo esperado: no hay versión nueva |

## Probar una rama con datos reales

*Actions → daily-scrape → Run workflow* deja elegir la rama y el `target`. Con código sin revisar,
usa siempre `target: staging` (el valor por defecto): escribe en la rama `staging` de Neon, no en
producción. GitHub rechaza `target: production` desde cualquier rama que no sea `main`. Detalles en
`docs/entorno-de-pruebas.md`, parte B.

## Problemas conocidos

| Síntoma | Causa | Qué hacer |
|---|---|---|
| La PR dice "Expected — Waiting for status to be reported" y no avanza | El check obligatorio no se ha lanzado (se renombró el job o la PR no activa el workflow) | Comprueba que `ci.yml` tiene `pull_request:` sin filtro de rutas y que el nombre del job es `quality` |
| No puedo elegir `quality (...)` al crear el ruleset | Los checks no se han ejecutado nunca | Haz el paso 1 primero |
| `git push` a `main` rechazado | El ruleset te cuenta como no-administrador | Revisa la *Bypass list* o usa una PR |
| El título de la PR no pasa la convención | No hay comprobación automática de los títulos | Revísalo a mano al fusionar. Se puede automatizar más adelante con una acción que compruebe el título |
