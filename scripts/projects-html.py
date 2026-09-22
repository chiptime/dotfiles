#!/usr/bin/env python3
"""projects-html.py — project-centric web dashboard (card UI, v3.1).

Design: developer-dashboard profile per ui-ux-pro-max checklist — darker theme,
higher contrast, SVG icons (no emoji-as-icon), text badges (never color-only),
resilient reflow, responsive 375px+, focus-visible, prefers-reduced-motion.

Data sources:
  argv[1]  PROJECTS.md (git truth tables from projects-dashboard.sh)
  ~/.dotfiles/scripts/projects.yaml       status/desc/next/group (human layer)
  ~/.local/share/time-ledger/entries.csv  week hours + linked conversations
  OpenCode API http://127.0.0.1:4096      /project + /session?directory=...
  ~/.local/share/time-ledger/webbase      FULL session-link prefix
                                          (default http://localhost:4097/server/<b64>/session)
  hub-state.json (sibling of PROJECTS.md) cockpit hub sections — hub-state/v2
                                          copied by the sweep after its hub pull
Output: PROJECTS.html next to input. Best-effort: exits 0 if inputs missing.
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

md_path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/.local/share/projects-dashboard/PROJECTS.md")
if not os.path.exists(md_path):
    sys.exit(0)
out_path = os.path.join(os.path.dirname(md_path), "PROJECTS.html")
HOME = os.path.expanduser("~")
OC_API = "http://127.0.0.1:4096"

WEBBASE_DEFAULT = "http://localhost:4097/server/aHR0cDovL2xvY2FsaG9zdDo0MDk3/session"
wb = os.path.join(HOME, ".local/share/time-ledger/webbase")
webbase = (open(wb).read().strip().rstrip("/") if os.path.exists(wb) and open(wb).read().strip()
           else WEBBASE_DEFAULT)

STATUS_SECTION = {"Active": "active", "Proposal": "proposal", "Paused": "paused",
                  "Closed": "closed", "Cold": "cold", "Unclassified": "unclassified"}
STATUS_TITLE = {"active": "Activos", "proposal": "Propuestas pendientes de decisión",
                "paused": "Pausados", "closed": "Cerrados", "cold": "Fríos / legacy",
                "unclassified": "Sin clasificar"}
STATUS_LABEL = {"active": "activo", "proposal": "propuesta", "paused": "pausado",
                "closed": "cerrado", "cold": "frío", "unclassified": "sin clasificar"}
ACTIONABLE_STATUSES = ("active", "proposal", "paused")
Y_META = {"scope", "status", "desc", "next", "name", "group", "streams"}

# ---------- projects.yaml ----------
projects = {}
yp = os.path.join(HOME, ".dotfiles/scripts/projects.yaml")
if os.path.exists(yp):
    cur = None
    for line in open(yp, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.fullmatch(r"([a-zA-Z0-9_.-]+):", line)
        if m:
            cur = m.group(1)
            projects.setdefault(cur, {})
        elif cur and line.startswith("  ") and ": " in line:
            k, v = line.strip().split(": ", 1)
            if k in Y_META:
                projects[cur][k] = v

# ---------- PROJECTS.md tables ----------
SECTION_RE = re.compile(r"^## (.*?) \((\d+)\)\s*$")
ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*`(.+?)`\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$")
sections, current = [], None
for line in open(md_path, encoding="utf-8"):
    line = line.rstrip("\n")
    sm = SECTION_RE.match(line)
    if sm:
        raw_title, count = sm.group(1).strip(), int(sm.group(2))
        clean = re.sub(r"^[^\w]+", "", raw_title)
        key = next((v for k, v in STATUS_SECTION.items() if clean.startswith(k)), "cold")
        current = {"key": key, "rows": []}
        sections.append(current)
        continue
    rm = ROW_RE.match(line)
    if rm and current is not None:
        name, branch, gcell, _h, _n = rm.groups()
        toks = gcell.split()
        d = toks[0] if toks else ""
        dirty = ahead = behind = ""
        for t in toks[1:]:
            if t.lstrip("⚠\ufe0f").isdigit():
                dirty = t.lstrip("⚠\ufe0f")
            elif t.startswith("↑"):
                ahead = t[1:]
            elif t.startswith("↓"):
                behind = t[1:]
        current["rows"].append({"name": name, "branch": branch, "date": d,
                                "dirty": dirty, "ahead": ahead, "behind": behind})

# ---------- ledger ----------
mon = date.today() - timedelta(days=date.today().weekday())
ledger = {}
week_total = 0.0
csv_path = os.path.join(HOME, ".local/share/time-ledger/entries.csv")
if os.path.exists(csv_path):
    for line in open(csv_path, encoding="utf-8"):
        parts = line.rstrip("\n").split(",")
        if len(parts) < 5 or parts[0] == "date":
            continue
        d, p, h = parts[0], parts[1], float(parts[2] or 0)
        e = ledger.setdefault(p, {"week": 0.0, "entries": []})
        e["entries"].append((d, h, parts[3], parts[4] if len(parts) > 4 else "",
                             parts[5] if len(parts) > 5 else ""))
        try:
            if date.fromisoformat(d) >= mon:
                e["week"] += h
                week_total += h
        except ValueError:
            pass
    for e in ledger.values():
        e["entries"].sort(reverse=True)

# ---------- OpenCode sessions (top-level only: no parentID) ----------
def fetch(url, timeout=4, headers=None):
    try:
        request = urllib.request.Request(url, headers=headers) if headers else url
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None

project_response = fetch(f"{OC_API}/project")
oc_unavailable = not isinstance(project_response, list)
oc_projects = {p["worktree"] for p in project_response or []
               if isinstance(p, dict) and p.get("worktree")} if not oc_unavailable else set()
oc_sessions = {}  # basename -> [(when, ses_id, title)] for existing catalog cards
session_projects = {}  # catalog parent or uncatalogued directory -> sessions
for wt in sorted(oc_projects):
    # The API defaults to 100 rows; request all roots, not a recent activity window.
    params = {"roots": "true", "limit": 2147483647}
    # Select the global instance without filtering out its actual session directories.
    if wt != "/":
        params["directory"] = wt
    query = urllib.parse.urlencode(params)
    response = fetch(f"{OC_API}/session?{query}",
                     headers={"x-opencode-directory": "/"} if wt == "/" else None)
    if not isinstance(response, list):
        oc_unavailable = True
        continue
    for s in response:
        if not isinstance(s, dict) or s.get("parentID"):
            continue
        ses = s.get("id", "")
        if not isinstance(ses, str) or not ses.startswith("ses_"):
            continue
        # Worktree sessions belong to their API project; global sessions retain their directory.
        directory = wt if wt != "/" else s.get("directory") or wt
        name = os.path.basename(directory.rstrip("/")).lstrip(".") or "OpenCode"
        meta = projects.get(name, {})
        parent = meta.get("group", name)
        group_key = ("catalog", parent) if name in projects else ("directory", directory)
        display = projects.get(parent, {}).get("name", parent)
        t = s.get("time") or {}
        ms = t.get("updated") or t.get("created") or 0
        when = datetime.fromtimestamp(ms / 1000) if ms else datetime.min
        row = (when, ses, s.get("title") or "sesión")
        oc_sessions.setdefault(name, []).append(row)
        group = session_projects.setdefault(group_key, {"display": display, "sessions": {}})
        group["sessions"][ses] = row

# ---------- multi-agent sessions (Phase 4): pi + agy (facts only, no links) ----------
# pi: ~/.pi/agent/sessions/--encoded-path--/  (directory name encodes the project path)
# agy: ~/.gemini/antigravity/code_tracker/active/<project>_<hash>/ (name carries the project)
agent_sessions = {}  # basename -> {"pi"|"agy": {"count", "last_dt"}}
RECENT = datetime.now() - timedelta(days=14)

def _bump(name, src, when, count):
    entry = agent_sessions.setdefault(name, {})
    cur = entry.get(src)
    if not cur or when > cur["last_dt"]:
        entry[src] = {"count": count, "last_dt": when}
    else:
        cur["count"] += count

def _scan_files(root):
    best, recent = datetime.min, 0
    for _root, _dirs, files in os.walk(root):
        for f in files:
            try:
                m = datetime.fromtimestamp(os.path.getmtime(os.path.join(_root, f)))
            except OSError:
                continue
            if m >= RECENT:
                recent += 1
            best = max(best, m)
    return best, recent

_pi_dir = os.path.expanduser("~/.pi/agent/sessions")
if os.path.isdir(_pi_dir):
    for d in os.listdir(_pi_dir):
        if not (d.startswith("--") and d.endswith("--")):
            continue
        best, recent = _scan_files(os.path.join(_pi_dir, d))
        if recent:
            real = "/" + d[2:-2].replace("-", "/").lstrip("/")
            _bump(os.path.basename(real.rstrip("/")) or real, "pi", best, recent)

_agy_dir = os.path.expanduser("~/.gemini/antigravity/code_tracker/active")
if os.path.isdir(_agy_dir):
    for d in os.listdir(_agy_dir):
        best, recent = _scan_files(os.path.join(_agy_dir, d))
        if recent:
            _bump(re.sub(r"_[0-9a-f]{16,}$", "", d), "agy", best, recent)

# ---------- units: fold YAML groups (e.g. Products/Planificador) ----------
# unit = dict(section_key, display, git, meta_key, members[list of names])
units, grouped = [], {}
for s in sections:
    for r in s["rows"]:
        g = projects.get(r["name"], {}).get("group")
        if g:
            grouped.setdefault(g, {"members": [], "section": s["key"]})
            grouped[g]["members"].append(r)
        else:
            units.append({"section": s["key"], "display": r["name"],
                          "names": [r["name"]], "git": r})

for g, info in grouped.items():
    meta = projects.get(g, {})
    members = info["members"]
    branches = sorted({m["branch"] for m in members})
    dates = sorted(m["date"] for m in members if m["date"])
    gits = {"name": g,
            "branch": branches[0] if len(branches) == 1 else f"{len(branches)} ramas",
            "date": dates[-1] if dates else "",
            "dirty": str(sum(int(m["dirty"] or 0) for m in members)) or "",
            "ahead": str(sum(int(m["ahead"] or 0) for m in members)) or "",
            "behind": ""}
    if not gits["dirty"]:
        gits["dirty"] = ""
    sec = next((s["key"] for s in sections if s["key"] == meta.get("status")),
               info["section"])
    units.append({"section": sec, "display": meta.get("name", g),
                  "names": [m["name"] for m in members], "git": gits})

section_units = {}
for u in units:
    section_units.setdefault(u["section"], []).append(u)

# ---------- icons ----------
I_CLOCK = ('<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>')
I_CHAT = ('<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/></svg>')
I_ALERT = ('<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3l10 18H2L12 3z"/><path d="M12 10v4M12 17.5v.5"/></svg>')
I_TARGET = ('<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2"/></svg>')

def esc(t): return html.escape(str(t), quote=True)

def when_label(dt):
    if not dt:
        return ""
    today = datetime.now().date()
    d = dt.date()
    if d == today:
        return "hoy"
    if d == today - timedelta(days=1):
        return "ayer"
    return d.strftime("%d/%m")

def card(u):
    name = u["display"]
    git = u["git"]
    meta = projects.get(u["names"][0], {}) if len(u["names"]) == 1 else projects.get(
        next((n for n in [u["display"]] + u["names"] if n in projects), u["names"][0]), {})
    if len(u["names"]) > 1 or u["display"] not in projects or projects[u["display"]].get("name"):
        meta = projects.get(u["names"][0], meta) if len(u["names"]) == 1 else meta
    parent_key = u["display"] if u["display"] in projects else (
        u["names"][0] if len(u["names"]) == 1 else u["names"][0])
    if len(u["names"]) > 1:
        meta = projects.get(u["names"][0], {})  # members share group config; parent meta below
    # resolve meta: for groups, parent block holds desc/next/status
    if len(u["names"]) > 1:
        gkey = next((k for k, v in projects.items() if v.get("name") == u["display"] or k == u["display"]), None)
        meta = projects.get(gkey, meta) if gkey else meta
    status = meta.get("status") or "cold"

    led = {"week": 0.0, "entries": []}
    for n in u["names"]:
        e = ledger.get(n)
        if e:
            led["week"] += e["week"]
            led["entries"].extend(e["entries"])
    led["entries"].sort(reverse=True)

    badges = [f"<code>{esc(git['branch'])}</code>"]
    if git["dirty"]:
        badges.append(f'<span class="badge warn">{I_ALERT}{esc(git["dirty"])} dirty</span>')
    if git["ahead"] and git["ahead"] != "0":
        badges.append(f'<span class="badge info">↑{esc(git["ahead"])} sin push</span>')
    if git["behind"] and git["behind"] != "0":
        badges.append(f'<span class="badge info">↓{esc(git["behind"])}</span>')
    badges.append(f'<span class="badge">últ. {esc(git["date"] or "—")}</span>')
    if len(u["names"]) > 1:
        badges.append(f'<span class="badge">{len(u["names"])} repos</span>')

    hours_html = ""
    if led["week"] > 0:
        hours_html = (f'<p class="hours">{I_CLOCK}<strong>{("%g" % led["week"]).replace(".", ",")}h</strong>'
                      f'&nbsp;esta semana</p>')

    next_html = (f'<p class="next"><span class="nlabel">Next</span> {esc(meta.get("next", ""))}</p>'
                 if meta.get("next") else "")
    streams_html = ""
    if meta.get("streams"):
        pills = "".join(f'<span class="stream">{esc(x.strip())}</span>'
                        for x in meta["streams"].split("·") if x.strip())
        streams_html = (f'<div class="convtitle">Streams</div><div class="streams">{pills}</div>')

    conv = []
    for n in u["names"]:
        for when, ses, title in sorted(oc_sessions.get(n, []), reverse=True)[:4]:
            if ses and ses.startswith("ses_"):
                conv.append((when or datetime.min, ses, title, n))
    conv.sort(key=lambda x: x[0], reverse=True)
    conv_html_items = []
    for _w, ses, title, _n in conv[:4]:
        conv_html_items.append(
            f'<li><a href="{esc(webbase)}/{esc(ses)}" target="_blank" rel="noopener">'
            f'{I_CHAT}{esc(title)}</a></li>')
    for d, h, src, note, ses in led["entries"][:3]:
        if ses and ses.startswith("ses_"):
            continue
        conv_html_items.append(
            f'<li>{I_CHAT}<span>{esc(note) or "apunte"}</span>'
            f'<span class="dim">{esc(d[8:10])}/{esc(d[5:7])} · {esc(h)}g · {esc(src)}</span></li>')
    conv_html = (f'<div class="convtitle">Sesiones</div><ul class="conv">{"".join(conv_html_items)}</ul>'
                 if conv_html_items else "")

    return f"""<article class="card" id="{esc(name)}">
  <header><span class="dot {esc(status)}"></span><h3>{esc(name)}</h3>
    <span class="status st-{esc(status)}">{esc(STATUS_LABEL.get(status, status))}</span></header>
  <p class="desc">{esc(meta.get('desc', ''))}</p>
  <div class="badges">{''.join(badges)}</div>
  {streams_html}{hours_html}{next_html}{conv_html}
</article>"""

chips, body_sections = [], []
grand_counts = {}
for s in sections:
    us = section_units.get(s["key"], [])
    if not us:
        continue
    cards = "".join(card(u) for u in us)
    grand_counts[s["key"]] = len(us)
    chips.append(f'<a class="chip" href="#sec-{s["key"]}">'
                 f'{esc(STATUS_TITLE.get(s["key"], s["key"]))} <strong>{len(us)}</strong></a>')
    open_attr = " open" if s["key"] in ("active", "proposal") else ""
    body_sections.append(
        f'<section id="sec-{s["key"]}"><details{open_attr}><summary>'
        f'<h2>{esc(STATUS_TITLE.get(s["key"], s["key"]))} <span class="count">{len(us)}</span></h2>'
        f'<span class="tog" aria-hidden="true"></span></summary>'
        f'<div class="grid">{cards}</div></details></section>')

act = []
for u in units:
    if u["section"] not in ACTIONABLE_STATUSES:
        continue
    meta = projects.get(u["display"], projects.get(u["names"][0], {}))
    if len(u["names"]) > 1:
        gkey = next((k for k, v in projects.items()
                     if v.get("name") == u["display"] or k == u["display"]), None)
        meta = projects.get(gkey, meta) if gkey else meta
    nxt = meta.get("next")
    if not nxt:
        continue
    ev = []
    if u["git"]["dirty"]:
        ev.append(f'{u["git"]["dirty"]} dirty')
    if u["git"]["ahead"]:
        ev.append(f'↑{u["git"]["ahead"]}')
    evtxt = f'<span class="ev">{esc(" · ".join(ev))}</span>' if ev else ""
    act.append(f'<li><a class="proj" href="#{esc(u["display"])}">{esc(u["display"])}</a> — '
               f'{esc(nxt)} {evtxt}</li>')
act_html = "".join(act) or "<li>Nada pendiente</li>"

# ---------- cockpit del hub (cockpit-view R1-R3, hub-state.json v2) -----------
# Every hub datum below comes EXCLUSIVELY from the sibling hub-state.json v2 —
# NEVER from dashboard.md. The index is keyed by slug AND repos[] AND unit
# display name (hub slugs are number-prefixed, units are not, aliases are 1:N).
# Missing/unparsable sibling ⇒ explicit "sin datos del hub" state instead of
# stale numbers presented as current; sensor hub columns stay empty (fallback).
hub_doc = None
hub_index = {}
try:
    with open(os.path.join(os.path.dirname(md_path), "hub-state.json"), encoding="utf-8") as f:
        hub_doc = json.load(f)
except (OSError, ValueError):
    hub_doc = None
if not isinstance(hub_doc, dict):
    hub_doc = None
if hub_doc is not None:
    for _e in hub_doc.get("projects") or []:
        if not isinstance(_e, dict):
            continue
        for _k in [_e.get("slug"), _e.get("unit")] + list(_e.get("repos") or []):
            if isinstance(_k, str) and _k and _k not in hub_index:
                hub_index[_k] = _e

def _hub_projects():
    ps = hub_doc.get("projects") if hub_doc is not None else None
    return [p for p in ps if isinstance(p, dict)] if isinstance(ps, list) else []

def _age_label(h):
    if h < 1:
        return f"{max(0, round(h * 60))} min"
    if h < 48:
        return f"{round(h)} h"
    return f"{round(h / 24)} días"

hub_today, hub_decide, hub_changes, hub_tareas = [], [], [], []
if hub_doc is None:
    hub_badge = '<span class="hubbadge none">sin datos del hub</span>'
    hub_panels = ('<p class="dim hubnodata">sin datos del hub — ejecuta <code>projects</code> '
                  "(el sweep copia hub-state.json junto a PROJECTS.md) o espera al drain de las 07:30.</p>")
else:
    today_iso = date.today().isoformat()
    horizon = (date.today() + timedelta(days=14)).isoformat()
    for e in _hub_projects():
        slug = e.get("slug") or "?"
        if e.get("estado") == "activo":  # Today: activo + próxima acción + hitos ≤14 días
            nxt = e.get("proxima_accion") or ""
            hitos = [h for h in (e.get("hitos") or []) if isinstance(h, dict)
                     and today_iso <= str(h.get("fecha") or "") <= horizon]
            if nxt or hitos:
                parts = ([esc(nxt)] if nxt else []) + \
                    [f'hito {esc(h.get("fecha") or "")}: {esc(h.get("texto") or "")}' for h in hitos]
                hub_today.append(f'<li><code>{esc(slug)}</code> — {" · ".join(parts)}</li>')
        logs = [l for l in (e.get("logs") or []) if isinstance(l, dict)]  # append-only: last = recent
        if logs:
            last = logs[-1]
            more = f' <span class="dim">(+{len(logs) - 1} más)</span>' if len(logs) > 1 else ""
            hub_changes.append(f'<li><code>{esc(slug)}</code> <span class="dim">'
                               f'{esc(last.get("fecha") or "s/f")}</span> — '
                               f'{esc(last.get("texto") or "")}{more}</li>')
        t = e.get("tareas") or {}
        if t.get("abiertas") or t.get("en_curso") or t.get("hecha"):
            subs = []
            for it in (t.get("items") or []):
                if not isinstance(it, dict) or it.get("estado") == "hecha":
                    continue  # hecha no se materializa: cuenta sí, línea no
                nid = it.get("id")
                origin = (f' <span class="dim">{esc(it.get("origin") or "")}:{esc(nid)}</span>'
                          if nid else "")
                subs.append(f'<li class="sub">{esc(it.get("titulo") or "")} '
                            f'<span class="dim">({esc(it.get("estado") or "")})</span>{origin}</li>')
            hub_tareas.append(
                f'<li><code>{esc(slug)}</code> — {int(t.get("abiertas") or 0)} abierta(s) · '
                f'{int(t.get("en_curso") or 0)} en curso</li>' + "".join(subs))
    inbox = hub_doc.get("inbox") or {}  # Needs decisions: inbox + triaje + propuestas locales
    hub_decide.append(f'<li>Inbox: opencode {int(inbox.get("opencode") or 0)} · '
                      f'pi {int(inbox.get("pi") or 0)} · bruno {int(inbox.get("bruno") or 0)}</li>')
    hub_decide.append(f'<li>Triaje: {int(hub_doc.get("triage") or 0)}</li>')
    for e in _hub_projects():
        loc = e.get("local")
        if isinstance(loc, dict) and loc.get("status_local") == "proposal":
            hub_decide.append(f'<li><code>{esc(e.get("slug") or "?")}</code> — propuesta '
                              '<span class="dim">local («proposal» no es vocabulario del hub)</span></li>')
    counts = {"activo": 0, "pausa": 0, "archivado": 0}  # Resumen computed from v2
    for e in _hub_projects():
        if e.get("estado") in counts:
            counts[e["estado"]] += 1
    hub_resumen = f'Hub: {counts["activo"]} activo · {counts["pausa"]} pausa · {counts["archivado"]} archivado'
    gen_iso = str(hub_doc.get("generated") or "")  # Freshness badge (R3)
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(gen_iso.replace("Z", "+00:00"))).total_seconds() / 3600
    except ValueError:
        age_h = None
    if age_h is None:
        hub_badge = '<span class="hubbadge stale">hub: fecha no válida</span>'
    else:
        cls = "stale" if age_h > 24 else "ok"
        hub_badge = (f'<span class="hubbadge {cls}" id="hub-fresh" data-generated="{esc(gen_iso)}">'
                     f'<span class="txt">datos del hub de hace {esc(_age_label(age_h))}</span></span>')

    def _panel(title, icon, items):
        return (f'<div class="hubpanel"><h3>{icon}{esc(title)}</h3>'
                f'<ul>{"".join(items) or "<li class=\"dim\">— nada</li>"}</ul></div>')

    hub_panels = ('<div class="hubgrid">'
                  + _panel("Hoy", I_TARGET, hub_today)
                  + _panel("Decisiones pendientes", I_ALERT, hub_decide)
                  + _panel("Cambios", I_CLOCK, hub_changes)
                  + _panel("Tareas Notion", I_CHAT, hub_tareas)
                  + "</div>")
    hub_section_html = (f'<section class="panel" id="sec-hub" aria-labelledby="hub-title">'
                        f'<h2 id="hub-title">{I_TARGET}Cockpit del hub {hub_badge} '
                        f'<span class="dim">{esc(hub_resumen)}</span></h2>{hub_panels}</section>')
if hub_doc is None:
    hub_section_html = (f'<section class="panel" id="sec-hub" aria-labelledby="hub-title">'
                        f'<h2 id="hub-title">{I_TARGET}Cockpit del hub {hub_badge}</h2>'
                        f'{hub_panels}</section>')

session_cards = []
for group in sorted(session_projects.values(), key=lambda g: g["display"].casefold()):
    rows = sorted(group["sessions"].values(), key=lambda row: (row[0], row[1]), reverse=True)
    items = [f'<li><a href="{esc(webbase)}/{esc(ses)}" target="_blank" rel="noopener">'
             f'{I_CHAT}{esc(title)}</a></li>' for _when, ses, title in rows]
    more = (f'<details class="session-more"><summary>Ver todas ({len(rows)})</summary>'
            f'<ul class="conv">{"".join(items[4:])}</ul></details>') if len(rows) > 4 else ""
    session_cards.append(f'<article class="card session-project"><h3>{esc(group["display"])}</h3>'
                         f'<ul class="conv">{"".join(items[:4])}</ul>{more}</article>')
session_notice = ("<p role=\"status\">OpenCode no disponible o datos incompletos. "
                  "Regenera el panel para reintentar.</p>") if oc_unavailable else ""
if not session_cards and not oc_unavailable:
    session_notice = "<p>No hay proyectos con sesiones de OpenCode.</p>"
ma_items = []
for _name, _srcs in sorted(agent_sessions.items(),
                           key=lambda kv: max(v["last_dt"] for v in kv[1].values()), reverse=True)[:12]:
    _parts = " · ".join(f'{k}: {v["count"]} recientes, última {v["last_dt"].strftime("%d/%m %H:%M")}'
                        for k, v in _srcs.items())
    ma_items.append(f'<li><code>{esc(_name)}</code> — {_parts}</li>')
ma_html = (f'<div class="convtitle">Otros agentes (pi · agy)</div>'
           f'<ul class="conv">{"".join(ma_items)}</ul>') if ma_items else ""
agent_html = session_notice + f'<div class="grid">{"".join(session_cards)}</div>' + ma_html

week_chip = (f'<span class="chip">{I_CLOCK}<strong>{("%.1f" % week_total).replace(".", ",")}h</strong>'
             f'&nbsp;esta semana</span>') if week_total > 0 else ""
gen = datetime.now().strftime("%d/%m/%Y %H:%M")

html_doc = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Global Project Tracker</title>
<style>
  :root {{ --bg:#070b14; --card:#0d1524; --line:#223047; --tx:#e8eef8; --dim:#9db0cc;
           --acc:#5ec2f7; --ok:#34d399; --prop:#fbbf24; --pau:#fb923c; --cold:#64748b; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
          margin: 0 auto; max-width: 76rem; padding: 1rem; background: var(--bg); color: var(--tx); }}
  a {{ color: var(--acc); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  :focus-visible {{ outline: 2px solid var(--acc); outline-offset: 2px; border-radius: 4px; }}
  h1 {{ font-size: 1.35rem; margin: .2rem 0; color: var(--acc); }}
  .meta {{ color: var(--dim); font-size: .8rem; margin: .1rem 0 .8rem; }}
  .strip {{ display: flex; flex-wrap: wrap; gap: .5rem; margin: .6rem 0 1rem; }}
  .chip {{ background: var(--card); border: 1px solid var(--line); border-radius: 999px;
           padding: .32rem .75rem; font-size: .84rem; color: var(--tx); cursor: pointer; }}
  .chip strong {{ color: var(--acc); margin-left: .25rem; }}
  .panel {{ background: var(--card); border: 1px solid var(--line); border-radius: .6rem;
            padding: .8rem 1rem; margin: 0 0 1rem; }}
  .panel h2 {{ font-size: 1rem; margin: 0 0 .5rem; color: var(--acc); display: flex; gap: .4rem; align-items: center; }}
  .panel ul {{ margin: 0; padding-left: 1.1rem; }}
  .panel li {{ font-size: .87rem; margin: .32rem 0; overflow-wrap: anywhere; }}
  .ev {{ color: var(--dim); font-size: .76rem; }}
  details > summary {{ list-style: none; cursor: pointer; display: flex; align-items: center; gap: .5rem; }}
  details > summary::-webkit-details-marker {{ display: none; }}
  .tog {{ width: .6rem; height: .6rem; border-right: 2px solid var(--dim); border-bottom: 2px solid var(--dim);
          transform: rotate(45deg); transition: transform .15s; }}
  details[open] .tog {{ transform: rotate(-135deg); }}
  h2 {{ font-size: 1.05rem; margin: 1.2rem 0 .7rem; color: var(--tx); }}
  .count {{ background: var(--card); border: 1px solid var(--line); border-radius: 999px;
            padding: .05rem .55rem; font-size: .8rem; color: var(--acc); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(21rem, 1fr)); gap: .7rem; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: .6rem; padding: .75rem .9rem; }}
  .card header {{ display: flex; align-items: center; gap: .5rem; }}
  .card h3 {{ font-size: .98rem; margin: 0; flex: 1; min-width: 0; overflow-wrap: anywhere; color: var(--tx); }}
  .dot {{ width: .55rem; height: .55rem; border-radius: 50%; flex: none; }}
  .dot.active {{ background: var(--ok); box-shadow: 0 0 6px var(--ok); }}
  .dot.proposal {{ background: var(--prop); }} .dot.paused {{ background: var(--pau); }}
  .dot.closed, .dot.cold, .dot.unclassified {{ background: var(--cold); }}
  .status {{ font-size: .7rem; color: var(--dim); border: 1px solid var(--line);
             border-radius: 999px; padding: .05rem .5rem; flex: none; }}
  .desc {{ font-size: .84rem; margin: .45rem 0; overflow-wrap: anywhere; }}
  .badges {{ display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }}
  .badge {{ font-size: .72rem; border: 1px solid var(--line); border-radius: 999px;
            padding: .1rem .5rem; color: var(--dim); display: inline-flex; gap: .25rem; align-items: center; }}
  .badge.warn {{ color: #fda4af; border-color: #7f1d1d66; }}
  .badge.info {{ color: #93c5fd; }}
  code {{ color: #fcd34d; font-size: .78rem; }}
  .hours {{ font-size: .87rem; margin: .5rem 0 .2rem; display: flex; gap: .35rem; align-items: center; }}
  .hours strong {{ color: var(--acc); }}
  .next {{ font-size: .83rem; background: #101c33; border-left: 3px solid var(--acc);
           border-radius: .3rem; padding: .38rem .55rem; margin: .5rem 0 0; overflow-wrap: anywhere; }}
  .nlabel {{ color: var(--acc); font-weight: 700; font-size: .7rem; text-transform: uppercase;
             letter-spacing: .05em; margin-right: .3rem; }}
  .streams {{ display: flex; flex-wrap: wrap; gap: .3rem; }}
  .stream {{ font-size: .72rem; border: 1px solid var(--line); border-radius: 999px;
             padding: .12rem .55rem; color: var(--tx); background: #101c33; }}
  .convtitle {{ font-size: .68rem; color: var(--dim); text-transform: uppercase; letter-spacing: .06em;
                 margin: .6rem 0 .15rem; }}
  .conv {{ list-style: none; margin: 0; padding: 0; }}
  .conv li {{ font-size: .8rem; margin: .28rem 0; display: flex; gap: .35rem; align-items: baseline;
               flex-wrap: wrap; overflow-wrap: anywhere; }}
  .dim {{ color: var(--dim); font-size: .72rem; }}
  .ic {{ width: .95em; height: .95em; vertical-align: -0.15em; flex: none; }}
  .hours .ic, .conv .ic, .panel h2 .ic, .hubpanel h3 .ic {{ color: var(--acc); }}
  .badge.warn .ic {{ color: #fda4af; }}
  .hubgrid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr)); gap: .7rem; }}
  .hubpanel {{ background: #101c33; border: 1px solid var(--line); border-radius: .45rem;
               padding: .55rem .7rem; min-width: 0; }}
  .hubpanel h3 {{ font-size: .85rem; margin: 0 0 .4rem; color: var(--acc);
                  display: flex; gap: .35rem; align-items: center; }}
  .hubpanel ul {{ margin: 0; padding-left: 1rem; }}
  .hubpanel li {{ font-size: .8rem; margin: .28rem 0; overflow-wrap: anywhere; }}
  .hubpanel li.sub {{ list-style: none; margin: .12rem 0 .12rem .7rem; }}
  .hubpanel li.dim, li.dim {{ color: var(--dim); }}
  .hubbadge {{ font-size: .72rem; border: 1px solid var(--line); border-radius: 999px;
               padding: .12rem .55rem; vertical-align: middle; margin-left: .4rem; }}
  .hubbadge.ok {{ color: var(--ok); border-color: #14532d66; }}
  .hubbadge.stale {{ color: #fda4af; border-color: #7f1d1d66; }}
  .hubbadge.none {{ color: var(--dim); }}
  .hubnodata {{ margin: .2rem 0; }}
  @media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} body {{ padding: .6rem; }} }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style></head><body>
<h1>Global Project Tracker</h1>
<p class="meta">Actualizado {gen} · fuentes: git sweep + projects.yaml + time-ledger + sesiones OpenCode + hub-state v2 · refresco diario 08:00 · <code>projects</code> regenera</p>
<div class="strip">{''.join(chips)}{week_chip}</div>
<div class="panel"><h2>{I_TARGET}Accionables pendientes</h2><ul>{act_html}</ul></div>
{hub_section_html}
<section class="panel" id="sec-opencode" aria-labelledby="opencode-title"><h2 id="opencode-title">{I_CHAT}Proyectos con sesiones multi-agente</h2>{agent_html}</section>
{''.join(body_sections)}
<script>
(function () {{
  var el = document.getElementById('hub-fresh');
  if (!el) return;
  var t = Date.parse(el.getAttribute('data-generated') || '');
  if (isNaN(t)) return;
  var h = (Date.now() - t) / 36e5;
  var label = h < 1 ? 'hace ' + Math.max(0, Math.round(h * 60)) + ' min'
            : h < 48 ? 'hace ' + Math.round(h) + ' h'
            : 'hace ' + Math.round(h / 24) + ' días';
  var txt = el.querySelector('.txt');
  if (txt) txt.textContent = 'datos del hub de ' + label;
  el.classList.add(h > 24 ? 'stale' : 'ok');
}})();
</script>
</body></html>"""

with open(out_path, "w", encoding="utf-8") as f:
    f.write(html_doc)
print(f"HTML written: {out_path}")

# ---------- sensor contract: projects.json (facts for the control hub) ----------
# The dashboard is a SENSOR: facts only. status_local is the local fallback until
# the hub note exists; the hub's estado/prioridad/ambito are authoritative once live.
def unit_json(u):
    meta = projects.get(u["names"][0], {}) if len(u["names"]) == 1 else {}
    if len(u["names"]) > 1:
        gkey = next((k for k, v in projects.items()
                     if v.get("name") == u["display"] or k == u["display"]), None)
        meta = projects.get(gkey, {}) if gkey else {}
    led = {"week": 0.0, "entries": []}
    for n in u["names"]:
        e = ledger.get(n)
        if e:
            led["week"] += e["week"]
    sessions = []
    for n in u["names"]:
        for when, ses, title in oc_sessions.get(n, []):
            sessions.append({"id": ses, "title": title,
                             "updated": when.isoformat() if when else None})
    sessions.sort(key=lambda x: x["updated"] or "", reverse=True)
    return {
        "name": u["display"],
        "repos": u["names"],
        "scope": meta.get("scope"),
        "status_local": meta.get("status"),
        "branch": sorted({u["git"]["branch"]}),
        "last_commit": u["git"]["date"] or None,
        "dirty": int(u["git"]["dirty"]) if u["git"]["dirty"] else 0,
        "ahead": int(u["git"]["ahead"]) if u["git"]["ahead"] else 0,
        "hours_week": round(led["week"], 2),
        "sessions": sessions[:20],
        "uncatalogued": u["display"] not in projects and not meta,
    }

sensor_projects = [unit_json(u) for u in units]

# hub-state v2 mirror (cockpit-view R2): the sibling index loaded above, keyed
# by slug AND repos[] AND unit display name (D1 normalization: hub slugs are
# number-prefixed — 006-ai-stack — local keys are not; 1:N aliases carry both
# repos). Absent/unparsable sibling ⇒ graceful empty hub columns (preserved
# fallback: estado_hub/prioridad_hub simply stay unset).
for _p in sensor_projects:
    _h = hub_index.get(_p["name"])
    if _h:
        _p["estado_hub"] = _h.get("estado")
        _p["prioridad_hub"] = _h.get("prioridad")

# dirty_history[7]: hecho rodante por repo (fecha, dirty) para el triaje
# sostenido del hub. Estado local del sensor, cap 7 por fecha; los huecos
# (PC apagado) viajan como ausencia y el drain falla cerrado.
_HIST_PATH = os.path.join(os.path.dirname(md_path), "dirty-history.json")
try:
    with open(_HIST_PATH, encoding="utf-8") as f:
        _hist = json.load(f)
except (OSError, ValueError):
    _hist = {}
_today = datetime.now().strftime("%Y-%m-%d")
for _u in units:
    _key = _u["display"]
    _row = {"date": _today, "dirty": int(_u["git"]["dirty"]) if _u["git"]["dirty"] else 0}
    _rows = [r for r in (_hist.get(_key) or []) if r.get("date") != _today]
    _rows.append(_row)
    _rows.sort(key=lambda r: r["date"])
    _hist[_key] = _rows[-7:]
with open(_HIST_PATH, "w", encoding="utf-8") as f:
    json.dump(_hist, f, ensure_ascii=False, indent=1)
for _p in sensor_projects:
    _p["dirty_history"] = _hist.get(_p["name"], [])

# autodescubrimiento: proyectos presentes solo en sesiones de OpenCode (sin catálogo)
known = {n for u in units for n in u["names"]}
known_display = {u["display"] for u in units}
for name, sess in oc_sessions.items():
    if not sess or name in known or name in known_display:
        continue
    sessions = sorted(
        ({"id": ses, "title": title, "updated": when.isoformat() if when else None}
         for when, ses, title in sess),
        key=lambda x: x["updated"] or "", reverse=True)
    sensor_projects.append({
        "name": name, "repos": [], "scope": None, "status_local": None,
        "branch": [], "last_commit": None, "dirty": 0, "ahead": 0,
        "hours_week": 0.0, "sessions": sessions[:20], "uncatalogued": True,
        "dirty_history": _hist.get(name, []),
    })

sensor = {
    "schema": "projects-sensor/v1",
    "generated": datetime.now().isoformat(timespec="seconds"),
    "projects": sensor_projects,
    "multi_agent": {
        name: {k: {"count": v["count"], "last": v["last_dt"].isoformat(timespec="seconds")}
               for k, v in srcs.items()}
        for name, srcs in sorted(agent_sessions.items())
    },
}
for _p in sensor["projects"]:
    _a = agent_sessions.get(_p["name"])
    if _a:
        _p["agents"] = {k: {"count": v["count"], "last": v["last_dt"].isoformat(timespec="seconds")}
                        for k, v in _a.items()}
json_path = os.path.join(os.path.dirname(md_path), "projects.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(sensor, f, ensure_ascii=False, indent=1)
print(f"JSON written: {json_path}")
