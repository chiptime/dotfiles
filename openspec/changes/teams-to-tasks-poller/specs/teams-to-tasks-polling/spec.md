# teams-to-tasks-polling Specification

## Purpose

LLM-free, read-only preflight that decides whether the Teams-to-Tasks sweep agent runs: search Teams for today's messages, compare timestamps against persisted state, exit deterministically. Task creation unchanged; only launch becomes event-driven.

## Requirements

### Requirement: News detection

The poller MUST read Teams search results (`que`, `Date=Today`) via an injected adapter, normalize each timestamp to canonical UTC, and classify news as any timestamp strictly newer than `last_run`.

#### Scenario: Newer message is news

- GIVEN last_run `2026-09-09T18:30Z`, newest message `2026-09-10T08:00Z`
- WHEN evaluated
- THEN the outcome is news

#### Scenario: Equal or older is no-news

- GIVEN last_run equals the newest message timestamp
- WHEN evaluated
- THEN the outcome is no-news

#### Scenario: Missing state bootstraps as news

- GIVEN no state file and messages found
- WHEN evaluated
- THEN the outcome is news

### Requirement: State advancement

A no-news poll MUST advance `last_run` to its poll start T before exiting. A news poll MUST NOT advance: `cron.sh` writes T only after the gated agent succeeds. Every failure path MUST leave `last_run` byte-identical.

#### Scenario: No-news advances at poll

- GIVEN a no-news poll that started at T
- WHEN the poller exits with the no-news code
- THEN last_run equals T

#### Scenario: News defers advancement to agent success

- GIVEN a news poll that started at T, prior state P
- WHEN the poller exits news, writing nothing
- THEN last_run stays P; cron.sh writes T after agent success, never after failure

#### Scenario: Failure preserves state

- GIVEN last_run R and a throwing adapter
- WHEN the poller exits with the failure code
- THEN last_run is byte-identical to R

### Requirement: Deterministic exit codes

The poller MUST exit with exactly three codes: `0` news, `1` no-news, `2` failure. Every error path MUST map to `2` with a diagnostic on the log/stderr channel.

#### Scenario: Failure code is uniform

- GIVEN any error path (throw, invalid state, timeout)
- WHEN the poller terminates
- THEN the exit code is `2`, no partial classification emitted

### Requirement: Browser always closed

The poller MUST close the browser and release the shared Teams profile on every exit path.

#### Scenario: Close on error

- GIVEN the adapter throws mid-session
- WHEN the poller exits
- THEN the adapter's close ran exactly once

### Requirement: Hard timeout

The poll MUST be bounded by a hard timeout; expiry MUST follow the failure code path.

#### Scenario: Hung search is terminated

- GIVEN the adapter never returns
- WHEN the timeout elapses
- THEN the poll exits `2` and the close runs

### Requirement: PAUSE and flock preserved

`cron.sh` MUST keep the PAUSE check and hold the flock across the poller→agent sequence; poller and agent MUST NEVER hold the browser profile concurrently.

#### Scenario: PAUSE blocks everything

- GIVEN the PAUSE file exists
- WHEN cron fires
- THEN neither poller nor agent runs

#### Scenario: Lock contention skips run

- GIVEN the lock is held elsewhere
- WHEN cron fires
- THEN it exits without polling or launching

### Requirement: Agent gating

`cron.sh` MUST launch the agent exactly once ONLY on the news code; no-news and failure MUST NOT launch it, and failures SHOULD notify.

#### Scenario: News launches one agent

- GIVEN the poller exits `0`
- WHEN cron.sh proceeds
- THEN one agent run starts

#### Scenario: No-news and failure launch nothing

- GIVEN the poller exits `1` or `2`
- WHEN cron.sh proceeds
- THEN no agent launches and cron exits cleanly

### Requirement: Read-only guarantee

The poller MUST NOT post, react, reply, or otherwise mutate Teams state.

#### Scenario: No mutation calls

- GIVEN any poll outcome
- WHEN the session is inspected
- THEN no mutation action was issued to Teams

## Out of Scope

The 18:30 digest; extraction, Notion, and dedupe changes; interactive usage; any Teams mutation.
