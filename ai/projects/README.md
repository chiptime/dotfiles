# projects — tracker digest + web dashboard subsystem

Weekly summary of the global project tracker to the phone via ntfy + standalone web dashboard.

## Components

| File | Role |
|---|---|
| `cron.sh` | Regenerates `~/.local/share/projects-dashboard/PROJECTS.md` (via `scripts/projects-dashboard.sh`) and pushes a compact digest (section counts + repos with uncommitted changes) to ntfy |
| `install.sh` | Idempotent installer: weekly digest cron (Mon 09:00), daily silent regen cron (08:00), systemd user service |

## Web dashboard

`scripts/projects-html.py` renders `PROJECTS.html` (standalone, NOT part of ai-quotas),
served by the `projects-dashboard.service` user unit on **port 47624**. Includes:
- 🎯 Pending actionables (every `next:` from `scripts/projects.yaml`)
- 💬 Recent conversations — ledger rows carrying an OpenCode `ses_...` id, deep-linked
  via `~/.local/share/time-ledger/webbase` (one line; default `http://127.0.0.1:4096`;
  point it at the mobile-proxy base for phone access)

Phone access: Tailscale → `<wsl-host>:47624/PROJECTS.html` (service binds 0.0.0.0;
Tailscale is the access control).

## Install / remove

```bash
~/.dotfiles/ai/projects/install.sh    # install or refresh
crontab -l | grep projects            # verify
systemctl --user status projects-dashboard.service
# remove: crontab -e → delete both lines; systemctl --user disable --now projects-dashboard.service
```

## Dependencies

- `scripts/projects-dashboard.sh` (same repo) — the tracker itself
- `NTFY_TOPIC` from `shell/private-env.sh` (symlinked outside the repo; secrets never enter git)
- `curl` for the ntfy push (best-effort: failures are logged to `~/.local/state/projects-dashboard/cron.log` and never break the snapshot regeneration)

## Notes

- Logs: `~/.local/state/projects-dashboard/cron.log` (machine-local, never committed)
- Cron runs with a minimal PATH; all paths in `cron.sh` are absolute.
- The digest is a notification convenience — the source of truth is always the snapshot: run `projects` any time.
