---
description: Audita tu feedback loop con la IA (cómo comunicas, tus workflows, fricciones) y propone nuevas skills, herramientas CLI y mejoras del sistema basadas en telemetría real
---

Carga primero la skill `improve-system` (`~/.config/opencode/skills/improve-system/SKILL.md`) y su rúbrica (`references/rubric-and-templates.md`), y ejecuta el análisis completo sobre las sesiones recientes.

Argumentos opcionales del usuario: `$ARGUMENTS`
- Si incluye un número de días (ej. `7` o `--days 7`), pasa `--days <N>` a `improve-system --json`.
- Si incluye un nombre de proyecto (ej. `operational-centers`, `ssff-mcp`, `compare-prices`), pasa `--project <nombre>` a `improve-system --json`.
- Si está vacío, usa la ventana incremental desde la última ejecución (o `--days 7` si es la primera vez).
