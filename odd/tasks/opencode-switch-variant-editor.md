# OpenCode switch variant editor

## Objective
Edit each agent's model and variant pair within its own OpenCode switch profile.

## Problem and rationale
The initial editor used a global variant per agent. The user clarified that each agent must store its own model and variant within each profile; another profile must not change it.

## Authorized scope and constraints
- Update scripts/opencode-switch-profile.sh, scripts/install-opencode-settings-v2.sh and their two regression scripts.
- Migrate the eight models.*.json maps and remove the shared gentle-variants.fragment.json override after carrying its current values into the profiles.
- Document usage in ai/agents/opencode/models/README.md, preserving existing edits.
- Each agent stores {model, variant} in its profile. Same model may use different variants by agent or profile. Accept legacy string entries as model with default variant; never leak variants from previous profiles or fragments.
- Model-only replacement resets variant to default unless both are explicitly selected. Preserve current variants during the initial migration, without claiming model compatibility validation.
- Preserve unrelated dirty/untracked work; do not run the real installer, stage, commit, or publish.
- No per-model compatibility guarantee; communicate supported-value uncertainty.
- RDD is off (clone_local); use functional checks and independent verification.

## Tasks
- [x] V3: Reopen implementation for profile-local pairs: migrate data, installer/editor and regression tests. Route: delegated; coordinated multi-file change. Eight maps migrated with model/metadata snapshots preserved; global variant fragment removed after purity check. Writer observed RED (54 switch failures, 35 installer failures), then GREEN (146 switch assertions, 70 installer checks); parent confirmed installer checks and four-script syntax. V1/V2 evidence below describes the historical global implementation, not acceptance of the new scope.
- [x] V4: Independently verify isolation, legacy/default behavior, combined edits and installer idempotence; update usage. Route: delegated; unknown native risk cannot lower verification. Independent snapshot check confirms 8 maps/276 model+variant pairs and metadata intact. Verifier found typed-model acceptance gap; correction adds shape and exact catalogue validation before writes, cancels without catalogue. Writer observed RED then GREEN; final writer/verifier/parent checks: 175 editor assertions, 70 installer checks, four-script syntax PASS.
- [x] V1: Add model/variant/both editing, safe argument transport, and explicit default/keep semantics. Route: delegated; multi-file behavior and tests require a writer. Writer observed baseline RED (29 failing assertions), then GREEN (68 passing assertions); parent reran all 68 successfully and bash syntax check passed.
- [x] V2: Verify sandbox flows and document usage. Route: delegated verification; independent confirmation required if native assessment is unassessable. Independent verifier found inherited-agent validation bug; one bounded correction replaced `in` with `Object.hasOwn`. Two new regressions observed RED (6 failures) then GREEN (80 passes). Verifier and parent confirmed 80/80 plus syntax after correction; README usage documented.

## Acceptance criteria and checks
- Variant-only editing does not query or change models.
- Default clears the explicit variant; keep/cancel preserves files. Editing an inactive profile does not change the active profile unless explicitly applied.
- Invalid input does not write or apply anything.
- Show selected model and profile-local variant; warn model support varies, never label variants global.
- Sandbox installer observes variants; real user configuration remains untouched by tests.
- Existing model-only profile application behavior remains intact.
- Run bash scripts/test-opencode-switch-edit-variant.sh, bash scripts/test-install-opencode-settings-v2.sh, and bash -n on all four scripts. Tests must isolate settings fixtures so current fallback fragments cannot alter synthetic prompt expectations.
- Observe deterministic RED before implementation, GREEN afterwards.

## Delivery and progress
Strategy: ask-on-risk. Initial estimate 200–300 lines; profile-local correction estimated 300–600 more. Writer reports approximately 1270 authored lines including durable migration snapshot tests, plus generated map migration. Advisory size exceeded naturally; tests remain with behavior. No commit or PR planned.
No commits authorized. RDD off; native assessment unassessable because of pre-existing untracked inventory, so independent verification required.
Current required checks pass: 175 editor assertions, 70 installer checks and four-script syntax, confirmed independently with parent spot checks. Profile isolation, default/keep/cancel, model-change variant reset, invalid/prototype names, invalid/unknown models and unavailable catalogue covered. Prior installer prompt-fixture failure resolved by hermetic settings fixtures.
Skipped: interactive fzf pseudo-TTY proof (automation probes did not stabilize; no interactive PASS claimed). Provider-specific variant compatibility validation is not implemented and not claimed.
No live configuration regeneration or commits performed. The live command symlink resolves to the modified script.
Scope correction complete: model and variant are stored in each agent's profile entry; old shared fragment removed. Legacy strings/default pairs clear stale variants on application. Same-model no-op preserves variant; a changed model resets it unless explicitly selected.
Next step: user can view pairs in opencode-switch edit <profile>, edit variante/ambos and choose whether to apply. opencode-switch status displays live applied configuration; restart OpenCode after applying. Manual interactive fzf smoke test remains useful.
