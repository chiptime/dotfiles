#!/bin/sh
# Notion MCP launcher with secret fallback.
#
# opencode resolves {env:NOTION_CLECE} only when its own process carries the
# variable. Launches from systemd --user (desktop entries, services) never
# source shell/private-env.sh, so the token resolved empty and the server sent
# a malformed Bearer header (Notion 401). This wrapper keeps private-env.sh as
# the single source of truth and fills NOTION_TOKEN when it arrives empty.
[ -n "$NOTION_TOKEN" ] || {
	. "$HOME/.dotfiles/shell/private-env.sh" 2>/dev/null
	NOTION_TOKEN="$NOTION_CLECE"
	export NOTION_TOKEN
}
exec npx -y @notionhq/notion-mcp-server
