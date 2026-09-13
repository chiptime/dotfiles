---
name: branch-audit
description: Audit local git branches of the current repository with last-commit metadata, a merged-status matrix against base targets, semantic branch summaries, Notion ticket linking, and cleanup suggestions. Trigger: branch audit, auditoría de ramas, estado de ramas, qué ramas hay, ramas mergeadas, branch status, merged status.
---

# Branch Audit

Audit the local branches of the current git repository: last-commit metadata,
a merged-status matrix against base targets, optional semantic summaries, and
Notion ticket linking. Backed by the `branch-audit` CLI (installed on PATH by
the dotfiles, zero runtime dependencies).

## 1. Run the audit

From the target repository (never from an unrelated cwd), run:

```bash
branch-audit --json
```

The JSON contains `repo`, `current` (checked-out branch or null), `targets`
(base branches that exist in the repo), and `branches[]` with `name`,
`isCurrent`, `subject`, `author`, `date`, `dateRelative`, `upstream`,
`ahead`, `behind`, and `merged` (a map of target -> boolean).

If the default targets do not fit the repository, override them:

```bash
branch-audit --targets master,main,deploy-develop --json
```

## 2. Present the results

Render a compact table for the user: branch, last commit (relative), subject,
and a merged `✓` / `·` cell per target. Flag unmerged branches younger than
2 weeks (`date` within the last 14 days AND not merged into some target) as
likely active work — do not suggest deleting those.

## 3. Semantic layer (optional, cheap)

For branches whose name or subject is unclear, read what they actually change:

```bash
git log --oneline <target>..<branch> | head -20
```

Produce a one-line natural-language summary of what the branch does and attach
it to the table row. Do NOT launch sub-agents for this; direct git reads are
enough.

## 4. Notion layer (ticket linking + branch fields)

Extract likely ticket tokens from branch names and commit subjects: JIRA-style
patterns (e.g. `ABC-123`), or the project's own ID prefixes (OC-xx/AS-xx/EP-xx/XS-xx).
Branch names usually carry NO ticket token, so match by querying the database
and comparing commit subjects against ticket titles semantically. Then:

1. Load the project's Notion backlog profile from Engram before touching
   Notion: `mem_search(query: "tooling/notion-backlog", project: "<project>")`,
   then `mem_get_observation` for the full profile. It defines the database
   (modern data_source_id), schema, ID prefixes, and the next free IDs —
   never hardcode or guess them.
2. If no profile exists for this project, retry with `all_projects: true`
   (sibling projects may share the database). If the profile belongs to a
   different project, confirm with the user before reusing it; otherwise skip
   this layer and say so.
3. Ensure the `Rama backend` and `Rama frontend` properties exist (rich_text).
   If missing, create them with `notion_API-update-a-data-source`. When one
   field holds several branches, write them comma-separated.
4. Match each branch to at most one ticket. On match: fill ONLY the empty
   branch field(s) of that ticket page — read the page first, never overwrite
   populated values; appending a second branch to a filled field requires
   user approval. On no match: create a card using the next free ID from the
   profile and its conventions (Type/Priority/Status mappings), then fill
   both branch fields.
5. Card body — structured so anyone understands the task without reading the
   diff. One Notion heading per section, in this order:
   - `Funcionalidad` — what the task does, in functional (not technical) language.
   - `Cambios por repo` — backend / frontend bullets; write "sin cambios"
     when a repo is not involved.
   - `Commits representativos` — 3-5 key commit subjects with short hash.
     Never cite diff shortstats against a base: they reflect merge-base drift,
     not the branch's own work.
   - `Estado` — branch names, merged-matrix result, last activity date.
   - `Relaciones` — related tickets (mark uncertain matches as "probable")
     or "—".
   For pre-existing tickets, APPEND this under a dated heading via
   `notion_API-patch-block-children` (convention: prefix "🔁"); NEVER replace
   existing card content with `replace_content`.
6. Do NOT create cards for infrastructure refs (permanent branches, worksets,
   base work lines) or fully merged stale refs — list them as "no ticketable"
   instead.
7. Link each branch to at most one ticket. NEVER invent or force a ticket
   link; if no plausible match is found, say "no ticket found".

## 5. Cleanup suggestion (suggest only, never delete)

Branches merged into ALL targets are deletion candidates. List them with the
suggested command:

```bash
git branch -d <branch>
```

Only suggest. Never run any branch deletion without the user's explicit
approval. If `git branch -d` refuses a branch, git considers it unmerged —
do not escalate to `-D`.

## Rules

- Run `branch-audit` inside the repository under discussion; its exit code 1
  means "not a git repository" — do not improvise around it.
- The merged matrix reflects LOCAL branch state only; it says nothing about
  remote-only branches.
- Keep the presented table compact; sort follows the CLI (most recent first).
- All deletions require explicit user approval. Within an approved
  audit-and-link run, creating cards and filling EMPTY `Rama backend` /
  `Rama frontend` fields is allowed; overwriting populated fields, clearing
  values, or replacing existing card bodies always needs explicit user
  approval.
