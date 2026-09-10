---
name: notion-personal-backlog
description: "Trigger: pendiente personal, anotar tarea, todo personal, backlog personal, apunta esto, cross-project todo. Capture a personal or cross-project pending item into the dedicated personal Notion database, never the Clece SIS backlog."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: notion-personal-backlog

## Activation Contract

Load when the user (or Gertru) wants to capture a personal or cross-project pending item that is NOT Clece SIS work (operational-centers, associations, entities-portal, entities-portal-ep-comments). SIS items go through `/notion-backlog` instead.

## Hard Rules

- NEVER write to a Mantenimiento SIS data source (`2345b76b-8c24-409e-a728-6074d38acefd`, `797cb00f-e8dc-4762-be4c-6bd8c43f64d4`). If the item is Clece SIS work, redirect to `/notion-backlog` and stop.
- One capture = one actionable item. Never merge multiple todos into one page.
- Only use properties defined in `assets/notion-personal-backlog-schema.md`. Never invent new properties or Proyecto options on the fly; ask the user to add a new option first.
- Never guess the target data source ID. Resolve it via Bootstrap before creating anything.

## Decision Gates

| State | Action |
|---|---|
| Profile found in memory | Use its `data_source_id` directly, skip to Execution Step 3 |
| No profile, active Notion integration only sees pages parented in Mantenimiento SIS data sources | STOP — tell the user to run this from Gertru (it holds its own Notion integration for personal use), or to share a personal parent page with the currently active integration first |
| No profile, active integration has a usable non-SIS parent page | Create the database per schema, then save the profile |

## Execution Steps

1. `mem_search(query: "tooling/notion-personal-backlog", scope: "personal", all_projects: true)`. If found, `mem_get_observation` for the `data_source_id` and schema options, then go to step 4.
2. Not found: probe access with `notion_API-post-search` filtered to `object: page`. If every result parents into a Mantenimiento SIS data source, apply the STOP row above and do not create anything.
3. Otherwise, ask the user which existing page should hold the database (or accept a pasted URL), then create it with `notion_API-create-a-data-source` using the schema in `assets/notion-personal-backlog-schema.md`. `mem_save` the profile with `scope: "personal"`, `topic_key: "tooling/notion-personal-backlog"`, `capture_prompt: false`, including the new `data_source_id`.
4. Ask the user only for fields you cannot infer from their text (usually Proyecto, Prioridad). Estado defaults to `📥 Inbox`.
5. Create the item with `notion_API-post-page`, `parent: { data_source_id }`, and the resolved properties.
6. Confirm success with the created page URL and the properties you set.

## Output Contract

Return the created page URL and the exact property values set. On STOP, return the reason and the two remediation options from the Decision Gates table — never fall back to writing into Mantenimiento SIS.

## References

- `assets/notion-personal-backlog-schema.md` — property names, select options, and (once bootstrapped) the live `data_source_id`.
