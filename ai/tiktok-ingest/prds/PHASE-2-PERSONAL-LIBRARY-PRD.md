# PRD — Phase 2: Personal Knowledge Library

**Status:** DRAFT. Planning documentation only. This PRD records product
requirements; it authorizes no implementation, execution, service operation,
network, browser or GPU use, commit, or push.
**Owner:** Bruno. **Date:** 2026-11-04.
**Decision authority:** the operator decisions recorded in section 15 (D-01..D-12).
They supersede the technology-adoption framing of [PRD.md](../PRD.md) §6.7
(fixed roots) and §6.6 (`fit` with the operator's stack) for this phase.
**Context:** [ROADMAP.md](../ROADMAP.md), [PRD.md](../PRD.md),
[Phase 0 PRD](PHASE-0-ONE-COMMAND-PRD.md), [Phase 1 PRD](PHASE-1-LOCAL-WEB-PRD.md),
[Phase 3 PRD](PHASE-3-DESTINATIONS-PRD.md) (destination adapters, formerly Phase 2).

## 1. Context and problem

The operator keeps very large saved collections (TikTok today, Instagram later)
full of useful everyday knowledge: recipes, remedies, stretches, skincare,
software, places to buy things, methods. Today that knowledge is lost in the
scroll.

The engine already turns one TikTok video into local visual and audio evidence
(`video.md`, `audio.md`), a strict synthesis document, bounded verification and
a `pending` backlog entry. But the product was framed as **technology adoption**:

- Classification accepts only five fixed roots, `tecnología`, `recurso`,
  `educativo`, `consumo`, `ruido` (`src/tiktok_ingest/contracts.py:53`,
  enforced in `synthesis.py:88`). Recipes, remedies and exercises all fall into
  `consumo`, which [PRD.md](../PRD.md) §6.7 defines as "personal apps with no
  stack relevance".
- `fit` is a free-text judgement of "fit with the operator's actual stack"
  ([PRD.md](../PRD.md) §6.6), not with the operator's interests.
- The terminal artifact is `backlog.jsonl`: a review queue, not a library.
  Nothing can be browsed by topic, searched, or handed to Gertru.

Observed state on 2026-11-04 (local state root): 2 collections scanned, 21
inventoried videos, 19 processed, 15 backlog entries (all `accepted`); the
taxonomy holds 13 subgroups across `consumo` (6), `tecnología` (4) and
`educativo` (3).

## 2. Objectives and non-goals

### Objectives

1. Replace the fixed five-root taxonomy with a **three-level interest
   hierarchy**: section → subsection → topic, like the sections of a personal
   interests magazine.
2. Filter by the operator's tastes through an operator-authored **interests
   profile** that drives a relevance score, while the operator's accept/reject
   stays the only gate into the library.
3. Turn every accepted entry into a **library entry**: one Markdown file with
   front-matter, shaped by its kind (recipe, routine, tool, place, method…).
4. Make the library **browsable and searchable** in `ti web` and **exportable**
   as a Markdown tree that Gertru (Hermes) can ingest.
5. **Reclassify** the existing entries using only their stored text evidence.

### Non-goals

- Instagram or any other source (Phase 4, gated by its own research).
- Direct Gertru/Hermes integration (API, tool or memory write); this phase only
  exports files.
- Notion or other external destinations (Phase 3).
- Automatic acceptance, unattended or scheduled runs, multiple users.
- Re-downloading media or re-running vision/transcription to reclassify.
- A learned/trained preference model; relevance comes from the written profile.
- Medical, nutritional or legal validation of content.
- Changes to the GPU, consent, resource-gate or resume guarantees of Phase 0.

## 3. Shared definitions

- **Section**: a broad, stable area of interest (for example `alimentacion`).
  Sections are defined only by the operator.
- **Subsection**: a named shelf inside a section (for example
  `alimentacion/recetas`). Seeded by this PRD; the model may *propose* new ones.
- **Topic**: the dynamic, per-video label inside a subsection (for example
  `recetas/pan-sin-gluten`), normalized with the existing `normalize_subgroup`
  rule (`state.py:332`).
- **Placement**: one `section/subsection/topic` path. Each entry has exactly one
  **primary** placement and zero to three **secondary** placements.
- **Library entry**: the Markdown document generated from an accepted backlog
  entry.
- **Relevance**: a number in 0..1 with a short reason, computed against the
  interests profile. It ranks; it never accepts or rejects.
- The Phase 0/1 definitions (processing outcome vs operator status, accepted is
  not confirmed, a candidate URL is not verified evidence) apply unchanged.

## 4. Taxonomy

### 4.1 Seed hierarchy

Sections and subsections live in `library-taxonomy.json` in the state root. The
seed below is the initial content; the operator may edit it at any time.

| Section | Seed subsections |
|---|---|
| `alimentacion` | `recetas`, `utensilios-cocina`, `superalimentos`, `ingredientes`, `donde-comprar`, `tecnicas-cocina` |
| `salud` | `comida-saludable`, `estiramientos`, `rehabilitacion`, `remedios`, `skincare`, `cuidado-cabello`, `descanso` |
| `deporte` | `rutinas`, `movilidad`, `fuerza`, `cardio`, `material` |
| `tecnologia` | `herramientas-ia`, `desarrollo`, `seguridad`, `apps`, `hardware` |
| `hogar` | `limpieza`, `organizacion`, `bricolaje`, `plantas` |
| `aprendizaje` | `metodos`, `productividad`, `idiomas`, `cursos` |
| `dinero` | `ahorro`, `inversion`, `compras` |
| `viajes-ocio` | `destinos`, `lugares`, `planes` |
| `cultura` | `libros`, `peliculas-series`, `musica` |
| `ruido` | — (never enters the library) |

`ruido` is the only non-library section: motivational content, trends and
engagement bait with no extractable knowledge.

### 4.2 Growth rules

- New **sections** are created only by the operator editing the file.
- A new **subsection** proposed by synthesis is stored as `proposed`; the entry
  keeps the proposal and the web review shows it. Accepting the entry with that
  placement accepts the subsection; rejecting the proposal moves the entry to
  the closest existing subsection the operator picks.
- **Topics** are free and accumulate, normalized, as today's subgroups do.
- Overlap is expected (healthy food belongs to `alimentacion` and `salud`).
  It is solved by secondary placements, never by duplicating entries.

## 5. Interests profile

- `interests.md` in the state root: plain Markdown written by the operator, with
  three headings: `## Me interesa`, `## Me da igual`, `## No quiero`.
- The text of the profile is sent to the configured text API together with the
  sanitized evidence. It is listed in the destinations preview of the synthesis
  consent like any other outgoing text.
- A missing profile is not an error: relevance is then `null` with reason
  `no interests profile`.
- The profile's content hash is part of the synthesis fingerprint, so editing
  it makes affected syntheses stale (re-synthesis is text-only and needs
  consent, section 9).

## 6. Library entry

### 6.1 Kinds and body templates

| `kind` | Required body sections |
|---|---|
| `receta` | Ingredientes, Pasos, Notas |
| `rutina` | Ejercicios (with reps/time when stated), Precauciones |
| `remedio` | Qué es, Cómo se usa, Precauciones |
| `herramienta` | Qué es, Para qué me sirve, Cómo empezar, Enlaces |
| `lugar` | Qué es, Dónde, Enlaces |
| `producto` | Qué es, Dónde comprar, Precio si se menciona |
| `metodo` | Idea, Pasos |
| `nota` | Resumen (fallback when no other kind fits) |

Every entry also has `## Resumen` and `## Fuente`. Content is written in Spanish.
A body section the video does not support is written as `No se menciona en el
vídeo.`, never invented.

### 6.2 Front-matter

```yaml
id: "7678368146386111765"
title: "Strix: escáner de seguridad con agentes"
kind: herramienta
primary: tecnologia/seguridad/escaneo-con-agentes
secondary: []
relevance: { score: 0.8, reason: "..." }
verification: { summary: confirmed, claims: 2 }
health_notice: false
author: "@ivan_agentesia"
source_url: "https://www.tiktok.com/@ivan_agentesia/video/7678368146386111765"
source_platform: tiktok
collection: "<collection key>"
accepted_at: "2026-11-04T10:32:10Z"
synthesis_fingerprint: "<sha256>"
schema: 1
```

### 6.3 Health notice

Every entry whose primary or secondary placement is under `salud`, or whose kind
is `remedio`, carries `health_notice: true` and starts its body with the
sentence `Información de un vídeo, no es consejo médico.` unless every health
claim in it has the verdict `confirmed`.

## 7. Architecture and trust boundaries

```text
evidence (video.md, audio.md) + interests.md
   -> text API (synthesis schema v2)  ──external, consented──
   -> verification (unchanged)
   -> backlog.jsonl (pending)  -> operator accept / reject
   -> accept: library/<section>/<subsection>/<id>-<slug>.md   (source of truth)
            -> library.sqlite (FTS index, derived, rebuildable)
   -> ti web /library (read)       -> ti library export <dir> (copy)
```

- The library is local. Its only writers are the accept action, `reclassify`
  and `reindex`, all under the existing single exclusive writer.
- `library.sqlite` is derived: deleting it loses nothing; `ti library reindex`
  rebuilds it from the Markdown tree.
- Export copies the Markdown tree to an operator-chosen directory outside the
  state root; it never deletes files there and never writes back.
- Reject keeps its permanent blocklist meaning: rejected means "not in my
  library and never ingested again".

## 8. Data schema and state

- **Synthesis schema v2** (`SYNTHESIS_SCHEMA_VERSION = "2"`): replaces
  `classification.root/subgroup` with `primary` and `secondary` placements,
  adds `kind`, `title`, `relevance`, `proposed_subsections` and the kind body
  fields; drops the stack-oriented `fit`. v1 documents remain readable for
  history and are the input of reclassification.
- **Library entry state**:

```text
backlog pending ──accept──> library entry written ──reclassify──> rewritten
        └──────reject──> blocklisted (no library entry)
```

- An accepted entry without a library file (for example, accepted before this
  phase) is reported as `missing_library_entry` and fixed by `reclassify`.

## 9. Security and privacy

- Media, frames, local paths, browser state and credentials never leave the
  machine (unchanged Phase 0 rule).
- The interests profile is the only new outgoing text; it is shown in the
  consent preview and never sent without it.
- Library files and the export contain no local absolute paths and no
  credentials; the existing credential scrub (`synthesis.py:298`) runs on every
  synthesis v2 document.
- Export and web stay loopback/local; no network listener is added.

## 10. Functional requirements

### Taxonomy

- **FR-01** The system MUST read sections and subsections from
  `library-taxonomy.json`, creating it from the seed in §4.1 when absent. (D-01)
- **FR-02** Synthesis MUST reject a placement whose section is not in the file;
  it MUST NOT create sections. (D-01)
- **FR-03** A subsection not in the file MUST be stored as `proposed` and shown
  in review; it becomes part of the taxonomy only when the operator accepts an
  entry with it. (D-01)
- **FR-04** Topics MUST be normalized with `normalize_subgroup`. (D-01)
- **FR-05** Each synthesis MUST have exactly one primary placement and at most
  three secondary placements, none repeating the primary. (D-01)

### Interests and relevance

- **FR-06** Synthesis MUST include the text of `interests.md` when present and
  MUST list it in the consent destinations preview. (D-02)
- **FR-07** Synthesis MUST return `relevance` with a score in 0..1 and a reason,
  or `null` with reason `no interests profile` when the file is absent. (D-02)
- **FR-08** Relevance MUST NOT change an entry's operator status. (D-02)
- **FR-09** The profile content hash MUST be part of the synthesis fingerprint.
  (D-02)

### Library entries

- **FR-10** Accepting a backlog entry MUST write its library Markdown file
  atomically at `library/<section>/<subsection>/<id>-<slug>.md`. (D-04)
- **FR-11** The file MUST contain the front-matter of §6.2 and the body sections
  of its kind (§6.1); unsupported sections MUST read `No se menciona en el
  vídeo.` (D-09)
- **FR-12** Entries under `salud` or of kind `remedio` MUST carry the health
  notice of §6.3. (D-03)
- **FR-13** Rejecting MUST keep the current blocklist behaviour and MUST NOT
  write a library file. (D-02)
- **FR-14** Writing the same accepted entry twice MUST produce identical bytes.
  (D-04)

### Index, web and export

- **FR-15** `ti library reindex` MUST rebuild `library.sqlite` from the Markdown
  tree alone. (D-04)
- **FR-16** `ti web` MUST add a Library view: sections → subsections → entries,
  showing secondary placements, plus full-text search over title, summary and
  body. (D-04)
- **FR-17** `ti library export <dir>` MUST copy the Markdown tree to `<dir>`,
  MUST refuse a `<dir>` inside the state root, and MUST NOT delete files in
  `<dir>`. (D-05)

### Reclassification

- **FR-18** `ti library reclassify` MUST re-run only text synthesis (v2) for
  accepted entries whose synthesis is v1, missing a library file, or stale
  because the profile changed. (D-08)
- **FR-19** Reclassification MUST perform zero media downloads, zero vision
  inference and zero transcription. (D-08)
- **FR-20** Reclassification MUST ask for synthesis consent once per invocation,
  showing the entry count and the text API destination. (D-08)
- **FR-21** A reclassified entry MUST keep its `accepted` status; its new
  placement MUST be shown for review in the web Library view as `reclassified`
  until the operator confirms or moves it. (D-08)

### Documentation

- **FR-22** README, OPERATIONS and `ti --help` MUST document every new command,
  file and view. (D-10)

## 11. Verification and acceptance

### 11.1 Levels

| Level | What | Trigger |
|---|---|---|
| V1 — Automated tests | Unit tests with fakes for every FR: taxonomy validation, schema v2, relevance, entry rendering, health notice, index rebuild, export guards, reclassify zero-media invariant | Every change under `src/tiktok_ingest/` |
| V2 — Scratch-state smoke | A versioned script runs `reclassify` (fake text API), `reindex`, `export` and the web Library view against a `mktemp -d` copy of a fixture state root, and asserts files, index rows and HTTP responses | Same as V1 |
| V3 — Manual UAT | The operator reclassifies the real 15 entries, browses the library by section, searches, edits `interests.md`, and exports to a scratch directory | Once, before closing the phase |

### 11.2 Acceptance scenarios

```gherkin
Scenario: Accepting an entry writes a library file (FR-10, FR-11) [V1+V2]
  Given a pending backlog entry with a v2 synthesis of kind receta
  When the operator accepts it
  Then library/alimentacion/recetas/<id>-<slug>.md exists
  And it has the §6.2 front-matter and the Ingredientes, Pasos, Notas sections

Scenario: Unknown section is refused (FR-02) [V1]
  Given a synthesis whose primary placement starts with "astrologia"
  When the document is validated
  Then validation fails and no backlog entry is written

Scenario: Proposed subsection waits for the operator (FR-03) [V1+V3]
  Given a synthesis proposing alimentacion/fermentados
  When the entry is shown in review
  Then the subsection is marked proposed
  And it enters library-taxonomy.json only after the entry is accepted

Scenario: Health notice (FR-12) [V1]
  Given an accepted entry of kind remedio with an unverified claim
  Then its body starts with "Información de un vídeo, no es consejo médico."

Scenario: Reclassify never touches media (FR-18, FR-19) [V1+V2+V3]
  Given 15 accepted v1 entries with stored video.md and audio.md
  When the operator runs ti library reclassify and grants consent
  Then 15 library files exist with v2 placements
  And no download, vision or transcription call was made

Scenario: Index is derived (FR-15) [V1+V2]
  Given a library tree and a deleted library.sqlite
  When ti library reindex runs
  Then searching a word from any entry body returns that entry

Scenario: Export is append-only and outside the state root (FR-17) [V1+V2]
  Given an export directory with an unrelated file
  When ti library export runs twice
  Then the unrelated file still exists and the tree matches the library
  And exporting into the state root is refused
```

### 11.3 Traceability

| FR | V1 | V2 | V3 |
|---|---|---|---|
| FR-01..FR-05 | ✔ | | ✔ |
| FR-06..FR-09 | ✔ | | ✔ |
| FR-10..FR-14 | ✔ | ✔ | ✔ |
| FR-15..FR-17 | ✔ | ✔ | ✔ |
| FR-18..FR-21 | ✔ | ✔ | ✔ |
| FR-22 | | | ✔ |

### 11.4 Success metrics

- The real 15 accepted entries are browsable by section with zero media work.
- The operator finds a known recipe or remedy by search in under 30 seconds.
- Gertru can read the exported tree with no format conversion.

## 12. Entry conditions

- Phase 0 checklist items 7 and 8 (live text API synthesis and verification)
  need live evidence before reclassifying real entries; V1 and V2 do not.
- The uncommitted `feat/tiktok-web-ux` work is committed or set aside before
  the Library view is added to the same SPA.

## 13. Risks and open questions

- **ASSUMPTION:** the configured text API model produces Spanish kind bodies
  reliably within the existing 100 000-character evidence bound
  (`synthesis_api.py:54`); measure on the 15 real entries.
- **ASSUMPTION:** Hermes can ingest a Markdown tree with YAML front-matter;
  confirm against the Hermes/Gertru workspace before closing V3.
- Throughput, not taxonomy, limits library growth on very large collections;
  it is tracked in the roadmap, not here.

## 14. Execution boundaries and stop gate

1. Implementation is limited to FR-01..FR-22. No unrequested features,
   optimizations or refactors.
2. Blocking bugs, regressions or gaps outside this PRD go under
   `## Escalated / Out-of-Scope Issues` in the feature document, and work on
   them stops until the operator decides.
3. When V1 and V2 pass, the executor reports a three-line summary and stops; V3
   is the operator's.

## 15. Decision ledger

| ID | Decision (operator, 2026-11-04) |
|---|---|
| D-01 | Generic sections like a personal interests page, with subsections below (for example `alimentacion` → recetas, utensilios de cocina, superalimentos, ingredientes, sitios donde comprar; `salud` → comida saludable, estiramientos, rehabilitación, skincare, cuidado del cabello). Dynamic topics stay. |
| D-02 | "Filtered by me" = an editable interests profile scoring relevance + the operator's accept/reject as the only gate. |
| D-03 | Keep primary-source verification when candidate URLs exist; health content is marked unverified / not medical advice unless confirmed. |
| D-04 | Library = one Markdown file per entry with front-matter + a SQLite full-text index, local, with a web view by section. |
| D-05 | Gertru/Hermes: Markdown folder export only in this phase. |
| D-06 | This phase is Phase 2 — Personal knowledge library; destination adapters move to Phase 3. |
| D-07 | Instagram is Phase 4, gated by its own research; out of scope here. |
| D-08 | Reclassify the existing entries with text-only re-synthesis, no media re-processing. |
| D-09 | Entry content: summary, kind-specific actionable content, sources, original link, author, tags, relevance. |
| D-10 | PRD in English; library content in Spanish. |
| D-11 | Decision taken by the agent within D-01: one primary placement plus up to three secondary placements solve overlaps such as healthy food. |
| D-12 | Decision taken by the agent within D-01: sections only by the operator; model-proposed subsections wait for the operator's acceptance. |
