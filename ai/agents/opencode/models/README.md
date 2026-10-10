# Modelos de opencode (v2)

Fuente única de verdad de **qué modelo usa cada agente** de opencode. Vive en el repo
y se aplica a `~/.config/opencode/opencode.json` con `scripts/install-opencode-settings-v2.sh`.

> Esto relaja a propósito el principio de `../settings/README.md` (no fijar modelos
> concretos en el repo): este repo es personal y aquí la reproducibilidad pesa más.

## Ficheros y Perfiles

| Fichero | Papel |
| --- | --- |
| `models.v2.json` | El mapa activo. Es sobreescrito por el selector de perfiles o editado a mano. |
| `models.default.json` | Preset Default (`opencode-switch default`): GLM-5.3-flash, GLM-5.3 (max), Sol (Verify), GLM-5.3-flash (Explore). |
| `models.claude.json` | Preset Anthropic Directa (`opencode-switch claude`): Sonnet 5.5 en Worker/Verify, Opus en Riesgo. |
| `models.openai.json` | Preset ChatGPT Plus (`opencode-switch openai`): GPT-6.1 Sol en Worker, Verify y Reviewers. También es el destino de `opencode-switch -f default` (fallback). |
| `models.opencode-go.json` | Preset OpenCode Go (`opencode-switch opencode-go`): Qwen 3.8 Max en Worker/Verify. |
| `models.zai.json` | Preset Z.ai Max (`opencode-switch zai`): AGY en Explore/Readability, resto como default. |
| `models.subs.json` | Preset Suscripciones Activas (Anthropic + ChatGPT Plus + AGY + OpenCode-Go). |
| `models.subs-openai.json` | Preset Suscripciones sin Claude (fallback OpenAI Sol). |
| `../../../../scripts/opencode-switch-profile.sh` | Conmutador rápido (`opencode-switch [--fallback/-f] <perfil>`). |
| `../../../../scripts/install-opencode-settings-v2.sh` | Aplica el mapa activo sobre `opencode.json`. |

Claves de `models.v2.json` (y sus presets):

- `small_model`: modelo para tareas laterales (títulos, etc.).
- `agents`: `nombre-de-agente -> {"model": "provider/id", "variant": "..."}`. El par
  es **propio de cada perfil**: el mismo modelo puede usar variantes distintas por
  agente y por perfil, y ningún perfil filtra su variante a otro. `variant: ""`
  significa default explícito. Se aceptan entradas legacy (`"provider/id"` como
  string), equivalentes a par con variante default — el instalador y el editor las
  tratan igual, pero los perfiles migrados ya están en formato par.
- `inherit`: agentes que **a propósito** no fijan modelo (heredan el de la sesión).

Las variantes viven DENTRO de cada mapa de perfil (clave `variant` del par). El
antiguo `ai/agents/opencode/settings/gentle-variants.fragment.json` (variante global
compartida) se retiró: sus valores se migraron a los ocho perfiles y ya no existe.

## Cambiar un modelo

1. Edita `agents.<agente>.model` en `models.v2.json` (o usa `opencode-switch edit`;
   al cambiar solo el modelo, la variante del par se resetea a default).
2. `scripts/install-opencode-settings-v2.sh`
3. Reinicia opencode.

No edites `agent.*.model` a mano en `opencode.json`: es la copia aplicada y la próxima
ejecución del instalador la sobrescribe con el mapa.

## Editar modelo o variante (`opencode-switch edit`)

`opencode-switch edit [perfil]` (o la tecla `e` del menú) abre el editor: eliges
subagente (la lista muestra modelo **y** variante del par en ese perfil) y luego la
acción — **modelo**, **variante** o **ambos**. El cambio solo se escribe cuando todo
el input es válido; cancelar (o fallar una validación) en cualquier paso no toca
nada. Editar solo variante no consulta `opencode models` ni toca el modelo del par.

| Concepto | Comportamiento |
| --- | --- |
| Valores de variante | Solo `low` `medium` `high` `max` `default` `keep`. |
| `default` | Guarda variante vacía (`""`) en el par: limpia el override explícito. |
| `keep` | Conserva la variante **solo si el modelo no cambia**. Con modelo distinto, la variante anterior no se reutiliza: queda en default y se avisa. |
| Solo modelo | Cambiar el modelo resetea la variante a default (nunca reutiliza la del modelo anterior). Re-elegir el mismo modelo es un no-op que conserva el par. |
| Alcance | El par `model`+`variant` es **propio del perfil editado**; los demás perfiles no cambian. |
| Compatibilidad | No garantizada y no validada contra el catálogo: cada modelo soporta un subconjunto de valores distinto. |
| Aplicar | Perfil activo: el instalador se ejecuta una sola vez tras escribir. Perfil inactivo (modelo, variante o ambos): pregunta «¿aplicar perfil ahora?»; si lo rechazas, ni instalador ni cambios en el activo. Los no-ops (keep/cancel/mismos valores) no invocan el instalador. |
| Reinicio | Los cambios en `opencode.json` requieren reiniciar opencode. |

Para ver los valores aplicados ahora mismo: `opencode-switch status` (muestra
modelos y variantes de la config viva). Para ver el par de un perfil concreto,
abre `opencode-switch edit <perfil>` y observa la lista, o lee directamente el
`models.<perfil>.json`.

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

- Para cada agente mapeado solo sobrescribe `model` y `variant` (nunca un spread
  del objeto del mapa: `prompt`/`permission` de la config viva se conservan), y
  además `small_model`. Para agentes mapeados, la variante del par/default del
  mapa SIEMPRE gana a la que fije cualquier fragmento.
- Solo en agentes que ya existen en `opencode.json` (nunca crea agentes fantasma).
- Valida la forma del mapa (pares `{model, variant}` o strings legacy) y aborta
  **antes** de escribir si algo no encaja.
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

v1 no sabe nada de `models.v2.json`, así que ignorarlo es seguro. Ojo: v1 tampoco
conoce las variantes por perfil (el antiguo fragmento global `gentle-variants.fragment.json`
ya no existe), así que un rollback a v1 deja cada agente con la variante que pongan
sus fragmentos v1 — sin overrides migrados.
