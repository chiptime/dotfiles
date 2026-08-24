#!/bin/sh
# Deterministic agy stub for integration tests. Behavior via AGY_STUB_MODE.
# Writes a canonical exploration.md into its cwd (the CLI workdir), like agy.
set -u
MODE="${AGY_STUB_MODE:-success}"
touch stub-invoked.marker
ART='## Exploration: stub
### Current State
Stub exploration run.
### Affected Areas
src/agy/
### Approaches
Stub approach.
### Recommendation
Stub recommendation.
### Risks
None.
### Ready for Proposal
Yes.'
case "$MODE" in
	success) printf '%s\n' "$ART" > exploration.md; echo "stub ok"; exit 0;;
	empty) echo "stub finished without writing the artifact"; exit 0;;
	auth) echo "authentication required: captcha challenge, please sign in" >&2; exit 1;;
	quota) echo "429 quota exceeded: RESOURCE_EXHAUSTED" >&2; exit 1;;
	timeout) sleep 30;;
	mutate) printf '%s\n' "$ART" > exploration.md; : > "${AGY_STUB_MUTATE_REPO:?}/mutated-by-stub.txt"; echo "stub ok"; exit 0;;
	*) echo "unknown stub mode: $MODE" >&2; exit 2;;
esac
