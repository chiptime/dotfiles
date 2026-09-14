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
- Bruno's PC has a consent gate: every capture pops a 15-second Yes/No dialog on his screen. Refusals and timeouts return stdout JSON `{"error":"denied-by-user","gate":"denied"|"timeout"}` with non-zero exit. Accept the answer, say it plainly, and NEVER retry the capture without him asking first.
- Screen content may contain credentials: never quote secrets seen in captures; describe actions and flows instead.
- Captures stay machine-local under `~/.local/state/pc-eyes/` on the node; only the requested capture travels over the gateway.

## Workflow

1. Invoke the node: exec tool with `host=node`, node `bruno-wsl`, command `/home/bruno/.local/bin/wsl-shot`. Run it as ONE plain command — NO redirects, pipes, or shell chains (wrappers like `bash -c` break the allowlist and get denied). stdout JSON: `{"file","gatewayPath","width","height","bytes"}`.
2. The script already delivers the JPG to the gateway filesystem at `gatewayPath` (e.g. `/downloads/pc-eyes/shot-...jpg`). NEVER route the image through the conversation as base64 text — models corrupt large blobs when re-emitting them. The `image` tool cannot read `/downloads/` directly: first copy the file into the workspace (`cp <gatewayPath> /home/node/.openclaw/workspace/pc-eyes/`, create the dir if needed) and use the workspace path.
3. Analyze the image with the `image` tool using `gatewayPath` (native vision, model `zai/glm-4.6v`): name the tool, the visible state, and the data flowing between tools. If `gatewayPath` is empty, the scp delivery failed — say so and stop; do not fall back to base64. Do NOT try `screen.record` — it is blocked by `gateway.nodes.denyCommands`.
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
