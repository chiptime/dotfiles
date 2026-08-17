---
description: Procesa correos/chats/notas y desglosa en items de backlog Notion (generico, cualquier proyecto; perfil por proyecto via engram)
---

# Backlog ingest — texto a items Notion (generico)

Tu rol: tomar el TEXTO que el usuario pegara abajo y desglosarlo en items
estructurados en la database de Notion del proyecto ACTUAL.

## Paso 0 — Cargar perfil del proyecto (SIEMPRE, antes de nada)

El perfil (database IDs, esquema, prefijos, asignado por defecto) NO esta en
este prompt: vive en engram, scoped por proyecto.

1. Detecta el proyecto actual (`mem_current_project` o del cwd).
2. `mem_search(query: "tooling/notion-backlog", project: "<proyecto>")`.
3. Si no aparece: repite con `all_projects: true` (el perfil puede vivir en un
   proyecto hermano que comparte la misma database). OJO: si el perfil
   encontrado pertenece a OTRO proyecto, confirme con el usuario que ESTE
   proyecto comparte esa database antes de usarlo. Si no la comparte (o el
   usuario no lo confirma), ve al bootstrap: este proyecto necesita su propio
   perfil.
4. Si aparece: usa `mem_get_observation` para el contenido completo. Ese
   contenido define TODOS los valores concretos (IDs, esquema, prefijos).
5. Si NO existe ningun perfil: ejecuta el bootstrap de abajo.

## Bootstrap (solo primera vez en un proyecto)

1. Pide al usuario la URL de la database de Notion.
2. Extrae el `database_id` (primer UUID de la URL).
3. `notion_API-retrieve-a-database` -> apunta el `data_sources[0].id` moderno.
4. `notion_API-retrieve-a-data-source` -> lista el esquema real (properties,
   opciones de select/multi_select, title).
5. `notion_API-get-users` -> identifica la persona real asignable por defecto.
6. Pregunta al usuario: esquema de prefijos de ID (uno por app/area del
   proyecto), mapeo tipo/prioridad/estado del texto -> opciones reales del
   esquema, y any default assignee.
7. `mem_save` el perfil con topic_key `tooling/notion-backlog` en el proyecto
   actual (capture_prompt: false), para que la proxima vez sea automatico.

## Reglas universales

1. **Una accionable por item.** Si el texto mezcla 3 temas, genera 3 items.
2. **NO inventes contexto tecnico.** Si no esta en el texto o en engram,
   dejalo como "spike pendiente" o pregunta al usuario.
3. **Verifica la app/area correcta** de cada item con el usuario antes de
   crear (especialmente cuando el proyecto tiene varias apps).
4. **Busca SDDs/trabajo previo relacionados en engram** antes de crear.
5. **Continua la numeracion** existente: query-data-source primero, mira los
   IDs ya usados con el prefijo correspondiente.
6. **Usa SOLO las propiedades del esquema real** del perfil. Nunca inventes
   propiedades nuevas sin pedirlo.

## Estructura del cuerpo de cada item (omitir secciones que no apliquen)

1. **Contexto funcional** - que pasa, que deberia pasar.
2. **Sintoma tecnico** - error concreto, controlador, archivo, funcion.
3. **Causa raiz** - si se conoce (identificada / hipotesis).
4. **Casos conocidos** - IDs, DNIs, fechas, ejemplos.
5. **Workaround actual** - si existe.
6. **Proximos pasos / Pasos del spike** - numerado, accionable.
7. **SDDs/trabajo previo relacionados** - nombre + observation ID engram + estado.
8. **Archivos involucrados** - rutas en code block `plain text`.
9. **Origen / Stakeholders** - fuente (correo del DD-MM-YYYY) + personas (rol).
10. **Dependencias** - referencias a otros IDs, bloqueos, jerarquia Epic/Tasks.

## Flujo de trabajo

1. **MODO INTERACTIVO** (por defecto): presenta cada item con preview (tabla
   de props), pregunta lo que falte (asignado, prioridad, contexto tecnico
   adicional), crea via Notion MCP, pasa al siguiente.
2. **MODO BATCH** (si el usuario lo pide): crea todos de golpe tras
   confirmar la lista completa.
3. **Al final** entrega: mapa de dependencias + vista por persona
   (paralelizacion) + resumen con IDs y URLs Notion.

## Notas tecnicas Notion API

- Crear items: `notion_API-post-page` con
  `parent: { data_source_id: "<del perfil>" }`.
- Cada bloque `code` requiere `language` obligatorio.
- Evita rich_text mixto con annotations dentro de bullets/paragraphs; un solo
  text object simple por bloque.
- Jerarquia Epic->Task: `notion_API-patch-page` con
  `properties.<prop_relation>.relation = [{ id: "<page_id_padre>" }]` (el
  nombre de la propiedad de relacion viene en el perfil).
- Listar items y continuar numeracion: `notion_API-query-data-source`.

## Texto a procesar

[EL USUARIO PEGA AQUI EL CORREO / CHAT / NOTA]
