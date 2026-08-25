# sdd-explore request flow (current)

Mermaid diagram of one routed SDD exploration with the dispatcher plugin active
(Phase 1 optimizations + Phase 2 0-LLM dispatcher). Rendered from verified
code paths — `src/agy/dispatch-core.ts`, `src/agy/outcomes.ts`, and the
`tool.execute.before` hook contract.

```mermaid
flowchart TD
    ORCH["[1] Orchestrator — deepseek-v4-pro (~2 requests per turn)"]
    ORCH -->|"task(subagent_type='sdd-explore',<br/>prompt with change/store/brief)"| HOOK

    HOOK{"[2] Plugin sdd-explore-dispatch<br/>hook tool.execute.before — 0 LLM"}

    HOOK -->|"tool != task or<br/>subagent_type != sdd-explore"| PASS["Pass through<br/>(2 comparisons, zero overhead)"]

    HOOK -->|"kill switch<br/>~/.config/ai-stack/force-native"| NAT
    HOOK -->|"agy-explore or agy<br/>binary missing"| NAT
    HOOK -->|"unparseable prompt<br/>(dispatcher never guesses)"| NAT

    HOOK -->|"parse OK"| CLI

    NAT["[3a] Native router fallback — nemotron-3-ultra<br/>Phase-1 optimized: 2 turns typical<br/>1. write req.json + bash agy-explore<br/>2. parse bash stdout, emit envelope<br/>3rd turn only if engramRequired"]
    NAT --> CLI

    CLI["[3] agy-explore — LOCAL process, 0 LLM<br/>validate req (schema agy-explore/req@1)<br/>quota gate (~/.local/state/ai-quotas/*.json)"]
    CLI --> AGY

    AGY["[4] agy --print --model req.model<br/>real exploration on Gemini (Google side)<br/>N requests — the actual work"]
    AGY --> CLASS

    CLASS{"[5] Deterministic classification<br/>quota/transient regex counts ONLY<br/>when exit code != 0 (Phase 1)"}

    CLASS -->|"success"| ENV["THROW envelope<br/>Status/Summary/Artifacts/Next/Risks<br/>orchestrator reads artifactPath<br/>note: renders as an error-framed step<br/>in the chat UI (accepted tradeoff)"]

    CLASS -->|"quota / transient / timeout<br/>with exit != 0"| FB["MUTATE args.subagent_type =<br/>'sdd-explore-fallback' (original prompt)<br/>DeepSeek redoes exploration natively<br/>12-30 requests"]
    FB --> NATFB["sdd-explore-fallback agent<br/>deepseek-v4-pro"]

    CLASS -->|"anything else<br/>(blocked classes)"| BLOCK["THROW blocked envelope<br/>outcome + reason surfaced<br/>orchestrator decides"]
```

## Per-path request cost

| Path | Before (LLM router) | Now (dispatcher) |
|---|---|---|
| Happy (dispatch success) | 2 DeepSeek + **3-4 Nemotron** + N Gemini | 2 DeepSeek + **0 router** + N Gemini |
| Degraded (ambiguous prompt / kill switch / missing binary) | — | 2 DeepSeek + 2 Nemotron + N Gemini |
| Real fallback (quota exhausted, exit != 0) | 2 + 3-4 Nemotron + 12-30 DeepSeek | 2 + 0 router + 12-30 DeepSeek |
| False fallback from log noise | happened (2026-08-24, metrics #9) | impossible — exit code corroborates |

## Escape hatches (all land on the optimized native router, never worse)

1. Kill switch: `touch ~/.config/ai-stack/force-native` (same file the CLI checks).
2. Binary preflight: dispatcher verifies `agy-explore` and `agy` exist before dispatching.
3. Parse ambiguity: no backticked change name, conflicting names, or unparseable
   prose falls through to the native 2-turn router — the dispatcher never guesses.

## Known gaps

- Engram persistence (`sdd/{change}/explore` mem_save) was done by the LLM router;
  the plugin has no MCP access, so the envelope reports it as PENDING with the
  workdir artifact path. Follow-up: fold the engram write into `agy-explore`.
- gentle-ai prompt drift lowers the dispatch rate silently — check the plugin's
  debug logs (`client.app.log`) if dispatched runs stop appearing.
