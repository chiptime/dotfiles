#!/usr/bin/env python3
"""projects_unified.py: Deterministic join between catalogue tiles and tracker projects.

Provides:
- build_unified_cards(): merges tiles (from tiles.json or /api/tiles) and tracker
  entities (projects.yaml + PROJECTS.md + hub-state + ledger + oc_sessions).
- Format and label helpers for UI rendering and tests.
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple

STATUS_LABEL = {
    "active": "activo",
    "proposal": "propuesta",
    "paused": "pausado",
    "closed": "cerrado",
    "cold": "frío",
    "unclassified": "sin clasificar",
    "archived": "archivado",
}

SCOPE_LABEL = {
    "personal": "Personal",
    "work": "Trabajo",
}

# Explicit disambiguation mapping: tile_id -> tracker_key
# Handles group projects, renamed repos, and known aliases
DEFAULT_DISAMBIGUATION = {
    "compara-precios": "compare-prices",
    "comparaprecio": "compare-prices",
    "Planificador-backend": "planificador",
    "Planificador-frontend": "planificador",
    "associations": "sis",
    "entities-portal": "sis",
    "operational-centers": "sis",
    "oc--test": "sis",
    "apps-workspaces-operational-centers": "sis",
    "dotfiles-discover": "dotfiles",
    "dotfiles-deploy": "dotfiles",
    "herdr-tts": "agent-tts",
}


def normalize_key(text: str) -> str:
    """Normalize string for safe anchor and lookup."""
    return re.sub(r"[^\w.-]+", "-", text.strip().lower()).strip("-")


def resolve_tile_tracker_key(
    tile: Dict[str, Any],
    tracker_keys: set,
    disambiguation: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Deterministically find which tracker key a tile corresponds to."""
    dis_map = disambiguation if disambiguation is not None else DEFAULT_DISAMBIGUATION
    tid = tile.get("id", "")
    loc = tile.get("location", "")
    loc_base = os.path.basename(loc.rstrip("/")) if loc else ""

    # Rule 1: Disambiguation map
    if tid in dis_map and dis_map[tid] in tracker_keys:
        return dis_map[tid]

    # Rule 2: Exact tile id in tracker
    if tid in tracker_keys:
        return tid

    # Rule 3: Location basename in tracker
    if loc_base and loc_base in tracker_keys:
        return loc_base

    return None


def build_unified_cards(
    tiles: List[Dict[str, Any]],
    projects_yaml: Dict[str, Any],
    projects_md: Optional[Dict[str, Any]] = None,
    sessions: Optional[Dict[str, List[Any]]] = None,
    hub_state: Optional[Dict[str, Any]] = None,
    snapshot_time: Optional[str] = None,
    hub_age_label: Optional[str] = None,
    disambiguation: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Produce full join of catalogue tiles and tracker projects into unified cards."""
    md_map = projects_md or {}
    sess_map = sessions or {}
    dis_map = disambiguation if disambiguation is not None else DEFAULT_DISAMBIGUATION

    tracker_keys = set(projects_yaml.keys()) | set(md_map.keys())

    # Map tiles to tracker keys
    tiles_by_tracker: Dict[str, List[Dict[str, Any]]] = {}
    unmatched_tiles: List[Dict[str, Any]] = []

    for t in tiles:
        t_key = resolve_tile_tracker_key(t, tracker_keys, dis_map)
        if t_key:
            tiles_by_tracker.setdefault(t_key, []).append(t)
        else:
            unmatched_tiles.append(t)

    cards: List[Dict[str, Any]] = []

    # 1. Process all tracker entities (matched with tiles if available)
    for t_key in sorted(tracker_keys):
        y_data = projects_yaml.get(t_key, {})
        m_data = md_map.get(t_key, {})
        matched_tiles = tiles_by_tracker.get(t_key, [])

        # Best tile for primary card action (prefer web service if any, else first project tile)
        primary_tile = None
        if matched_tiles:
            primary_tile = next((t for t in matched_tiles if (t.get("kind") or "web") == "web"), matched_tiles[0])
            if primary_tile and not primary_tile.get("kind"):
                primary_tile["kind"] = "web"

        scope = y_data.get("scope")
        if not scope and primary_tile:
            tags = primary_tile.get("tags", [])
            if "trabajo" in tags:
                scope = "work"
            elif "personal" in tags:
                scope = "personal"
        if not scope:
            scope = "personal"

        status = y_data.get("status")
        if not status:
            sec = m_data.get("section", "").lower()
            if "active" in sec:
                status = "active"
            elif "paused" in sec:
                status = "paused"
            elif "proposal" in sec:
                status = "proposal"
            elif "closed" in sec:
                status = "closed"
            else:
                status = "cold"

        display_name = y_data.get("name") or (primary_tile.get("name") if primary_tile else None) or t_key
        desc = y_data.get("desc") or (primary_tile.get("description") if primary_tile else "")
        next_act = y_data.get("next")

        has_live_health = bool(primary_tile and primary_tile.get("kind") == "web" and primary_tile.get("url"))

        card = {
            "id": t_key,
            "anchor": normalize_key(t_key),
            "name": display_name,
            "scope": scope,
            "scope_label": SCOPE_LABEL.get(scope, "Personal"),
            "status": status,
            "status_label": STATUS_LABEL.get(status, status),
            "desc": desc,
            "next": next_act,
            "group": y_data.get("group"),
            "streams": y_data.get("streams"),
            "tile": primary_tile,
            "all_tiles": matched_tiles,
            "tracker": {
                "branch": m_data.get("branch"),
                "dirty": m_data.get("dirty", 0),
                "hours_week": m_data.get("hours_week", "0.0"),
                "hours_total": m_data.get("hours_total", "0.0"),
                "sessions": sess_map.get(t_key, []),
            },
            "freshness": {
                "snapshot_time": snapshot_time,
                "hub_age": hub_age_label,
                "has_live_health": has_live_health,
            },
        }
        cards.append(card)

    # 2. Process unmatched tiles (tiles that have no tracking entry in projects.yaml/PROJECTS.md)
    for t in unmatched_tiles:
        tags = t.get("tags", [])
        scope = "work" if "trabajo" in tags else "personal"
        tid = t.get("id", "")
        display_name = t.get("name") or tid
        kind = t.get("kind") or "web"
        if not t.get("kind"):
            t["kind"] = kind
        has_live_health = bool(kind == "web" and t.get("url"))

        card = {
            "id": tid,
            "anchor": normalize_key(tid),
            "name": display_name,
            "scope": scope,
            "scope_label": SCOPE_LABEL.get(scope, "Personal"),
            "status": "unclassified",
            "status_label": STATUS_LABEL["unclassified"],
            "desc": desc,
            "next": None,
            "group": None,
            "streams": None,
            "tile": t,
            "all_tiles": [t],
            "tracker": {
                "branch": None,
                "dirty": 0,
                "hours_week": "0.0",
                "hours_total": "0.0",
                "sessions": [],
            },
            "freshness": {
                "snapshot_time": snapshot_time,
                "hub_age": hub_age_label,
                "has_live_health": has_live_health,
            },
        }
        cards.append(card)

    return cards


NAV_JS = """(function () {
  var l = window.location;
  var web = (l.protocol === 'http:' || l.protocol === 'https:') && l.hostname;
  var base = (web ? l.protocol : 'http:') + '//' + (web ? l.hostname : 'localhost');
  var links = document.querySelectorAll('a[data-portal-port]');
  for (var i = 0; i < links.length; i++) {
    var a = links[i];
    a.setAttribute('href', base + ':' + a.getAttribute('data-portal-port') + a.getAttribute('data-portal-path'));
  }
})();"""


def render_proyectos_html(
    cards: List[Dict[str, Any]],
    snapshot_time: Optional[str] = None,
    hub_age_label: Optional[str] = None,
    webbase: Optional[str] = None,
) -> str:
    """Render standalone, responsive, interactive PROYECTOS.html portal page."""
    import html as html_lib

    wb_prefix = (webbase or "http://localhost:4097/server/aHR0cDovL2xvY2FsaG9zdDo0MDk3/session").rstrip("/")

    # Render card markup
    cards_html = []
    for c in cards:
        cid = c["id"]
        canchor = c["anchor"]
        name = html_lib.escape(c["name"])
        desc = html_lib.escape(c["desc"]) if c["desc"] else ""
        scope = c["scope"]
        scope_lbl = c["scope_label"]
        st = c["status"]
        st_lbl = c["status_label"]
        next_act = html_lib.escape(c["next"]) if c["next"] else ""

        tile = c.get("tile")
        tile_id = tile.get("id") if tile else ""
        icon = tile.get("icon") if tile and tile.get("icon") else "📦"
        kind = tile.get("kind") if tile else ""
        url = tile.get("url") if tile else ""
        loc = tile.get("location") if tile else ""
        cmd = tile.get("command") if tile else ""
        tags = " ".join(tile.get("tags", [])) if tile else ""

        # Tracking metrics
        tr = c.get("tracker", {})
        dirty = tr.get("dirty", 0)
        branch = tr.get("branch") or ""
        hours_tot = tr.get("hours_total", "0.0")
        hours_wk = tr.get("hours_week", "0.0")
        sessions = tr.get("sessions", [])

        # Search haystack
        haystack = html_lib.escape(
            f"{cid} {name} {desc} {next_act} {scope_lbl} {st_lbl} {loc} {cmd} {tags}".lower()
        )

        # Service / Health row
        access_html = ""
        if kind == "web" and url:
            access_html = f"""
            <div class="card-access web" data-tile-id="{html_lib.escape(tile_id)}">
              <span class="dot unknown" aria-hidden="true"></span>
              <a class="service-link" href="{html_lib.escape(url)}" target="_blank" rel="noopener">Abrir servicio ({html_lib.escape(url)})</a>
              <span class="health-meta">desconocido</span>
            </div>
            """
        elif loc:
            access_html = f"""
            <div class="card-access copy-row" data-copy="{html_lib.escape(loc)}" role="button" tabindex="0" title="Copiar ruta">
              <span class="copy-lbl">Ruta:</span>
              <code class="copy-val">{html_lib.escape(loc)}</code>
              <span class="copy-hint">Copiar</span>
            </div>
            """
        elif cmd:
            access_html = f"""
            <div class="card-access copy-row" data-copy="{html_lib.escape(cmd)}" role="button" tabindex="0" title="Copiar comando">
              <span class="copy-lbl">Cmd:</span>
              <code class="copy-val">{html_lib.escape(cmd)}</code>
              <span class="copy-hint">Copiar</span>
            </div>
            """

        # Next actionable block
        next_html = ""
        if next_act:
            next_html = f"""
            <div class="card-next">
              <span class="next-icon" aria-hidden="true">🎯</span>
              <div class="next-body"><strong>Siguiente acción:</strong> {next_act}</div>
            </div>
            """

        # Stats / sessions row
        stats_items = []
        if hours_tot and hours_tot != "0.0":
            stats_items.append(f'<span class="stat-item">⏱️ {hours_tot}h total</span>')
        if hours_wk and hours_wk != "0.0":
            stats_items.append(f'<span class="stat-item">⚡ {hours_wk}h esta semana</span>')
        if dirty and int(dirty) > 0:
            stats_items.append(f'<span class="stat-item warn">⚠️ {dirty} dirty</span>')
        if branch:
            stats_items.append(f'<span class="stat-item"><code>{html_lib.escape(branch)}</code></span>')

        stats_html = f'<div class="card-stats">{" · ".join(stats_items)}</div>' if stats_items else ""

        # Sessions links
        sess_html = ""
        if sessions:
            s_links = []
            for s in sessions[:3]:
                sid = s.get("id")
                stitle = html_lib.escape(s.get("title") or sid)
                s_links.append(f'<a class="ses-link" href="{wb_prefix}/{sid}" target="_blank" rel="noopener">{stitle}</a>')
            more = f" (+{len(sessions) - 3})" if len(sessions) > 3 else ""
            sess_html = f'<div class="card-sessions"><span class="ses-icon">💬</span> {len(sessions)} sesiones: {", ".join(s_links)}{more}</div>'

        # Freshness subline
        fresh_items = []
        if snapshot_time:
            fresh_items.append(f"Snapshot: {html_lib.escape(snapshot_time)}")
        if hub_age_label:
            fresh_items.append(f"Hub: {html_lib.escape(hub_age_label)}")
        if kind == "web" and url:
            fresh_items.append('<span class="fresh-health">Salud: polling 5s</span>')
        fresh_html = f'<div class="card-freshness">{" · ".join(fresh_items)}</div>' if fresh_items else ""

        card_markup = f"""
        <article class="card" id="{html_lib.escape(cid)}" data-scope="{scope}" data-status="{st}" data-search="{haystack}">
          <div class="card-header">
            <div class="card-avatar" aria-hidden="true">{html_lib.escape(icon)}</div>
            <div class="card-title-group">
              <h2 class="card-title"><a href="#{html_lib.escape(canchor)}" class="anchor-link">{name}</a></h2>
              <div class="card-badges">
                <span class="badge scope-{scope}">{scope_lbl}</span>
                <span class="badge status-{st}">{st_lbl}</span>
              </div>
            </div>
          </div>
          {f'<p class="card-desc">{desc}</p>' if desc else ''}
          {next_html}
          {stats_html}
          {sess_html}
          {access_html}
          {fresh_html}
        </article>
        """
        cards_html.append(card_markup)

    all_cards_markup = "\n".join(cards_html)

    doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proyectos · Ecosistema</title>
<style>
  :root {{
    --bg: #070b14; --card: #0d1524; --raise: #101c33; --line: #223047;
    --tx: #e8eef8; --dim: #9db0cc; --acc: #5ec2f7;
    --up: #34d399; --down: #f87171; --unknown: #9db0cc;
    --scope-work: #0284c7; --scope-pers: #8b5cf6;
    --st-act: #10b981; --st-prop: #f59e0b; --st-pau: #f97316;
    --st-cold: #64748b; --st-unclass: #475569;
  }}
  * {{ box-sizing: border-box; }}
  [hidden] {{ display: none !important; }}
  body {{
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--tx);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex; flex-direction: column;
  }}
  a {{ color: var(--acc); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{ color: #fcd34d; font-size: .88em; overflow-wrap: anywhere; }}
  :focus-visible {{ outline: 2px solid var(--acc); outline-offset: 2px; border-radius: 4px; }}
  .sr-only {{
    position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }}
  .skip {{
    position: absolute; left: .5rem; top: -4rem; z-index: 10; background: var(--acc); color: #04121c;
    padding: .5rem .9rem; border-radius: .4rem; font-weight: 600; text-decoration: none;
  }}
  .skip:focus {{ top: .5rem; }}

  /* Shared Ecosistema header */
  .site {{ background: #0a1220; border-bottom: 1px solid var(--line); }}
  .site-inner {{
    max-width: 76rem; margin: 0 auto; padding: .6rem 1rem;
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .4rem 1.5rem;
  }}
  .brand {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: .1rem .7rem; }}
  .brand-name {{ font-size: 1.1rem; font-weight: 700; letter-spacing: .02em; color: var(--acc); }}
  .brand-sub {{ color: var(--dim); font-size: .82rem; }}
  .site nav {{ display: flex; flex-wrap: wrap; gap: .3rem; }}
  .site nav a {{
    display: inline-flex; align-items: center; min-height: 2.5rem; padding: 0 1rem;
    border: 1px solid transparent; border-radius: .5rem; color: var(--dim);
    text-decoration: none; font-weight: 600; transition: background .15s, color .15s;
  }}
  .site nav a:hover {{ color: var(--tx); background: var(--card); }}
  .site nav a[aria-current="page"] {{ color: var(--tx); background: var(--raise); border-color: var(--acc); }}

  .page {{ flex: 1; width: 100%; max-width: 76rem; margin: 0 auto; padding: 1.4rem 1rem 2rem; }}
  h1 {{ font-size: 1.5rem; line-height: 1.2; margin: 0; }}
  .lead {{ color: var(--dim); margin: .35rem 0 0; max-width: 48rem; }}

  /* Controls and Filters */
  .toolbar {{ margin: 1.2rem 0 .6rem; display: grid; gap: .8rem; }}
  .searchrow {{ display: flex; flex-wrap: wrap; gap: .6rem; }}
  .search {{
    flex: 1 1 16rem; max-width: 32rem; min-height: 2.6rem; padding: 0 .9rem;
    background: var(--card); border: 1px solid var(--line); border-radius: .55rem;
    color: var(--tx); font: inherit;
  }}
  .search:focus-visible {{ border-color: var(--acc); outline-offset: 0; }}
  .search::placeholder {{ color: var(--dim); }}
  .btn {{
    min-height: 2.6rem; padding: 0 1rem; background: transparent; color: var(--tx); font: inherit;
    border: 1px solid var(--line); border-radius: .55rem; cursor: pointer;
  }}
  .btn:hover {{ border-color: var(--acc); }}
  .facet {{ display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .8rem; }}
  .facet-label {{
    flex: none; min-width: 4.5rem; color: var(--dim); font-size: .75rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: .08em;
  }}
  .chips {{ display: flex; flex-wrap: wrap; gap: .4rem; }}
  .chip {{
    min-height: 2.25rem; padding: 0 .85rem; display: inline-flex; align-items: center; gap: .45rem;
    background: var(--card); border: 1px solid var(--line); border-radius: 999px;
    color: var(--dim); cursor: pointer; font: inherit; font-size: .88rem;
    transition: border-color .15s, color .15s;
  }}
  .chip:hover {{ color: var(--tx); border-color: var(--dim); }}
  .chip[aria-pressed="true"] {{ background: var(--raise); border-color: var(--acc); color: var(--tx); }}
  .chip .n {{ color: var(--dim); font-size: .78rem; font-variant-numeric: tabular-nums; }}
  .chip[aria-pressed="true"] .n {{ color: var(--acc); }}
  .summary {{ margin: 0; color: var(--dim); font-size: .85rem; min-height: 1.3em; }}

  /* Cards Grid */
  .grid {{ display: grid; gap: .9rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); margin-top: 1rem; }}
  .card {{
    display: flex; flex-direction: column; gap: .65rem; min-width: 0; width: 100%;
    background: var(--card); border: 1px solid var(--line); border-radius: .75rem;
    padding: 1rem; color: var(--tx); text-align: left; transition: border-color .15s;
  }}
  .card:hover {{ border-color: #3b5070; }}
  .card-header {{ display: flex; align-items: flex-start; gap: .75rem; }}
  .card-avatar {{
    width: 2.4rem; height: 2.4rem; border-radius: .5rem; flex: none;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.25rem; background: var(--bg); border: 1px solid var(--line);
  }}
  .card-title-group {{ min-width: 0; flex: 1; }}
  .card-title {{ font-size: 1.1rem; margin: 0; line-height: 1.25; overflow-wrap: anywhere; }}
  .card-title a {{ color: var(--tx); text-decoration: none; }}
  .card-title a:hover {{ color: var(--acc); text-decoration: underline; }}
  .card-badges {{ display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .3rem; }}
  .badge {{
    font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    padding: .15rem .5rem; border-radius: 999px; border: 1px solid transparent;
  }}
  .badge.scope-work {{ background: #0c2b45; color: #7dd3fc; border-color: #0284c7; }}
  .badge.scope-personal {{ background: #24133b; color: #d8b4fe; border-color: #8b5cf6; }}
  .badge.status-active {{ background: #064e3b; color: #6ee7b7; border-color: #059669; }}
  .badge.status-proposal {{ background: #451a03; color: #fcd34d; border-color: #d97706; }}
  .badge.status-paused {{ background: #431407; color: #fdba74; border-color: #ea580c; }}
  .badge.status-cold {{ background: #1e293b; color: #94a3b8; border-color: #475569; }}
  .badge.status-closed {{ background: #1e293b; color: #64748b; border-color: #334155; }}
  .badge.status-unclassified {{ background: #172033; color: #9db0cc; border-color: #223047; }}

  .card-desc {{ color: var(--dim); font-size: .88rem; margin: 0; line-height: 1.4; overflow-wrap: anywhere; }}

  /* Next actionable */
  .card-next {{
    display: flex; gap: .5rem; background: #17243c; border: 1px solid #2a4066;
    border-radius: .45rem; padding: .55rem .75rem; font-size: .85rem; color: #e2e8f0;
  }}
  .next-icon {{ flex: none; }}
  .next-body {{ overflow-wrap: anywhere; }}

  /* Stats & sessions */
  .card-stats {{ font-size: .8rem; color: var(--dim); display: flex; flex-wrap: wrap; gap: .3rem; }}
  .stat-item.warn {{ color: #fbbf24; }}
  .card-sessions {{ font-size: .8rem; color: var(--dim); overflow-wrap: anywhere; }}
  .ses-link {{ color: var(--acc); }}

  /* Access: web vs copy */
  .card-access {{
    margin-top: auto; padding-top: .5rem; border-top: 1px solid var(--line);
    font-size: .82rem;
  }}
  .card-access.web {{ display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }}
  .service-link {{ font-weight: 600; color: var(--acc); overflow-wrap: anywhere; }}
  .health-meta {{ color: var(--dim); font-size: .75rem; margin-left: auto; }}
  .dot {{ width: .55rem; height: .55rem; border-radius: 50%; background: var(--unknown); flex: none; }}
  .dot.up {{ background: var(--up); }}
  .dot.down {{ background: var(--down); }}

  .copy-row {{
    display: flex; align-items: center; gap: .5rem; cursor: pointer;
    background: var(--bg); border: 1px solid var(--line); border-radius: .4rem;
    padding: .35rem .6rem; transition: border-color .15s;
  }}
  .copy-row:hover {{ border-color: var(--acc); }}
  .copy-lbl {{ color: var(--dim); font-size: .75rem; flex: none; font-weight: 600; }}
  .copy-val {{ flex: 1; font: .8rem ui-monospace, monospace; color: var(--tx); }}
  .copy-hint {{ color: var(--acc); font-size: .75rem; font-weight: 600; flex: none; }}
  .copy-hint.copied {{ color: var(--up); }}
  .copy-hint.failed {{ color: var(--down); }}

  .card-freshness {{ font-size: .72rem; color: #64748b; margin-top: .2rem; }}

  .empty {{ color: var(--dim); padding: 3rem 0; text-align: center; }}
  footer {{ color: var(--dim); font-size: .8rem; padding: 1rem; border-top: 1px solid var(--line); margin-top: 2rem; }}
  footer div {{ max-width: 76rem; margin: 0 auto; }}

  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{ transition: none !important; animation: none !important; scroll-behavior: auto !important; }}
  }}
</style>
</head>
<body>
<a class="skip" href="#main">Saltar al contenido</a>
<header class="site">
  <div class="site-inner">
    <div class="brand">
      <span class="brand-name">Ecosistema</span>
      <span class="brand-sub">Portal local</span>
    </div>
    <nav aria-label="Secciones del ecosistema">
      <a href="http://localhost:47625/" data-portal-port="47625" data-portal-path="/">Catálogo</a>
      <a href="/PROYECTOS.html" data-portal-port="47624" data-portal-path="/PROYECTOS.html" aria-current="page">Proyectos</a>
      <a href="/PROJECTS.html" data-portal-port="47624" data-portal-path="/PROJECTS.html">Seguimiento</a>
    </nav>
  </div>
</header>
<main id="main" class="page">
  <h1>Proyectos</h1>
  <p class="lead">Ficha unificada del ecosistema: salud y acceso al servicio (catálogo) combinados con estado del ciclo de vida, horas y sesiones (seguimiento).</p>

  <section class="toolbar" aria-label="Búsqueda y filtros">
    <div class="searchrow">
      <label class="sr-only" for="search">Buscar proyectos</label>
      <input class="search" id="search" type="search" autocomplete="off"
        placeholder="Buscar por nombre, descripción, estado, accionable, ruta…">
      <button type="button" class="btn" id="reset" hidden>Limpiar filtros</button>
    </div>
    <div class="facet">
      <span class="facet-label" id="scope-lbl">Ámbito</span>
      <div class="chips" id="scope-chips" role="group" aria-labelledby="scope-lbl">
        <button type="button" class="chip" data-scope="all" aria-pressed="true">Todos <span class="n" id="n-scope-all"></span></button>
        <button type="button" class="chip" data-scope="personal" aria-pressed="false">Personal <span class="n" id="n-scope-pers"></span></button>
        <button type="button" class="chip" data-scope="work" aria-pressed="false">Trabajo <span class="n" id="n-scope-work"></span></button>
      </div>
    </div>
    <div class="facet">
      <span class="facet-label" id="status-lbl">Estado</span>
      <div class="chips" id="status-chips" role="group" aria-labelledby="status-lbl">
        <button type="button" class="chip" data-status="all" aria-pressed="true">Todos <span class="n" id="n-st-all"></span></button>
        <button type="button" class="chip" data-status="active" aria-pressed="false">Activos <span class="n" id="n-st-act"></span></button>
        <button type="button" class="chip" data-status="proposal" aria-pressed="false">Propuestas <span class="n" id="n-st-prop"></span></button>
        <button type="button" class="chip" data-status="paused" aria-pressed="false">Pausados <span class="n" id="n-st-pau"></span></button>
        <button type="button" class="chip" data-status="cold" aria-pressed="false">Fríos <span class="n" id="n-st-cold"></span></button>
        <button type="button" class="chip" data-status="unclassified" aria-pressed="false">Sin clasificar <span class="n" id="n-st-unclass"></span></button>
      </div>
    </div>
    <p class="summary" id="summary" role="status"></p>
  </section>

  <div class="grid" id="projects-grid">
{all_cards_markup}
  </div>

  <div class="empty" id="noresults" hidden>
    <p>Ningún proyecto coincide con los filtros actuales.</p>
    <button type="button" class="btn" id="noresults-reset">Limpiar filtros</button>
  </div>
  <div class="sr-only" id="live" role="status"></div>
</main>
<footer><div>Ecosistema · Ficha unificada de proyectos · Sin dependencias remotas</div></footer>

<script id="portal-nav">
{NAV_JS}
</script>

<script>
(function () {{
  const $ = id => document.getElementById(id);
  const cards = Array.from(document.querySelectorAll('.card'));
  let SCOPE = 'all';
  let STATUS = 'all';
  let QUERY = '';

  function update() {{
    let visible = 0;
    const q = QUERY.toLowerCase().trim();

    cards.forEach(card => {{
      const matchScope = SCOPE === 'all' || card.getAttribute('data-scope') === SCOPE;
      const matchStatus = STATUS === 'all' || card.getAttribute('data-status') === STATUS;
      const matchQuery = !q || card.getAttribute('data-search').includes(q);

      const show = matchScope && matchStatus && matchQuery;
      card.hidden = !show;
      if (show) visible++;
    }});

    $('summary').textContent = visible + ' proyecto' + (visible === 1 ? '' : 's') + ' mostrado' + (visible === 1 ? '' : 's');
    $('noresults').hidden = visible > 0;
    $('reset').hidden = (SCOPE === 'all' && STATUS === 'all' && !QUERY);

    // Update chip counts
    updateChipCounts();
  }}

  function updateChipCounts() {{
    const q = QUERY.toLowerCase().trim();
    // Scope counts given status and query
    ['all', 'personal', 'work'].forEach(sc => {{
      const cnt = cards.filter(c => (sc === 'all' || c.getAttribute('data-scope') === sc) &&
                                   (STATUS === 'all' || c.getAttribute('data-status') === STATUS) &&
                                   (!q || c.getAttribute('data-search').includes(q))).length;
      const el = $('n-scope-' + (sc === 'all' ? 'all' : sc === 'personal' ? 'pers' : 'work'));
      if (el) el.textContent = '(' + cnt + ')';
    }});

    // Status counts given scope and query
    const stKeys = [
      ['all', 'all'], ['active', 'act'], ['proposal', 'prop'],
      ['paused', 'pau'], ['cold', 'cold'], ['unclassified', 'unclass']
    ];
    stKeys.forEach(([st, idSuffix]) => {{
      const cnt = cards.filter(c => (st === 'all' || c.getAttribute('data-status') === st) &&
                                   (SCOPE === 'all' || c.getAttribute('data-scope') === SCOPE) &&
                                   (!q || c.getAttribute('data-search').includes(q))).length;
      const el = $('n-st-' + idSuffix);
      if (el) el.textContent = '(' + cnt + ')';
    }});
  }}

  function resetFilters() {{
    SCOPE = 'all'; STATUS = 'all'; QUERY = '';
    $('search').value = '';
    document.querySelectorAll('#scope-chips .chip').forEach(c => c.setAttribute('aria-pressed', c.getAttribute('data-scope') === 'all'));
    document.querySelectorAll('#status-chips .chip').forEach(c => c.setAttribute('aria-pressed', c.getAttribute('data-status') === 'all'));
    update();
    $('search').focus();
  }}

  $('search').addEventListener('input', e => {{
    QUERY = e.target.value;
    update();
  }});

  $('scope-chips').addEventListener('click', e => {{
    const chip = e.target.closest('[data-scope]');
    if (!chip) return;
    const val = chip.getAttribute('data-scope');
    SCOPE = (val === SCOPE && val !== 'all') ? 'all' : val;
    document.querySelectorAll('#scope-chips .chip').forEach(c => c.setAttribute('aria-pressed', c.getAttribute('data-scope') === SCOPE));
    update();
  }});

  $('status-chips').addEventListener('click', e => {{
    const chip = e.target.closest('[data-status]');
    if (!chip) return;
    const val = chip.getAttribute('data-status');
    STATUS = (val === STATUS && val !== 'all') ? 'all' : val;
    document.querySelectorAll('#status-chips .chip').forEach(c => c.setAttribute('aria-pressed', c.getAttribute('data-status') === STATUS));
    update();
  }});

  $('reset').addEventListener('click', resetFilters);
  $('noresults-reset').addEventListener('click', resetFilters);

  // Clipboard copy handler
  function copyText(val, hintEl) {{
    function ok() {{
      if (hintEl) {{
        hintEl.textContent = 'Copiado ✓';
        hintEl.className = 'copy-hint copied';
        setTimeout(() => {{
          hintEl.textContent = 'Copiar';
          hintEl.className = 'copy-hint';
        }}, 2000);
      }}
      $('live').textContent = 'Copiado al portapapeles: ' + val;
    }}
    function fail() {{
      if (hintEl) {{
        hintEl.textContent = 'Error al copiar';
        hintEl.className = 'copy-hint failed';
      }}
      $('live').textContent = 'No se pudo copiar';
    }}
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(val).then(ok, fail);
    }} else {{
      try {{
        const ta = document.createElement('textarea');
        ta.value = val; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        const success = document.execCommand('copy');
        document.body.removeChild(ta);
        if (success) ok(); else fail();
      }} catch (err) {{ fail(); }}
    }}
  }}

  $('projects-grid').addEventListener('click', e => {{
    const copyTarget = e.target.closest('[data-copy]');
    if (copyTarget) {{
      const val = copyTarget.getAttribute('data-copy');
      const hint = copyTarget.querySelector('.copy-hint');
      copyText(val, hint);
    }}
  }});

  $('projects-grid').addEventListener('keydown', e => {{
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const copyTarget = e.target.closest('[data-copy]');
    if (copyTarget) {{
      e.preventDefault();
      const val = copyTarget.getAttribute('data-copy');
      const hint = copyTarget.querySelector('.copy-hint');
      copyText(val, hint);
    }}
  }});

  // Live health polling from Launchpad (47625)
  function pollHealth() {{
    const l = window.location;
    const web = (l.protocol === 'http:' || l.protocol === 'https:') && l.hostname;
    const apiUrl = (web ? l.protocol : 'http:') + '//' + (web ? l.hostname : 'localhost') + ':47625/api/tiles';

    fetch(apiUrl)
      .then(r => r.json())
      .then(data => {{
        const tiles = data.tiles || data;
        if (!Array.isArray(tiles)) return;
        const byId = {{}};
        tiles.forEach(t => byId[t.id] = t);

        document.querySelectorAll('.card-access.web[data-tile-id]').forEach(row => {{
          const tid = row.getAttribute('data-tile-id');
          const t = byId[tid];
          if (!t) return;
          const dot = row.querySelector('.dot');
          const meta = row.querySelector('.health-meta');
          if (!dot || !meta) return;

          const state = t.state || (t.health ? t.health.state : 'unknown');
          const latency = t.latency_ms || (t.health ? t.health.latency_ms : null);

          dot.className = 'dot ' + (state === 'up' ? 'up' : state === 'down' ? 'down' : 'unknown');
          meta.textContent = state === 'up' ? (latency ? latency + ' ms' : 'activo')
                           : state === 'down' ? 'caído'
                           : 'desconocido';
        }});
      }})
      .catch(() => {{ /* keep calm and degrade gracefully */ }});
  }}

  update();
  pollHealth();
  setInterval(pollHealth, 5000);
}})();
</script>
</body>
</html>"""
    return doc
