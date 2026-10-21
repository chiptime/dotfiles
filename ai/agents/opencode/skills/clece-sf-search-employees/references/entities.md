# Clece SF tenant map (clecesaT2 TEST, clecesaD DEV)

Distilled from the recruiting backend (`recruiting-backend/main/srv/OperationalCenters/`)
and live tenant observation. Host: `api2.successfactors.eu` (API) /
`api2preview.sapsf.eu` (appears in record URIs). Both work with the same basic auth.

## Identity and state

| Entity | Key | Notes |
|---|---|---|
| `User` | `userId` (= personIdExternal for employees) | `username` = DNI/NIE when real; list endpoint **excludes inactive users** (and has partial visibility even for actives — prefer key reads); `status` is a picklist: `'f'` seen on inactive, `'t'` and `active_external_suite` on active — **the authoritative active/inactive signal is `EmpEmployment.endDate` (null = active)** |
| `PerPerson` | `personIdExternal` | persons incl. candidates (2xxxxxxx range = QA candidates, mostly never hired) |
| `EmpEmployment` | (`userId`, `personIdExternal`) | **the census for terminations** — list includes past employments; `endDate` null = active; rehire creates a new row starting the rehire day |
| `EmpJob` | (`userId`, `seqNumber`, `startDate`) | effective-dated; reach via `EmpEmployment(...)/jobInfoNav`; the LAST row is the baja when `endDate`=9999-12-31 and `event`/`eventReason` set |

Baja modeling: employment ends on day D (`endDate` = D); the baja job row starts D+1
with event `58937`/`58938` and the reason. Rehire events use reasons like `ZR06`.

## eventReason families observed in TEST

- `ZB**` — termination family: `ZB02`, `ZB04`, `ZB27`, `ZB32`, `ZB41`, `ZB46`
- `ZC27` — separate family (ZC)
- `ZR06` — rehire event
- Families are the axis users filter by ("una baja ZB_XX" = any ZB** code)

## Document objects (what "having a document" means)

| Kind tag | Entity | Filter/key | Attachment navs |
|---|---|---|---|
| DN | `cust_Documentos_Nacionales` | `externalCode eq userId` | `cust_attachmentNav` DNI anverso, `1Nav` DNI reverso, `2Nav` NIE, `3Nav` passport, `4Nav` NUSS |
| BANK | `PaymentInformationDetailV3` | `PaymentInformationV3_worker eq userId` (also `iban` field) | `cust_attachmentNav` = bank certificate |
| OTROS | `cust_Otros_Requisitos_Obligatorios` | `externalCode eq userId` | `cust_attachmentNav`…`cust_attachment11Nav` (DARDE, certificates, other docs) |
| COLVUL | `cust_Col_vulnerable_hijo` | `cust_Col_Vulnerable_userId eq userId` | `cust_Adjunto_DiscapacidadNav` |

Rules:

- A document exists only when the nav resolves to an `Attachment` with
  `fileName`/`attachmentId`. Plenty of QA records are empty shells — do not count them.
- Document presence changes fast: the rehire flow itself uploads DN/BANK/OTROS files,
  so attachments appear (and tenants that scanned empty gain data) within hours.
  Re-scan instead of trusting old negatives.
- Composite keys need datetime literals (`PaymentInformationDetailV3` key =
  `PaymentInformationV3_effectiveStartDate` + `PaymentInformationV3_worker` +
  `externalCode`) — unreachable through the MCP; access BANK/COLVUL rows via ordered
  list scans + client-side matching (see SKILL.md Level 3b).
- The app's canonical query catalog lives in
  `IdentificationGetController/identificationReadRepository.ts` +
  `identificationConstants.ts` (operational-centers workspace). Copy new query shapes
  from there instead of inventing them.

## Transport gotchas (all access via ssff MCP tools — no config reads, no direct HTTP)

1. `$filter` of any kind dies: MCP transport turns spaces into `+` (SF rejects), literal
   `%20` gets double-encoded, and there is no spaceless operator form. Filtering is
   done client-side between MCP calls.
2. Key predicates and navigation segments are the workhorses. Single-token `$orderby`
   (ascending, no space) WORKS and makes list pagination deterministic — the base for
   binary-searching a target inside big lists (`PaymentInformationDetailV3` by
   `externalCode`, `cust_Col_vulnerable_hijo` by `cust_Col_Vulnerable_userId`).
3. `%` anywhere in a path is rejected by the MCP path validator, so composite keys with
   datetime literals are unreachable (`PaymentInformationV3_effectiveStartDate`,
   `cust_Col_Vulnerable_effectiveStartDate`, `EmpJob.startDate`) — use list scans +
   client-side matching instead.
4. `User` list excludes inactive users and has partial visibility even for actives;
   `EmpEmployment` list is the termination census but unordered — page with `$skip`
   until fewer than the page size returns.
5. Single-key reads return `d` as an object; list reads return `d.results` as array.
   Batch caps: ≤180 items per `ssff_batch_get`, ≤1000 records total per call.

## Operational hygiene

- Shared QA tenant: data churns by minutes. Timestamp every result; re-verify ids right
  before the user uses them.
- Never let credentials leave `~/.config/ssff-mcp/config.json`; the dotfiles repo must
  stay secret-free (passwords live in `shell/private-env.sh` outside the repo).
- People data: names + document numbers in tables are fine; avoid dumping full records
  or attachment contents.
