# Ajustes top-level de opencode (settings)

Fuente de verdad, dentro de este repo, para las claves **top-level** de
`~/.config/opencode/opencode.json` que queremos reproducibles en cualquier máquina.

Cada fichero `*.fragment.json` es un objeto JSON parcial que se fusiona dentro de la
configuración real mediante `jq`. El fragmento del repo **siempre gana**.

| Fragmento | Clave que fija | Para qué |
| --- | --- | --- |
| `small_model.fragment.json` | `small_model` | Fija el modelo usado en tareas auxiliares (generación de títulos). Sin esta clave, opencode usa el modelo de la sesión: con el proveedor `agy` eso lanza un proceso de la CLI y quema una conversación desechable en cada título. |

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
