# Launch Request — <product/feature name>

> Fill ONLY what is resolved, from established facts (scope, paths, policy, budgets) —
> never product guesses. Genuinely unknown fields stay visibly incomplete and block;
> known fields are filled as-is, without inventing extra metadata the user never
> provided. A launch request with unresolved placeholders is NOT launchable; saying so
> is required, not optional.

## Launch authorization

This template records what is being launched; it grants nothing and starts nothing.
Human consent happens in the future, when the user intentionally pastes explicit
authorization text into the launching session's prompt, for example: "With this request
I approve the pack described above and authorize local implementation within its stated
scope and budgets." The planner never invents that consent, never fills names or dates
on the user's behalf, and never treats PRD approval as implementation authorization.

## Target

- Repository root (canonical; fork status confirmed): {{REPO_ROOT}}
- Pack entrypoint (the single canonical execution doc): {{ENTRYPOINT_RELATIVE_PATH}}
- Pack identity (entrypoint SHA-256): {{ENTRYPOINT_SHA256}}
- Readiness state: {{draft|blocked|ready_for_bootstrap|ready_for_execution}}

## Scope being launched

- Milestones: {{MILESTONE_IDS}}
- FRs covered: {{FR_IDS}} (count: {{FR_COUNT}})
- Tasks: {{TASK_COUNT}} — gates: {{GATE_COUNT}}
- Non-goals: {{NON_GOALS_LINK_OR_SUMMARY}}

## Execution contract

- Remediation budget: {{ROUNDS_PER_MILESTONE}} functional rounds per milestone (default 2)
- Stop conditions: per {{LOOP_CONTRACT_REFERENCE}} — crash, unknown error, wrong source,
  unavailable gate, native human consent, budget exhaustion
- Candidate binding: {{BINDING_ID}} (bytes, modes, tests, config, fixtures, deps,
  environment)
- Checks already run: {{RUN_CHECKS}} — pending: {{PENDING_CHECKS}}

## Unresolved

{{UNRESOLVED_CHOICES_EACH_WITH_EXACT_QUESTION_OR_NONE}}

## Limitations

This document authorizes nothing by existing. The loop is a prepared contract, not a
background service: nothing runs until the user explicitly authorizes implementation by
pasting explicit launch text in a session that holds the authorization to execute. PRD
approval never authorizes app implementation by itself.
