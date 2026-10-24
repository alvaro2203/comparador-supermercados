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
| Bypass list | **Add bypass → Repository admin**, con *Bypass mode: Always allow*. Es lo que te deja hacer `cz bump` directamente sobre `main`. |
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
> excepción. El coste es que `cz bump` tendrá que hacerse en una rama y fusionarse con una PR.

## Publicar una versión (`cz bump`)

Con la excepción de administrador, se hace como hasta ahora, en `main` y con el árbol limpio:

```powershell
git switch main
git pull
cd scraper
uv run cz bump --dry-run     # vista previa
uv run cz bump               # commit "bump: version ..." y etiqueta vX.Y.Z
git push --follow-tags
```

## Probar una rama contra producción

*Actions → daily-scrape → Run workflow* deja elegir la rama. Eso ejecuta **ese código** contra Neon.
Con código sin revisar, marca siempre **dry_run**.

## Problemas conocidos

| Síntoma | Causa | Qué hacer |
|---|---|---|
| La PR dice "Expected — Waiting for status to be reported" y no avanza | El check obligatorio no se ha lanzado (se renombró el job o la PR no activa el workflow) | Comprueba que `ci.yml` tiene `pull_request:` sin filtro de rutas y que el nombre del job es `quality` |
| No puedo elegir `quality (...)` al crear el ruleset | Los checks no se han ejecutado nunca | Haz el paso 1 primero |
| `git push` a `main` rechazado | El ruleset te cuenta como no-administrador | Revisa la *Bypass list* o usa una PR |
| El título de la PR no pasa la convención | No hay comprobación automática de los títulos | Revísalo a mano al fusionar. Se puede automatizar más adelante con una acción que compruebe el título |
