"""TikTok ingest MVP — authorized input to CPU artifacts (Milestones 1-2).

Milestone 1 delivered contracts, local state and resume logic. Milestone 2
adds the gated pinned extractor (with oEmbed metadata cache), shared-media
validation, pure hybrid frame selection, CPU preparation inside the pinned
network-isolated FFmpeg container, the direct-URL input mode and the CLI.

Fixture-only test suite: no network, no GPU, no podman, no services, no
installs. See ``ai/tiktok-ingest/README.md`` and the PRDs for scope and
boundaries. Runtime state lives outside Git under
``~/.local/state/tiktok-ingest/``; extractor binaries live under
``~/.local/share/tiktok-ingest/``; durable code lives in this repository.
"""

__version__ = "0.2.0"
