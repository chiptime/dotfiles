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

Two tile kinds (`kind`, defaults to `web`; `project` marks repo catalog
entries):

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
- Project tiles: repo catalog entries in their own front section — clicking
  copies `location`. `docs`/`location` optional.
- `id` unique, non-empty; `health_interval_secs` must be > 0 when present.
- `tags`: optional array of lowercase category strings (e.g. `["voz"]`)
  shown as filter chips. Manual tags always win — retag only unions.

### Search and type filter

The front has a search box (matches name, description, command and tags,
case-insensitive) and type chips: `Web`/`Script` by kind plus one chip per
distinct tag (`personal` and `trabajo` display as "Proyectos personales" /
"Proyectos de trabajo"). Selecting a chip ANDs it with the search text.
Sections: Servicios, Proyectos, Scripts & herramientas.

### Health semantics

Any HTTP response (even 404) counts as **up** — a response proves the
listener is alive; transport errors/timeouts count as **down**.

## Ecosystem autodiscovery

```bash
./scripts/discover.py           # print discovered tiles as JSON
./scripts/discover.py --merge   # add only NEW ids to tiles.json
./scripts/discover.py --retag   # add missing auto tags to existing tiles
./scripts/discover.py --sync    # discover once, then --merge + --retag in one locked write
```

The modes are mutually exclusive and unknown options fail with exit 2.
`--retag` only reads the config (no discovery, no port scan); `--sync` is what
the timer runs (see [Scheduled discovery](#scheduled-discovery)).

All writer modes (`--merge`, `--retag`, `--sync`) take one cooperative
`flock` from read to commit, kept in machine-local state
(`$XDG_STATE_HOME/launchpad/tiles.lock`, default
`~/.local/state/launchpad/tiles.lock`; waits up to 120 s, override with
`LAUNCHPAD_LOCK_TIMEOUT_SECS`). The lock only coordinates runs of this
script; it does not stop an editor from saving the file. Writes resolve the
config symlink and atomically replace the target, keeping the symlink, its
permissions, manual fields and unknown top-level keys. Invalid JSON or an
unexpected shape fails with exit 1 and leaves the stored bytes untouched; a
run that changes nothing does not rewrite the file.

Sources: `scripts/*.sh` (commands resolved via `shell/aliases.sh` aliases),
`ai/*/` subsystems with README, git repos under `~/Code/personal/` and
`~/Code/Work/` at any depth (nested client/repo layouts included; override
with `LAUNCHPAD_PROJECT_ROOTS`, colon-separated; hidden dirs, `*-worktrees`
and `node_modules`/`dist`/`target` are skipped; generic basenames like
`backend`/`frontend` get their parent dir prefixed), and listening HTTP
ports not already covered. `--merge` never removes or overwrites existing
entries — manual edits always win.

Auto tags for the front's type chips: `personal` / `trabajo` by repo root,
`ia` for `ai/*/` subsystems plus AI keywords (ia/llm/ai/llama/embedding/
agent/agy/whisper), and keyword domains `teams`, `voz`
(voice/whisper/tts/herdr), `sap` (sap/odata/ssff). `--retag` also renames
legacy auto tags (`proyecto`→`personal`, `llm`→`ia`), reconciles
script→project kind for tiles under a project root, unions tags (manual
tags are never removed) and is idempotent.

## Scheduled discovery

A systemd **user** timer runs `discover.py --sync` locally: 2 min after the
user manager starts (post-boot catch-up), then every 30 min after the
previous run. The oneshot service has a 5 min timeout. It makes no morning
/ ntfy / hub / SSH / remote requests; the only network traffic is the
existing loopback port probes. Logs live in the local journal.

```bash
./scripts/install-discovery.sh                      # idempotent; deploy.sh runs it after the health check
systemctl --user list-timers launchpad-discover.timer   # next/last run
systemctl --user status launchpad-discover.service      # result of the last run
journalctl --user -u launchpad-discover.service -n 50   # sync output (+ added / ~ retagged)
systemctl --user start launchpad-discover.service       # run once now
systemctl --user disable --now launchpad-discover.timer # turn it off
```

The installer symlinks `systemd/launchpad-discover.{service,timer}` into
`~/.config/systemd/user`, reloads systemd and enables only the timer.

Polling vs scanning: the front polls `/api/tiles` every 5 s, which re-reads
the config file and health-checks the configured tile URLs; it never
discovers anything. The port scan runs only in discovery (default, `--merge`,
`--sync` and the timer), never from polling or `--retag`.

## Deploy

```bash
./scripts/deploy.sh back
```

Builds the release binary, installs it to `~/.local/bin/launchpad`, maps the
systemd unit and (first run only) the tiles config, restarts
`launchpad.service`, health-checks it and then installs the discovery timer.

- Port: `LAUNCHPAD_PORT` (default **47625**; 47623 is ai-quotas, 47624 is
  taken). Host: `LAUNCHPAD_HOST` (default loopback; `0.0.0.0` to expose to
  the LAN — pair with a firewall rule).

## Tests

```bash
cargo test
python3 -m unittest discover -s tests -p 'test_discover.py' -v   # discovery + timer plumbing (isolated HOME/state)
```
