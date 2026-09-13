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
- Captures scale to 1600px-wide JPEG q70 by default (~60–90 KB) to fit WS payloads; `--width 0` gives full resolution.
- Screen content is privacy-sensitive: captures never enter git, stay under `~/.local/state/pc-eyes/`.
