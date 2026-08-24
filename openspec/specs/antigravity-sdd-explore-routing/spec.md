# Antigravity SDD Explore Routing Specification

## Purpose

Route SDD exploration through Antigravity `agy` behind the unchanged `task(subagent_type="sdd-explore")` contract: cheap router, safe fallback.

## Requirements

### Requirement: Orchestrator Contract

The router MUST be a drop-in for `task(subagent_type="sdd-explore")`: valid SDD phase envelope, never nested `<task_result>`, orchestrator call unchanged.

#### Scenario: Unchanged delegation

- GIVEN the orchestrator calls `task(subagent_type="sdd-explore")`
- WHEN the router runs
- THEN a valid `sdd-explore` envelope returns

### Requirement: Shared Binding

A shared user-owned `OPENCODE_CONFIG` override MUST bind `sdd-explore` to the router for Web projects; repository config MUST allow per-project opt-out restoring native.

#### Scenario: Routed project

- GIVEN a project without opt-out
- WHEN `sdd-explore` is invoked
- THEN the router agent executes

#### Scenario: Opted-out project

- GIVEN a project with opt-out
- WHEN `sdd-explore` is invoked
- THEN the native agent executes

### Requirement: Cheap Router

The router MUST run on a cheap/free model with quota selection, outcome classification, and canonical validation in deterministic code; it MUST record token overhead, success/fallback rates, latency, and failure classifications for phase-D viability.

#### Scenario: Cost-reduced routing

- GIVEN agy is available
- WHEN exploration is routed
- THEN a cheap model decides; deterministic code routes
- AND token overhead, rates, latency, classifications are recorded

### Requirement: Quota Selection and Staleness

The router MUST pick the Gemini or 3P pool from the passive snapshot by requested model, not `active_model`; a missing or stale snapshot MUST allow one real agy attempt, refreshing routing state.

#### Scenario: Pool by requested model

- GIVEN a Gemini request
- WHEN the snapshot is read
- THEN the Gemini pool governs

#### Scenario: Stale snapshot refresh

- GIVEN snapshot missing or stale
- WHEN one real attempt runs
- THEN its outcome refreshes routing state

### Requirement: Fallback and Blocking

The router MUST delegate to `sdd-explore-fallback` only on quota exhaustion, timeout, or provider outage; auth, captcha, contract, and artifact-validation failures MUST block and surface; only the fallback is task-permitted and it MUST deny task, capping nesting at depth 2.

#### Scenario: Approved unavailability

- GIVEN quota exhaustion, timeout, outage
- WHEN classified
- THEN native fallback is delegated

#### Scenario: Disallowed fallback

- GIVEN auth, captcha, contract, or validation failure
- WHEN classified
- THEN it blocks and surfaces; no fallback

#### Scenario: Bounded nesting

- GIVEN the router delegates fallback
- WHEN the fallback runs
- THEN task is denied; no deeper nesting

### Requirement: Force-Native

A manual force-native setting MUST bypass agy and run the native executor; rollback MUST work via force-native or opt-out plus an OpenCode Web restart, native fallback unchanged.

#### Scenario: Kill switch active

- GIVEN force-native is set
- WHEN exploration runs
- THEN agy is skipped; native executes

#### Scenario: Rollback path

- GIVEN force-native or opt-out and Web restarted
- WHEN exploration runs
- THEN native behavior returns

### Requirement: Typed Runner

The agy-runner MUST consume versioned JSON input and return versioned typed outcomes (success, quota_unavailable, transient_unavailable, auth_captcha, timeout, task_failure, artifact_validation_failure); raw exit codes MUST NOT be trusted alone.

#### Scenario: Typed success

- GIVEN agy completes successfully
- WHEN the runner returns
- THEN a typed success with artifact path

### Requirement: Persistence Ownership

Canonical store behavior MUST hold for engram, openspec, hybrid, none: the router persists only on agy success, the fallback only on its own path, one owner; none persists nothing.

#### Scenario: Router-owned persistence

- GIVEN agy success
- WHEN the router persists
- THEN the canonical artifact exists once

#### Scenario: Fallback-owned persistence

- GIVEN the fallback executes
- WHEN it completes
- THEN it persists; router does not

### Requirement: Filesystem Containment

Exploration MUST NOT modify the codebase: agy MUST run read-only in its workdir with read-only codegraph MCP; only artifact paths writable.

#### Scenario: Contained run

- GIVEN agy explores the repository
- WHEN it finishes
- THEN the codebase is unchanged; only artifact paths written