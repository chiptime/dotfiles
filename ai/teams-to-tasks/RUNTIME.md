# Unattended runtime boundary

The launcher supports Linux OpenCode **1.18.30** and a non-Git run workspace.
It refuses another version or an unexpected Git workspace before model startup.
Version-matched source: `anomalyco/opencode`, commit
`3104c1428ec91f809e5ab86631300de41eb6952e`.

## Reads and screenshots

- Native `read` stays denied. Only the owned `read-plugin.ts` is configured;
  interactive plugins remain excluded by isolated HOME/config and disabled
  project discovery. `--pure` cannot be used: it suppresses this plugin too.
- `teams_read` opens a file once, verifies `/proc/self/fd/<fd>` resolves inside
  this run's canonical images or `data/opencode/tool-output` root, checks that
  the handle is a bounded regular file with one link, asks OpenCode's permission
  evaluator, and reads that same handle. Plugin-load failure leaves no reader.
- Read resources use `path.relative(ctx.worktree, actualPath)`; external-directory
  resources use absolute directory globs. Native 1.18.30 uses relative resources
  too, but does not resolve symlinks. For a non-Git workspace, worktree is `/`.
- Screenshots require a new absolute filename directly inside the supplied
  images directory. The prompt supplies the actual path; a before-tool hook
  rejects relative, existing and outside targets. Browser execution was not
  used to validate this correction.
- Images return standard PNG/JPEG/GIF/WebP attachments. Text supports offset/limit
  pagination with bounded output. Directories, binary text and files above 20 MiB
  fail closed rather than becoming a general filesystem interface.

## Authentication and diagnostics

The launcher's private channel accepts native `OPENCODE_AUTH_CONTENT`, otherwise
the original XDG data auth file. Only the selected provider's API/OAuth entry is
copied to isolated `data/opencode/auth.json` (0600). Unrelated accounts and
`wellknown` remote-config auth are not forwarded. No auth values enter prompts,
plugin options, repository files or startup diagnostics. Provider environment
variables and the chosen model/provider/variant remain unchanged.

Auth is deleted after normal child exit or launcher failure. A forced kill can
leave the private copy; OAuth refresh updates are not written back to the user's
original store. Existing default-plugin restrictions remain unchanged, so an
OAuth provider requiring such a plugin still needs separate compatibility proof.
This does not establish the cause of the previously observed `UnknownError`.

`startup.jsonl` records a bounded sequence of fixed stage/category enums, never
exception messages, headers, environment dumps or session text. Existing event
and stderr files retain the completion validator's evidence and private umask.

## Proof and residual risk

Tests execute version-matched native resource/evaluator expressions, the actual
owned reader against real dummy symlinks/hardlinks and a pathname swap, dummy
auth provisioning, screenshot-hook negatives and a dummy failing CLI. The
installed CLI's config check runs in a network-disabled namespace. This is not
full native ReadTool/agent/provider/MCP or scheduled-run proof.

Reading a pinned descriptor prevents a later pathname swap from redirecting the
read. It is **not a hardened sandbox** against same-user writers, mount changes,
in-place inode modification, or preexisting sensitive content moved into an
allowed directory. The screenshot hook is a precheck and retains a TOCTOU window
before the MCP opens its output path. No model shell/edit capability is added.

Rollback the launcher/prompt/test correction together with the owned plugin and
support modules; do not revert unrelated preexisting work. Checkpoint preservation
does not roll back already completed Notion writes. No checkpoint or scheduler
change is part of this correction.
