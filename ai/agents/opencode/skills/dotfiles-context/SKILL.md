---
name: dotfiles-context
description: "Trigger: dotfiles, nueva skill, script persistente, crontab, config de agente, fragmento MCP, symlink, mapear, replicable, donde pongo esto. Persistent artifacts are born in ~/.dotfiles and mapped to real paths — never the reverse."
license: Apache-2.0
metadata:
  author: "bruno"
  version: "1.0"
---

# Skill: dotfiles-context

## Activation Contract

Load BEFORE creating any persistent artifact outside a project: OpenCode skills, agent commands, cron jobs, helper scripts, MCP/config fragments, shell helpers. Also load when asked "¿dónde pongo esto?" about config.

## Hard Rules

- Source of truth is ALWAYS `/home/bruno/.dotfiles` (git, remote `chiptime/dotfiles`). Real paths under `~/.config/` are mapped, never the birthplace.
- SECRETS NEVER enter the repo. Tokens live in `shell/private-env.sh`, which is itself symlinked OUTSIDE the repo. Config references them only as `{env:VAR}`.
- Before any commit: secret-scan changed files against private-env values and generic token patterns; abort on any match.
- Mapping is a direct idempotent `ln -sfn <repo-path> <real-path>`. Do NOT require dotbot runs; `symlinks/conf.*.yaml` is passive documentation for fresh-machine bootstrap only.
- Machine-local data (browser profiles, state, logs, DBs, caches) stays under `~/.local/` and is never committed.
- Third-party binaries are never committed: the repo pins version and URL, and the installer downloads them to `~/.local/bin` (example: `wsl-notify-send` in `scripts/install-teams-to-tasks.sh`).

## Decision Gates

| Artifact | Repo home | Real mapping |
|---|---|---|
| OpenCode skill | `ai/agents/opencode/skills/<name>/` | `ln -sfn` → `~/.config/opencode/skills/<name>` |
| Cron job | `ai/<domain>/cron.sh` + README | symlink script + crontab line via subsystem installer |
| MCP/config fragment | `ai/agents/opencode/mcp/*.fragment.json` | merged into `opencode.json` by installer (jq), never hand-edited twice |
| Secret value | `shell/private-env.sh` (outside repo) | sourced by shell, referenced as `{env:VAR}` |
| Machine-local data | nowhere | `~/.local/share/...` or `~/.local/state/...` |

## Execution Steps

1. Place the artifact in its repo home (table above); update the subsystem README if one exists.
2. Map to the real path with `ln -sfn`; verify with `ls -la` and by reading a file through the link.
3. If it introduces a scheduled job or config fragment, extend/run the subsystem installer idempotently (example: `scripts/install-teams-to-tasks.sh`).
4. Register new skills in the skill registry; remind that config-time files need an opencode restart.
5. Run `git -C ~/.dotfiles status` plus the secret scan; report created files and mappings. Commit only on explicit request.

## Output Contract

Return: repo paths created, real mappings applied, registry/installer changes, secret-scan result.

## References

- `symlinks/conf.linux.yaml` — documented link map (bootstrap only)
- `shell/private-env.sh` — secret source (symlinked outside the repo)
- `ai/teams-to-tasks/README.md` — example subsystem following this contract
