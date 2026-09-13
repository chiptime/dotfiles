# projects — weekly digest subsystem

Sends a weekly summary of the global project tracker to the phone via ntfy.

## Components

| File | Role |
|---|---|
| `cron.sh` | Regenerates `~/.local/share/projects-dashboard/PROJECTS.md` (via `scripts/projects-dashboard.sh`) and pushes a compact digest (section counts + repos with uncommitted changes) to ntfy |
| `install.sh` | Idempotent crontab installer (weekly, Monday 09:00) |

## Install / remove

```bash
~/.dotfiles/ai/projects/install.sh    # install or refresh
crontab -l | grep projects            # verify
# remove: crontab -e → delete the ai/projects/cron.sh line
```

## Dependencies

- `scripts/projects-dashboard.sh` (same repo) — the tracker itself
- `NTFY_TOPIC` from `shell/private-env.sh` (symlinked outside the repo; secrets never enter git)
- `curl` for the ntfy push (best-effort: failures are logged to `~/.local/state/projects-dashboard/cron.log` and never break the snapshot regeneration)

## Notes

- Logs: `~/.local/state/projects-dashboard/cron.log` (machine-local, never committed)
- Cron runs with a minimal PATH; all paths in `cron.sh` are absolute.
- The digest is a notification convenience — the source of truth is always the snapshot: run `projects` any time.
