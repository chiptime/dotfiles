# Modelos de opencode (v2)

Fuente única de verdad de **qué modelo usa cada agente** de opencode. Vive en el repo
y se aplica a `~/.config/opencode/opencode.json` con `scripts/install-opencode-settings-v2.sh`.

> Esto relaja a propósito el principio de `../settings/README.md` (no fijar modelos
> concretos en el repo): este repo es personal y aquí la reproducibilidad pesa más.

## Ficheros

| Fichero | Papel |
| --- | --- |
| `models.v2.json` | El mapa. Es lo único que se edita a mano. |
| `../../../../scripts/install-opencode-settings-v2.sh` | Lo aplica sobre `opencode.json`. |

Claves de `models.v2.json`:

- `small_model`: modelo para tareas laterales (títulos, etc.).
- `agents`: `nombre-de-agente -> provider/modelo`.
- `inherit`: agentes que **a propósito** no fijan modelo (heredan el de la sesión).

Otros campos de un agente (`variant`, `prompt`, `permission`, `tools`…) **no** pasan por
este mapa: siguen donde estaban.

## Cambiar un modelo

1. Edita `agents.<agente>` en `models.v2.json`.
2. `scripts/install-opencode-settings-v2.sh`
3. Reinicia opencode.

No edites `agent.*.model` a mano en `opencode.json`: es la copia aplicada y la próxima
ejecución del instalador la sobrescribe con el mapa.

## Después de `gentle-ai sync`

`gentle-ai sync` reescribe `opencode.json` y puede devolver modelos antiguos o añadir
agentes nuevos. Vuelve a ejecutar el instalador: es idempotente y restaura el mapa.

## Flags y avisos

- `--dry-run`: informa, no escribe.
- `--check`: como `--dry-run`, pero sale con 1 si habría cambios o hay avisos.

| Aviso | Significado |
| --- | --- |
| `x <agente> = <id>` (fatal) | El mapa tiene un id que no existe en `opencode models`. Aborta **antes** de escribir. |
| `huérfano` | Agente en el mapa que ya no existe en la config (p. ej. gentle-ai lo renombró). Se omite; nunca se crea un agente fantasma. |
| `sin fijar` | Agente de la config que no está en `agents` ni en `inherit`. Conserva el modelo que le dé gentle-ai. Añádelo al mapa o a `inherit`. |
| `id inválido fuera del mapa` | Un `model` de la config viva (p. ej. un fragmento) apunta a un id inexistente. |
| `validación omitida` | `opencode models` no respondió; no se pudo comprobar. |
| `gana el mapa` | Una capa local/repo fija otro modelo; el mapa prevalece. Borra el duplicado. |

## Garantías

- Solo toca `model` y `small_model`.
- Solo en agentes que ya existen en `opencode.json`.
- Antes de escribir guarda `opencode.json.bak-settings-v2-<fecha>` (uno nuevo por cada cambio).
- Escritura atómica (`mv`) y `jq empty` posterior.

## El router

`ai/opencode-router/opencode-router.json` lo genera `ai/opencode-router/build.sh` desde
su template y **no lleva `model`** (lo exigen sus tests). Los modelos de `sdd-explore` y
`sdd-explore-fallback` salen de este mapa vía `opencode.json`.

## Retirado

Los perfiles `~/.config/opencode/profiles/` y `profile-versions/` (peak/offpeak) ya no se
usan. Quedaron archivados en `~/.local/state/opencode-archive/`.

## Rollback a v1

v1 (`scripts/install-opencode-settings.sh` y `../settings/`) no se ha modificado.

1. Restaura la copia deseada: `opencode.json.bak-settings-v2-*` o `opencode.json.bak-modelfix-*`.
2. Ejecuta `scripts/install-opencode-settings.sh`.
3. Reinicia opencode.

v1 no sabe nada de `models.v2.json`, así que ignorarlo es seguro.
