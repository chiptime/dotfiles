---
name: pc-eyes
description: "Trigger: mira mi pantalla, screenshot, mirá esto, enséñame, teach workflow, capture screen. See Bruno's Windows desktop via the WSL node to learn workflows and follow his work."
version: 1.0.0
---

# PC Eyes (WSL node)

## Activation Contract

- Use when Bruno asks you to look at his screen, follow a workflow he is demonstrating, or capture what he is doing on the PC.
- The PC is the paired WSL node. Captures run on the node, never on this gateway host.

## Hard Rules

- Check the node is online first (`openclaw nodes status`); if absent, say so and ask Bruno to start it. Never fabricate or describe a screen you did not capture.
- Never capture without Bruno expecting it, unless he explicitly started a watch mode.
- Screen content may contain credentials: never quote secrets seen in captures; describe actions and flows instead.
- Captures stay machine-local under `~/.local/state/pc-eyes/` on the node; only the requested capture travels over the gateway.

## Workflow

1. Invoke the node: `node.invoke` -> `system.run` -> `wsl-shot` (node path: `~/.local/bin/wsl-shot`). Default run prints one JSON line: `file`, `width`, `height`, `bytes`.
2. Get the image for vision analysis. Prefer the node surface that returns files; otherwise rerun with `--b64` (stdout carries base64 only; JSON moves to stderr). If neither transport works yet, say the pairing transport is unresolved.
3. Read the capture in context of what Bruno is doing: name the tool, the visible state, and the data flowing between tools.
4. Persist learnings after each teaching segment into the workflow doc Bruno names (or propose one): tools, steps, data flow, failure points.

## Teaching Protocol (apprenticeship loop)

- Bruno narrates a step -> capture -> confirm understanding in one line -> next step.
- Batch multiple captures only when comparing before/after states.

## Decision Gates

| Need | Action |
|------|--------|
| Quick "what do you see?" | One capture, one-paragraph readout |
| Teaching a workflow | Capture per step + persist to workflow doc |
| Debugging a data flow (e.g. Postman -> DBeaver -> local Node) | Capture at each hop and diff the states |

## Output Contract

Return: capture confirmation (path + dimensions), the visual readout, and any workflow knowledge extracted.

## References

- `scripts/wsl-shot.sh` — node-side capture script; deploy to the node at `~/.local/bin/wsl-shot`
- `README.md` — deployment and pairing checklist
