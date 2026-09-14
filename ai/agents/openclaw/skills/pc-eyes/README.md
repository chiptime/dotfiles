# pc-eyes — OpenClaw skill for seeing Bruno's PC

Born in `~/.dotfiles` (source of truth). Deploy targets:

| Piece | From (repo) | To (real) |
|---|---|---|
| Capture script | `ai/agents/openclaw/skills/pc-eyes/scripts/wsl-shot.sh` | WSL node: `ln -sfn ~/.dotfiles/ai/agents/openclaw/skills/pc-eyes/scripts/wsl-shot.sh ~/.local/bin/wsl-shot` |
| Skill | `ai/agents/openclaw/skills/pc-eyes/SKILL.md` (+ this README) | VPS: `~/.openclaw/workspace/skills/pc-eyes/` (scp or git pull; cannot be symlinked across machines) |

## One-time setup

1. **Node side (this WSL machine):**
   - `ln -sfn` mapping above, then `chmod +x ~/.local/bin/wsl-shot`.
   - Test: `wsl-shot` → JSON line with `file/width/height/bytes`; open the jpg to verify.
2. **Pair node to gateway (VPS):**
   - Node: `openclaw node run --host <gateway-host> --port 18789 --display-name bruno-wsl` (via Tailscale or secure tunnel).
   - Gateway: `openclaw devices list` → `openclaw devices approve <id>`; then `openclaw nodes pending` → `openclaw nodes approve <id>`.
   - Add `system.run` to the node exec approvals only after reviewing the allowlist (`~/.openclaw/exec-approvals.json` on the node).
3. **Gateway side (VPS):**
   - Copy `SKILL.md` + this README into `~/.openclaw/workspace/skills/pc-eyes/`.
   - `openclaw skills list` should show `pc-eyes`.
4. **Transport check (first session):**
   - From the gateway, capture via the node and confirm how image bytes reach the agent (node file surface vs `--b64`). Lock the chosen path into SKILL.md step 2.

## Notes

- `powershell.exe` is NOT on PATH in this WSL; the script falls back to the absolute path `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`.
- Captures are full-resolution JPEG q85 by default (~600 KB max expected); `--width N` scales down (0 = native, the default).
- Screen content is privacy-sensitive: captures never enter git, stay under `~/.local/state/pc-eyes/`.

## Deployed state (2026-09-14)

- Skill registered on gateway: `openclaw skills list` → `pc-eyes ✓ ready`.
- Node `bruno-wsl` paired (device + command surface: `system.run`, `system.which`, exec approvals).
- Exec approvals on the node (`~/.openclaw/exec-approvals.json`): `security=allowlist`, `ask=off`, `askFallback=deny`, `autoAllowSkills=true`; allowlist = bare `wsl-shot`, symlink path, and dotfiles real path (the runner resolves symlinks — all three forms required).
- Durability: two systemd user services on the WSL — `openclaw-gw-tunnel.service` (SSH tunnel local 18790 → VPS 100.74.160.4:18789) and `openclaw-node.service` (installed via `openclaw node install`, token captured in service env). Both enabled with auto-restart.
- Gotchas that cost debugging time (do not rediscover):
  - Shell wrappers (redirects, pipes, `bash -c`) break the allowlist → instruct the agent to run plain single commands.
  - `system.run` is exec-tool-only; `nodes invoke system.run` is reserved.
  - The deny surfaces as `SYSTEM_RUN_DENIED: approval required` both for ask-gating and allowlist misses — check the allowlist first.
  - Env vars do NOT cross into Windows processes (WSLENV /u unreliable) — `wsl-shot.sh` inlines values into the PS command.
  - Transport (final): `wsl-shot` captures, scp's the JPG to the VPS volume bind-mounted at `/downloads/pc-eyes/` inside the gateway container, and returns `gatewayPath` in the JSON; the agent reads it with the `image` tool (native vision). Dead ends, do not revisit: `--b64` over stdout (models corrupt base64 blobs) and the File Transfer plugin in 2026.7.1 (`file_fetch` enabled at gateway but never bound into agent sessions; node host 2026.7.1 declares no `file.*` commands).
- Consent gate (2026-09-14, requested by Bruno): `wsl-shot` pops a 15s Yes/No Windows dialog (WScript.Shell.Popup, topmost) before every capture. Yes=proceed; No/timeout=deny with `{"error":"denied-by-user"}`, exit 1. All answers land in `~/.local/state/pc-eyes/approvals.log`. `PC_EYES_NO_GATE=1` skips the prompt (only for Bruno's own local runs); `PC_EYES_GATE_TIMEOUT` overrides the 15s.
- `gateway.nodes.denyCommands` blocks `screen.record`; `wsl-shot` is the only screen surface.
