---
name: clece-sf-search-employees
description: Search Clece employees in SAP SuccessFactors tenants (clecesaT2 TEST, clecesaD DEV) through the ssff MCP — active/inactive workers, terminations by eventReason (ZB02, ZB04, ZC27, ZR06...), document attachments (national ID, bank certificate, DARDE, disability), rehire-case hunting. Use whenever the user asks to "find cases/employees" in SF/SuccessFactors/Clece, needs inactive employees with a specific baja reason, wants people with documents attached, asks for rehire/recontratación test data, or mentions clecesa tenants — even if they don't name the environment. Also use when an ssff MCP query fails with filter encoding errors: this skill holds the MCP-only workarounds.
---

# Clece SF Employee Search (via ssff MCP)

Progressive lookup workflow over Clece's SuccessFactors OData v2, executed **exclusively
through the `ssff` MCP tools** (`ssff_query_entity`, `ssff_batch_get`). Never read the
MCP's config, never call the SF API directly: the MCP owns authentication, sanitization
and entity access. Go as deep as the question requires.

## The one constraint that shapes everything

The MCP transport mangles OData option values containing **spaces** (they reach SF as
`+`, which SF rejects), and its path validator rejects `%` (so no encoded `datetime`
key literals). Everything else works. Therefore:

| Allowed | Broken |
|---|---|
| Key predicates: `EmpEmployment(personIdExternal='X',userId='X')` | `$filter` of any kind |
| Navigation segments: `.../jobInfoNav` | `$orderby` with two tokens (`field desc`) |
| `$expand` lists (commas, no spaces) | datetime literals in key paths |
| Single-token `$orderby` (`externalCode` — ascending) | `%` anywhere in the path |

Design every query as key reads, navigation segments, unordered/single-token-ordered
lists paged with `skip`, and client-side filtering done by YOU between MCP calls.

## Level 1 — Direct lookup (one person)

One `ssff_batch_get` (environment `TEST_basic`; `DEV_basic` for dev) with key reads:

- `User('1176312')` — name, `username` (= DNI when real), `status`
- `EmpEmployment(personIdExternal='X',userId='X')` — with select
  `userId,personIdExternal,startDate,endDate`
- `cust_Documentos_Nacionales('X')` and `cust_Otros_Requisitos_Obligatorios('X')` with
  the attachment expands (see entities reference) and `select: "externalCode"` to keep
  payloads small

Single-key reads return the record object directly; list reads return arrays — handle
both when parsing.

## Level 2 — Employment state and baja

- **Inactive** = `EmpEmployment.endDate` in the past (authoritative signal).
  `User.status` is a picklist (`'f'` inactive; `'t'`, `active_external_suite`, …
  active) — use it as a hint, never as the source of truth.
- The **baja** is the person's LAST `EmpJob` row via
  `EmpEmployment(personIdExternal='X',userId='X')/jobInfoNav`: `startDate` = day after
  the employment `endDate`, `endDate` = 9999-12-31, `event`/`eventReason` populated
  (e.g. `58937` + `ZB27`). Rehire creates a NEW employment row starting the rehire day
  (reasons like `ZR06`) — a rehired person is no longer a baja case.
- Reason families seen in TEST: `ZB02`, `ZB04`, `ZB27`, `ZB32`, `ZB41`, `ZB46` (ZB),
  `ZC27` (ZC), `ZR06` (rehire).

## Level 3 (max) — Filtered hunts

### 3a. Inactive employees by baja reason

1. **Census**: page `EmpEmployment` (list endpoint includes terminated people, unlike
   `User`): `select userId,personIdExternal,startDate,endDate`, `top 1000`, `skip`
   0/1000/2000… until a page returns fewer rows. Keep rows with `endDate` in the past.
2. **Reasons**: one `ssff_batch_get` per ≤90 candidates with
   `EmpEmployment(...)/jobInfoNav` key reads (the 1000-records-total batch cap — QA
   people carry few rows each; 90 keeps you safely under it). Take each person's LAST
   row and filter by reason prefix client-side.
3. Present: userId, name (from a second small batch of `User` key reads), DNI, endDate,
   reason — with the verification timestamp.

### 3b. Document attachments

- **DN / OTROS**: key reads with attachment expands (Level 1). A document "exists" only
  when the nav resolves with `fileName`/`attachmentId` — QA shells without files don't
  count.
- **BANK** (`PaymentInformationDetailV3`, attachment `cust_attachmentNav`, composite
  datetime key = unreachable): the list is binary-searchable — query
  `orderby: "externalCode"`, `select externalCode,PaymentInformationV3_worker`,
  `top 3`, jumping `skip` until you bracket the target `worker` (the code correlates
  with personIdExternal). Then re-read that small skip range WITH
  `expand: "cust_attachmentNav"` and inspect the person's rows.
- **COLVUL** (`cust_Col_vulnerable_hijo`): same trick with
  `orderby: "cust_Col_Vulnerable_userId"` (list carries the person in
  `cust_Col_Vulnerable_userId`); binary-search to the person's block, then expand
  `cust_Adjunto_DiscapacidadNav`.
- Several unknowns at once? Do the ordered scans once, build the person→attachment map,
  and intersect with the whole candidate list — cheaper than per-person searches.

### Reference for new query shapes

When working inside an operational-centers workspace, copy query shapes from the app's
own catalog before inventing them:
`backend/srv/OperationalCenters/HiringRequest/controllers/IdentificationGetController/identificationReadRepository.ts`
(+ `identificationConstants.ts`). The `references/entities.md` of this skill holds the
distilled entity map, document matrix and tenant semantics.

## Rules that keep this honest

- **Never read the MCP config or handle credentials.** The MCP owns auth; your job is
  composing entity paths, paging, and interpreting results.
- **Live churn**: TEST is a shared QA tenant — cases get rehired/modified while you
  work. Timestamp every result and re-verify the exact ids (Level 1) right before the
  user acts on them. Never present a stale pool as free.
- **People data is sensitive**: names and document numbers in operator-facing tables are
  fine; don't dump full records or attachment contents into reports.
- **Report negatives honestly** ("no bank-certificate attachments exist as of
  <timestamp>") instead of implying data might be there — and remember presence changes
  fast, so re-scan rather than trusting old negatives.
- Batch discipline: ≤180 items per `ssff_batch_get`, ≤1000 records total per call; put
  per-item failures in the report instead of retrying blind.

