# launchpad

Personal launchpad: a local web service that lists your apps and services
as health-checked tiles. Rust + `tiny_http`, embedded front, systemd user
service — same pattern as `tools/ai-quotas`.

## Endpoints

| Route        | What |
|--------------|------|
| `/`          | Tile grid (embedded HTML, `Cache-Control: no-store`) |
| `/api/tiles` | Tiles + server-side health states |
| `/api/health`| Own health JSON |

## Configuration

Tiles live in `~/.config/launchpad/tiles.json` (master copy in this repo at
`config/tiles.json`, symlinked on first deploy; `LAUNCHPAD_TILES` overrides
the path). The config is re-read on every `/api/tiles` request — edits apply
on the next front poll, no restart needed.

Two tile kinds (`kind`, defaults to `web`):

```json
{
  "tiles": [
    {
      "id": "ai-quotas",
      "kind": "web",
      "name": "AI Quotas",
      "icon": "📊",
      "url": "http://127.0.0.1:47623",
      "description": "Cuotas IA locales",
      "health_url": "http://127.0.0.1:47623/api/health",
      "health_interval_secs": 15
    },
    {
      "id": "opencode-switch",
      "kind": "script",
      "name": "Opencode Switch",
      "description": "Selector de perfiles de modelos para OpenCode",
      "command": "opencode-switch",
      "location": "~/.dotfiles/scripts/opencode-switch-profile.sh"
    }
  ]
}
```

- Web tiles: `url` required (must start with `http://` or `https://`);
  clicking opens it and the server probes `health_url` (default `url`).
- Script tiles: catalog entries — clicking copies `command` to the
  clipboard (with an `execCommand` fallback for LAN origins). Nothing is
  ever executed server-side. `command`/`docs`/`location` optional.
- `id` unique, non-empty; `health_interval_secs` must be > 0 when present.

### Health semantics

Any HTTP response (even 404) counts as **up** — a response proves the
listener is alive; transport errors/timeouts count as **down**.

## Ecosystem autodiscovery

```bash
./scripts/discover.py           # print discovered tiles as JSON
./scripts/discover.py --merge   # add only NEW ids to tiles.json
```

Sources: `scripts/*.sh` (commands resolved via `shell/aliases.sh` aliases),
`ai/*/` subsystems with README, personal repos under `~/Code/personal/`
(override with `LAUNCHPAD_PROJECT_ROOTS`, colon-separated; `*-worktrees`
skipped), and listening HTTP ports not already covered. `--merge` never
removes or overwrites existing entries — manual edits always win.

## Deploy

```bash
./scripts/deploy.sh back
```

Builds the release binary, installs it to `~/.local/bin/launchpad`, maps the
systemd unit and (first run only) the tiles config, restarts
`launchpad.service` and health-checks it.

- Port: `LAUNCHPAD_PORT` (default **47625**; 47623 is ai-quotas, 47624 is
  taken). Host: `LAUNCHPAD_HOST` (default loopback; `0.0.0.0` to expose to
  the LAN — pair with a firewall rule).

## Tests

```bash
cargo test
```
