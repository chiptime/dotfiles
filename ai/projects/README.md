# projects — tracker digest + web dashboard subsystem

Weekly summary of the global project tracker to the phone via ntfy + standalone web dashboard.

## Scheduling

The morning chain fires at **PC boot +90s** or **07:00** if already on
(`Persistent=true` recovers missed runs at next boot — Bruno starts his day ~08:50).
VPS-side clocks (drain-inbox 07:30, morning-brief 08:00) read whatever the last
chain delivered; see `doc/MAPA_INGESTAS.md` for the full clock map.

## Components

| File | Role |
|---|---|
| `cron.sh` | Regenerates `~/.local/share/projects-dashboard/PROJECTS.md` (via `scripts/projects-dashboard.sh`) and pushes a compact digest (section counts + repos with uncommitted changes) to ntfy |
| `morning.sh` | Morning chain: regenerate snapshot + send daily digest (ntfy) |
| `install.sh` | Idempotent installer: morning-chain systemd timer (boot catch-up + 07:00) + web service |

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
systemctl --user list-timers projects-morning.timer   # verify
systemctl --user status projects-dashboard.service
# remove: systemctl --user disable --now projects-morning.timer projects-dashboard.service
```

## Dependencies

- `scripts/projects-dashboard.sh` (same repo) — the tracker itself
- `NTFY_TOPIC` from `shell/private-env.sh` (symlinked outside the repo; secrets never enter git)
- `curl` for the ntfy push (best-effort: failures are logged to `~/.local/state/projects-dashboard/cron.log` and never break the snapshot regeneration)

## Notes

- Logs: `~/.local/state/projects-dashboard/cron.log` (machine-local, never committed)
- Cron runs with a minimal PATH; all paths in `cron.sh` are absolute.
- The digest is a notification convenience — the source of truth is always the snapshot: run `projects` any time.
