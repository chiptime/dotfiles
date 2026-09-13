# Ajustes top-level de opencode (settings)

Fuente de verdad, dentro de este repo, para las claves **top-level** de
`~/.config/opencode/opencode.json` que queremos reproducibles en cualquier máquina.

Cada fichero `*.fragment.json` es un objeto JSON parcial que se fusiona dentro de la
configuración real mediante `jq`. El fragmento del repo **siempre gana**.

| Fragmento | Claves que fija | Para qué |
| --- | --- | --- |
| `agy-models.fragment.json` | `provider.agy.models` + `provider.agy.options.models` | Catálogo agy materializado (generado — no editar a mano). opencode no consulta el hook `provider.models` del plugin para providers npm, así que el picker y el runtime necesitan esto en config. Regenerable desde `agy models`. |
| `side-tasks.fragment.json` | `small_model` + `agent.summary/compaction.model` | Default CERO-CONFIG para tareas laterales: `agy/default` (universal para usuarios del bridge). Sobrescribir por máquina en el fragmento local. |

## Principio agnóstico: el repo transporta mecanismo, no valores

Los fragmentos del repo **no fijan modelos concretos de terceros** (muse-spark,
glm-flash, tu plan de Zen...): otros usuarios de este repo no tienen los mismos
proveedores ni credenciales. Lo único universal aquí es `agy` — y ni aún eso es
obligatorio para las claves de preferencia.

Los **valores personales** (qué modelo concreto usas para títulos, resúmenes o
compactación) viven en el **fragmento local de la máquina**:

```
~/.config/opencode/settings.local.fragment.json
```

Ese fichero está FUERA del repo (mismo patrón que `shell/private-env.sh` para
secretos) y el instalador lo fusiona EL ÚLTIMO: la máquina gana sobre el repo,
el repo gana sobre la config viva. Ejemplo:

```json
{
  "small_model": "opencode/muse-spark-1.3-contributor-free",
  "agent": {
    "summary":    { "model": "opencode/muse-spark-1.3-contributor-free" },
    "compaction": { "model": "opencode/muse-spark-1.3-contributor-free" }
  }
}
```

### Default cero-config: `agy/default`

El fragmento `side-tasks.fragment.json` fija `small_model` y los agentes
`summary`/`compaction` a **`agy/default`**: cero configuración tras clonar —
cualquier usuario del bridge tiene agy, y con el store v2 las tareas laterales
no corrompen el hilo principal (cada una obtiene su propio binding).

Coste asumido por el default: un spawn de CLI y una conversación de agy por
título/resumen. Si no usas agy, o prefieres otro modelo, SOBREESCRÍBELO en tu
fragmento local (la máquina gana sobre el repo):

```json
{ "small_model": "opencode/muse-spark-1.3-contributor-free" }
```

### Sin pin, ¿qué pasa?

Ya no ocurre en esta setup: el repo SIEMPRE fija un default (cero config).
El "sin pin" solo existe si alguien elimina el fragmento — y entonces opencode
usa el modelo de la sesión para las tareas laterales.

## Por qué no se puede symlinkear el `opencode.json` completo

El fichero real ronda los 185 KB y su rama `agent.*` contiene entradas
`*-fallback` **generadas y reescritas por `gentle-ai sync`**. Si el fichero fuese un
symlink al repo, cada `sync` escribiría generación automática dentro del control de
versiones y produciría ruido y conflictos constantes.

Por eso se aplica el mismo patrón que ya usa `ai/agents/opencode/mcp/`: el repo guarda
solo el trozo que nos pertenece y un instalador lo inyecta con `jq`.

## Cómo añadir un ajuste nuevo

1. Crear `ai/agents/opencode/settings/<nombre>.fragment.json` con un objeto JSON que
   contenga únicamente las claves top-level a fijar. Ejemplo:

   ```json
   { "share": "disabled" }
   ```

2. Si el valor necesita la ruta del usuario, escribir literalmente `$HOME`: el
   instalador lo expande con `sed`, igual que en los fragmentos MCP.
3. Ejecutar el instalador. **No hace falta tocar el script**: fusiona todos los
   `*.fragment.json` del directorio en orden alfabético.
4. Documentar el fragmento en la tabla de arriba.

Regla práctica: nunca editar a mano la misma clave en `opencode.json` y en el
fragmento. El fragmento es el original; `opencode.json` es la copia aplicada.

## Instalación

```bash
~/.dotfiles/scripts/install-opencode-settings.sh
```

Comportamiento:

- **Idempotente**: si la configuración ya coincide con los fragmentos, no reescribe
  el fichero y lo dice (`Sin cambios`). El informe por clave distingue entre
  `añadido`, `cambiado` y `ya correcto`, así que la salida no miente.
- **Seguro**: valida cada fragmento y el resultado fusionado con `jq empty` **antes**
  de reemplazar nada. El fichero destino se sustituye con un `mv` atómico desde un
  temporal del mismo sistema de ficheros; un fallo a mitad nunca deja un
  `opencode.json` corrupto.
- **Backup**: en la primera ejecución que cambie algo guarda el original en
  `opencode.json.bak-settings-<timestamp>` (prefijo propio, para no confundirse con
  backups de otras herramientas).
- No ejecuta `dotbot`, ni `gentle-ai`, ni `opencode`.

Tras aplicarlo por primera vez, reiniciar opencode para que lea los ajustes.

## Después de un `gentle-ai sync`

`gentle-ai sync` reescribe `opencode.json` y puede pisar alguna de estas claves. El
instalador está pensado para eso: **siempre fusiona**, no se rinde al ver la clave
presente, de modo que basta con volver a ejecutarlo para que el repo recupere la
autoridad. Si el valor ya era el correcto, la segunda ejecución no toca el fichero.

## agy-models.fragment.json (generado)

`agy-models.fragment.json` NO se edita a mano: lo genera el bridge desde el
descubrimiento real de `agy models`. opencode no consulta el hook
`provider.models` del plugin para providers npm (verificado 2026-09-11), así
que el catálogo debe materializarse en config — en DOS sitios: el picker
(`provider.agy.models`) y el runtime del adapter (`provider.agy.options.models`).

Regenerar tras cambios en el catálogo de agy:

```bash
cd ~/Code/personal/agy-bridge/packages/opencode-adapter
bun run scripts/export-config-models.ts > /tmp/agy-models.json
# envolver: {"provider":{"agy":{"models": ...,"options":{"models": ...}}}}
# sobrescribir agy-models.fragment.json y luego:
bash ~/.dotfiles/scripts/install-opencode-settings.sh
```

Ojo: el merge con jq es aditivo — si agy ELIMINA un modelo, hay que borrar
la clave vieja a mano en opencode.json (o restaurar desde backup) antes de
reaplicar.
