// tiktok-ingest Phase 1 web client (T2: S1-S6 UX redesign).
//
// Vanilla JS, hash routing, no frameworks. Rules pinned by
// tests/test_web_static.py:
// - Rendering uses createElement/textContent only; server data never
//   becomes markup. Icons are DOM nodes built with createElementNS.
// - ONE request wrapper performs HTTP; only postJSON mutates state and
//   it always attaches the per-start token header. The token comes from
//   the meta tag the server injects and is never displayed or logged.
// - Every API path is a quoted literal in PATHS so the tests can prove
//   each one resolves on the real server.
// - The only external-looking string is the SVG namespace identifier
//   (a name, never fetched); no outbound reference exists anywhere.
//
// Sections: helpers, api, icons, components, toasts, shell, views,
// modal, run stream, shortcuts, router, boot.

(function () {
  "use strict";

  // ---- endpoints (single source of truth) -----------------------------

  var PATHS = {
    health: "/api/health",
    collections: "/api/collections",
    collection: "/api/collections/{key}",
    runs: "/api/runs",
    runReport: "/api/runs/{key}/{report}",
    activeRun: "/api/runs/active",
    consent: "/api/runs/active/consent",
    ready: "/api/runs/active/ready",
    cancel: "/api/runs/active/cancel",
    events: "/api/runs/active/events",
    blocks: "/api/blocks",
    video: "/api/videos/{video_id}",
    videoEvidence: "/api/videos/{video_id}/evidence/{name}",
    backlog: "/api/backlog",
    backlogEntry: "/api/backlog/{video_id}",
    backlogDecision: "/api/backlog/{video_id}/decision",
    backlogPlan: "/api/backlog/plan",
    backlogApply: "/api/backlog/apply"
  };

  function endpoint(name, params) {
    var path = PATHS[name];
    if (!path) throw new Error("unknown endpoint " + name);
    if (params) {
      Object.keys(params).forEach(function (key) {
        path = path.replace("{" + key + "}", encodeURIComponent(params[key]));
      });
    }
    return path;
  }

  // ---- token + HTTP helpers --------------------------------------------

  var TOKEN = (function () {
    var meta = document.querySelector('meta[name="ti-token"]');
    return meta && meta.content ? meta.content : "";
  })();

  function request(path, options) {
    return fetch(path, options).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = null; }
        }
        if (!response.ok) {
          var message = data && typeof data.error === "string"
            ? data.error
            : "request failed (HTTP " + response.status + ")";
          throw new Error(message);
        }
        return { data: data, text: text };
      });
    });
  }

  function withQuery(path, query) {
    if (!query) return path;
    var parts = Object.keys(query).map(function (key) {
      return encodeURIComponent(key) + "=" + encodeURIComponent(query[key]);
    });
    return path + (parts.length ? "?" + parts.join("&") : "");
  }

  function getJSON(name, params, query) {
    return request(withQuery(endpoint(name, params), query), {
      headers: { "Accept": "application/json" }
    }).then(function (result) { return result.data; });
  }

  function getText(name, params) {
    return request(endpoint(name, params), {})
      .then(function (result) { return result.text; });
  }

  // The ONLY mutating helper: every write carries the per-start token.
  function postJSON(name, body, params) {
    if (!TOKEN) throw new Error("security token missing; reload the page");
    return request(endpoint(name, params), {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-TI-Token": TOKEN
      },
      body: JSON.stringify(body === undefined ? {} : body)
    }).then(function (result) { return result.data; });
  }

  // ---- tiny DOM helpers (text-only rendering) ---------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) {
      // Never render "[object Object]": structured values show as JSON.
      node.textContent = typeof text === "object" ? JSON.stringify(text) : String(text);
    }
    return node;
  }

  function link(href, text) {
    var anchor = el("a", null, text);
    anchor.href = href;
    return anchor;
  }

  function para(parent, text, className) {
    var node = el("p", className, text);
    parent.appendChild(node);
    return node;
  }

  function addCell(row, text) {
    row.appendChild(el("td", null, text));
  }

  function jsonText(value) {
    return JSON.stringify(value, null, 2);
  }

  // Backlog classification is an object {root, subgroup, confidence,
  // unknown_reason}; values render verbatim, joined for reading.
  function classificationText(value) {
    if (value === null || typeof value !== "object") {
      return String(value);
    }
    var parts = [value.root, value.subgroup].filter(function (part) {
      return part !== null && part !== undefined && part !== "";
    });
    var text = parts.length ? parts.join(" / ") : "unclassified";
    if (typeof value.confidence === "number") {
      text += " (confidence " + value.confidence + ")";
    }
    if (value.unknown_reason) {
      text += " - " + value.unknown_reason;
    }
    return text;
  }

  function addFact(list, term, value) {
    list.appendChild(el("dt", null, term));
    list.appendChild(el("dd", null, value));
    return list.lastChild;
  }

  function addFactNode(list, term, node) {
    list.appendChild(el("dt", null, term));
    var dd = el("dd");
    dd.appendChild(node);
    list.appendChild(dd);
    return dd;
  }

  function newTable(headings) {
    var table = el("table", "list");
    var head = el("thead");
    var headRow = el("tr");
    headings.forEach(function (heading) {
      headRow.appendChild(el("th", null, heading));
    });
    head.appendChild(headRow);
    table.appendChild(head);
    var body = el("tbody");
    table.appendChild(body);
    return { table: table, body: body };
  }

  function asList(value) {
    return Array.isArray(value) ? value : [];
  }

  // ---- SVG icons (S3): DOM createElementNS nodes, never markup strings --

  var SVG_NS = "http://www.w3.org/2000/svg";

  var ICONS = {
    brand: [
      ["rect", { x: 3.5, y: 4.5, width: 17, height: 15, rx: 3 }],
      ["path", { d: "M10 9l6 3-6 3z" }]
    ],
    home: [
      ["path", { d: "M3 10.8L12 3l9 7.8" }],
      ["path", { d: "M5.5 9.5V20h13.5V9.5" }],
      ["path", { d: "M10 20v-5.5h4V20" }]
    ],
    collections: [
      ["path", { d: "M12 3L3 8l9 5 9-5-9-5z" }],
      ["path", { d: "M3 12.7l9 5 9-5" }],
      ["path", { d: "M3 17.4l9 5 9-5" }]
    ],
    runs: [
      ["circle", { cx: 12, cy: 12, r: 8.6 }],
      ["path", { d: "M12 7.2V12l3.4 2" }]
    ],
    blocks: [
      ["path", { d: "M7.9 3h8.2L21 7.9v8.2L16.1 21H7.9L3 16.1V7.9L7.9 3z" }],
      ["path", { d: "M12 7.8v4.6" }],
      ["path", { d: "M12 16.1v.01" }]
    ],
    evidence: [
      ["path", { d: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z" }],
      ["path", { d: "M14 3v5h5" }],
      ["path", { d: "M9 13h6" }],
      ["path", { d: "M9 17h6" }]
    ],
    review: [
      ["circle", { cx: 12, cy: 12, r: 8.6 }],
      ["path", { d: "M8.4 12.4l2.4 2.4 4.8-5.2" }]
    ],
    run: [
      ["path", { d: "M4 17l6-5-6-5" }],
      ["path", { d: "M12 19h8" }]
    ]
  };

  function svgIcon(name, size) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size || 18);
    svg.setAttribute("height", size || 18);
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    asList(ICONS[name]).forEach(function (part) {
      var node = document.createElementNS(SVG_NS, part[0]);
      var attrs = part[1] || {};
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
      svg.appendChild(node);
    });
    return svg;
  }

  // ---- vocabulary tones (P1-FR-03: values render verbatim, only tinted) -

  var OUTCOME_TONES = {
    // stage outcomes / operator statuses
    complete: "ok",
    confirmed: "ok",
    accepted: "ok",
    processed_complete: "ok",
    pending: "info",
    in_progress: "info",
    new_processable: "info",
    dry_run: "info",
    blocked: "warn",
    unverifiable: "warn",
    partial: "warn",
    partial_resumable: "warn",
    stale: "warn",
    failed: "bad",
    rejected: "bad",
    contradicted: "bad",
    refuted: "bad",
    budget_exceeded: "bad",
    unsupported: "muted",
    not_applicable: "muted",
    none: "muted"
  };

  function toneFor(value) {
    return OUTCOME_TONES[String(value)] || "neutral";
  }

  // ---- components --------------------------------------------------------

  function badge(text, tone) {
    return el("span", "badge tone-" + (tone || "neutral"),
      text === null || text === undefined ? "" : String(text));
  }

  function chip(text, tone) {
    return el("span", "chip tone-" + (tone || "neutral"),
      text === null || text === undefined ? "" : String(text));
  }

  function callout(kind, title) {
    var box = el("div", "callout callout-" + kind);
    if (title) box.appendChild(el("p", "callout-title", title));
    return box;
  }

  function copyButton(getText, label) {
    var button = el("button", "btn btn-ghost btn-sm copy-btn", label || "Copy");
    button.type = "button";
    button.title = "Copy to clipboard";
    button.addEventListener("click", function () {
      var value = typeof getText === "function" ? getText() : getText;
      var done = function (ok) {
        var previous = button.textContent;
        button.textContent = ok ? "Copied" : "copy unavailable";
        button.disabled = true;
        window.setTimeout(function () {
          button.textContent = previous;
          button.disabled = false;
        }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(
          function () { done(true); },
          function () { done(false); }
        );
      } else {
        done(false);
      }
    });
    return button;
  }

  // ---- document reader (Markdown / JSON artifacts) ------------------------

  // The chosen mode survives artifact switches for the whole session.
  var readerMode = "formatted";

  // A reader for one artifact: Formatted/Raw toggle, expand/collapse for
  // foldable documents and a copy of the raw text. reader.js does the
  // formatting with text-only DOM; invalid JSON (or a missing reader)
  // falls back to the raw text with a visible note.
  function documentViewer(name, text) {
    var reader = window.TIReader;
    var kind = reader ? reader.kindOf(name) : "text";
    var wrap = el("div", "doc-viewer");
    var toolbar = el("div", "doc-toolbar");
    var body = el("div", "doc-body");
    wrap.appendChild(toolbar);

    var formatted = null;
    var note = null;
    if (kind === "json") {
      try {
        formatted = reader.renderJSON(JSON.parse(text));
      } catch (err) {
        note = "Not valid JSON (" + err.message + "); showing the raw text.";
      }
    } else if (kind === "markdown") {
      formatted = reader.renderMarkdown(text);
    }
    var raw = el("pre", "verbatim doc-raw", text);
    raw.setAttribute("role", "region");
    raw.setAttribute("aria-label", name + " raw text");
    if (formatted) {
      formatted.setAttribute("role", "region");
      formatted.setAttribute("aria-label", name + " formatted");
    }
    var foldable = Boolean(formatted && formatted.querySelector("details"));

    var modeButtons = {};
    var foldButtons = [];
    function show(mode) {
      var active = mode === "formatted" && formatted ? "formatted" : "raw";
      body.textContent = "";
      body.appendChild(active === "formatted" ? formatted : raw);
      Object.keys(modeButtons).forEach(function (key) {
        modeButtons[key].setAttribute("aria-pressed", String(key === active));
      });
      foldButtons.forEach(function (button) {
        button.hidden = active !== "formatted" || !foldable;
      });
    }

    if (formatted) {
      var group = el("div", "segmented");
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", "View mode");
      [["formatted", kind === "json" ? "Tree" : "Formatted"], ["raw", "Raw"]]
        .forEach(function (pair) {
          var button = el("button", "btn btn-sm", pair[1]);
          button.type = "button";
          button.addEventListener("click", function () {
            readerMode = pair[0];
            show(pair[0]);
          });
          modeButtons[pair[0]] = button;
          group.appendChild(button);
        });
      toolbar.appendChild(group);
      [["Expand all", true], ["Collapse all", false]].forEach(function (pair) {
        var button = el("button", "btn btn-ghost btn-sm", pair[0]);
        button.type = "button";
        button.addEventListener("click", function () {
          reader.setExpanded(formatted, pair[1]);
        });
        foldButtons.push(button);
        toolbar.appendChild(button);
      });
    }
    toolbar.appendChild(el("span", "doc-spacer"));
    var lines = text ? text.split("\n").length : 0;
    toolbar.appendChild(el("span", "muted small",
      lines + (lines === 1 ? " line" : " lines")));
    toolbar.appendChild(copyButton(function () { return text; }, "Copy raw"));

    if (note) {
      var warn = callout("warn");
      para(warn, note);
      wrap.appendChild(warn);
    }
    wrap.appendChild(body);
    show(readerMode);
    return wrap;
  }

  // Relative dates (S5): "3 weeks ago", exact timestamp on hover.
  function pluralAgo(count, unit) {
    return count + " " + unit + (count === 1 ? "" : "s") + " ago";
  }

  function relativeTime(iso) {
    if (iso === null || iso === undefined || iso === "") {
      return { text: "n/a", title: "" };
    }
    var text = String(iso);
    var parsed = Date.parse(text);
    if (isNaN(parsed)) return { text: text, title: text };
    var seconds = Math.round((Date.now() - parsed) / 1000);
    if (seconds < 0) seconds = 0;
    var out;
    if (seconds < 45) out = "just now";
    else if (seconds < 90) out = "a minute ago";
    else if (seconds < 2700) out = pluralAgo(Math.round(seconds / 60), "minute");
    else if (seconds < 5400) out = "an hour ago";
    else if (seconds < 79200) out = pluralAgo(Math.round(seconds / 3600), "hour");
    else if (seconds < 129600) out = "a day ago";
    else if (seconds < 561600) out = pluralAgo(Math.round(seconds / 86400), "day");
    else if (seconds < 2592000) out = pluralAgo(Math.round(seconds / 604800), "week");
    else if (seconds < 31536000) {
      out = pluralAgo(Math.max(1, Math.round(seconds / 2592000)), "month");
    } else {
      out = pluralAgo(Math.max(1, Math.round(seconds / 31536000)), "year");
    }
    return { text: out, title: text };
  }

  function timeEl(iso, className) {
    var info = relativeTime(iso);
    var node = el("span", className || "time", info.text);
    if (info.title) node.title = info.title;
    return node;
  }

  // ids never wrap mid-token: truncate + copy (S5).
  function monoId(value) {
    var wrap = el("span", "mono-id");
    if (!value) return wrap;
    var text = el("span", "mono truncate", String(value));
    text.title = String(value);
    wrap.appendChild(text);
    wrap.appendChild(copyButton(function () { return String(value); }));
    return wrap;
  }

  function stagePills(stages) {
    var row = el("div", "stage-row");
    Object.keys(stages || {}).forEach(function (stage) {
      var outcome = stages[stage];
      var pill = el("span", "stage-pill tone-" + toneFor(outcome));
      if (STAGE_HELP[stage]) pill.title = STAGE_HELP[stage];
      pill.appendChild(el("span", "stage-name", stage));
      pill.appendChild(el("span", "stage-outcome",
        outcome === null || outcome === undefined ? "?" : String(outcome)));
      row.appendChild(pill);
    });
    return row;
  }

  function commandBox(text, label) {
    var box = el("div", "command-box");
    if (label) box.appendChild(el("span", "command-label", label));
    var line = el("code", "command mono",
      text === null || text === undefined ? "" : String(text));
    line.title = text === null || text === undefined ? "" : String(text);
    box.appendChild(line);
    if (text !== null && text !== undefined && text !== "") {
      box.appendChild(copyButton(function () { return String(text); }));
    }
    return box;
  }

  function skeleton(lines) {
    var box = el("div", "skeleton");
    var count = lines || 2;
    for (var index = 0; index < count; index++) {
      box.appendChild(el("div", "skeleton-line"));
    }
    return box;
  }

  function emptyNote(parent, text) {
    para(parent, text, "empty");
  }

  // ---- toasts (page-level live region in index.html) ----------------------

  var toastsEl = document.getElementById("toasts");

  function toast(message, kind) {
    var item = el("div", "toast toast-" + (kind || "info"), message);
    toastsEl.appendChild(item);
    window.setTimeout(function () { item.remove(); }, 6500);
  }

  // ---- view plumbing ---------------------------------------------------------

  var view = document.getElementById("view");

  // One plain-language line under each view title: what the section
  // answers. Keys are the exact resetView titles (pinned by tests).
  var VIEW_INTROS = {
    "Home": "What is going on right now: pending decisions, blocked items, "
      + "the latest run and your collections at a glance.",
    "Collections": "Your saved TikTok collections and what the inventory "
      + "found in each. Observed means the video was seen in the list, not "
      + "that it was processed.",
    "Collection": "One collection's inventory: what TikTok declares, what "
      + "was actually observed, and the evidence that the end of the list "
      + "was reached.",
    "Runs": "Every saved pipeline run, newest first. Each video moves "
      + "through six stages; open a run to see where each one stopped.",
    "Run report": "One saved run: per-video stage outcomes, failures, "
      + "cleanup notes and the full report as saved on disk.",
    "Blocks": "What is stuck and how to resume it: blocked stages per video "
      + "and run-level stops, with recorded reasons and a resume command "
      + "when the saved identity is available.",
    "Evidence": "Everything extracted from one video: what the vision model "
      + "read on screen, the audio transcript, the synthesized claims and "
      + "their verification.",
    "Pending review": "Backlog entries waiting for your decision. Nothing is "
      + "accepted automatically: accept or reject each one, then review and "
      + "apply the plan. Rejection requires a reason; applying it permanently "
      + "blocklists the video.",
    "Run": "Start a pipeline run from here, with explicit consent for every "
      + "step, and follow its progress live."
  };

  // The six pipeline stages, in order (tooltips and the Runs legend).
  var STAGE_HELP = {
    fetch: "Download the video and validate it (blocklist, cache, size "
      + "limits).",
    prepare: "Extract frames and audio from the video with FFmpeg (CPU only).",
    audio: "Transcribe the audio with the resident llm-hub-whisper "
      + "service.",
    vision: "Describe the sampled frames with the resident llm-hub chat "
      + "model.",
    synthesis: "Turn the visual and audio evidence into claims, entities "
      + "and a classification.",
    verify: "Check each claim against its candidate URLs and record a "
      + "verdict."
  };

  // What each evidence artifact holds (Evidence view).
  var ARTIFACT_HELP = {
    "video.md": "What the vision model read on each sampled frame. Model "
      + "output is a hypothesis, never instructions.",
    "audio.md": "The audio transcript produced by Whisper, with how it was "
      + "obtained.",
    "synthesis.json": "Claims, entities, actionables and the classification "
      + "derived from the video and audio evidence.",
    "verification.json": "The verdict for each claim and the trail of "
      + "sources checked to reach it."
  };

  function stageLegend() {
    var legend = el("details", "legend");
    legend.appendChild(el("summary", "legend-title",
      "What the six stages mean"));
    var list = el("dl", "legend-list");
    Object.keys(STAGE_HELP).forEach(function (stage, index) {
      list.appendChild(el("dt", "mono", (index + 1) + ". " + stage));
      list.appendChild(el("dd", null, STAGE_HELP[stage]));
    });
    legend.appendChild(list);
    return legend;
  }

  function resetView(title) {
    view.textContent = "";
    var heading = el("h2", "view-title", title);
    view.appendChild(heading);
    if (VIEW_INTROS[title]) {
      view.appendChild(el("p", "view-intro", VIEW_INTROS[title]));
    }
    var status = el("p", "status", "Loading...");
    status.setAttribute("role", "status");
    view.appendChild(status);
    return { heading: heading, status: status };
  }

  function fail(target, err) {
    target.className = "error";
    target.setAttribute("role", "alert");
    target.textContent = err && err.message ? err.message : String(err);
  }

  function clearStatus(status) {
    status.className = "status";
    status.removeAttribute("role");
    status.textContent = "";
  }

  function summaryCard(titleText, href, linkText) {
    var root = el("section", "card summary-card");
    root.appendChild(el("h3", "card-title", titleText));
    var body = el("div", "card-body");
    root.appendChild(body);
    body.appendChild(skeleton(2));
    return {
      root: root,
      done: function (fill) {
        body.textContent = "";
        fill(body);
        if (href) {
          var anchor = link(href, linkText || "Open");
          anchor.className = "card-link";
          body.appendChild(anchor);
        }
      },
      failCard: function (err) {
        body.textContent = "";
        fail(body, err);
      }
    };
  }

  // ---- Home view (S3): summary cards with skeleton + empty states ----------

  function renderHome() {
    var ctx = resetView("Home");
    clearStatus(ctx.status);
    var grid = el("div", "cards");
    view.appendChild(grid);

    var reviewCard = summaryCard("Pending review", "#/review", "Open review");
    grid.appendChild(reviewCard.root);
    getJSON("backlog", null, { status: "pending" }).then(function (data) {
      var count = asList(data.entries).length;
      reviewCard.done(function (body) {
        if (!count) {
          emptyNote(body, "No pending entries.");
          return;
        }
        body.appendChild(el("p", "big-number", String(count)));
        para(body, count === 1
          ? "entry awaits a decision"
          : "entries await decisions", "muted small");
      });
    }).catch(function (err) { reviewCard.failCard(err); return null; });

    var blocksCard = summaryCard("Blocks", "#/blocks", "Open blocks");
    grid.appendChild(blocksCard.root);
    getJSON("blocks").then(function (data) {
      var itemCount = asList(data.items).length;
      var runCount = asList(data.runs).length;
      blocksCard.done(function (body) {
        if (!itemCount && !runCount) {
          emptyNote(body, "Nothing blocked.");
          return;
        }
        body.appendChild(el("p", "big-number", String(itemCount)));
        para(body, "blocked items"
          + (runCount ? " · " + runCount + " run-level" : ""), "muted small");
      });
    }).catch(function (err) { blocksCard.failCard(err); return null; });

    var runsCard = summaryCard("Last saved run", "#/runs", "Open runs");
    grid.appendChild(runsCard.root);
    getJSON("runs").then(function (data) {
      // Server order is newest first.
      var last = asList(data.runs)[0];
      runsCard.done(function (body) {
        if (!last) {
          emptyNote(body, "No saved runs yet.");
          return;
        }
        body.appendChild(timeEl(last.saved_at, "strong"));
        para(body, last.collection_name || last.collection_key || "collection",
          "muted small");
        body.appendChild(countChips(last.counts));
      });
    }).catch(function (err) { runsCard.failCard(err); return null; });

    var collectionsCard = summaryCard("Collections", "#/collections",
      "Open collections");
    grid.appendChild(collectionsCard.root);
    getJSON("collections").then(function (data) {
      var entries = asList(data.collections);
      collectionsCard.done(function (body) {
        if (!entries.length) {
          emptyNote(body, "No collections recorded yet.");
          return;
        }
        var complete = 0;
        entries.forEach(function (entry) {
          if (entry.status === "complete") complete++;
        });
        body.appendChild(el("p", "big-number", String(entries.length)));
        para(body, complete + " complete · "
          + (entries.length - complete) + " in progress", "muted small");
      });
    }).catch(function (err) { collectionsCard.failCard(err); return null; });
  }

  function countChips(counts) {
    var row = el("div", "chip-row");
    var values = counts || {};
    if (values.complete != null) {
      row.appendChild(chip(values.complete + " complete", "ok"));
    }
    if (values.pending != null) {
      row.appendChild(chip(values.pending + " pending", "info"));
    }
    if (values.other != null) {
      row.appendChild(chip(values.other + " other", "muted"));
    }
    return row;
  }

  // ---- Collections view (P1-FR-04, S5) ---------------------------------------

  function renderCollections() {
    var ctx = resetView("Collections");
    getJSON("collections").then(function (data) {
      var entries = asList(data.collections);
      clearStatus(ctx.status);
      if (!entries.length) {
        emptyNote(view, "No collections recorded yet.");
        return;
      }
      var list = el("div", "stack");
      view.appendChild(list);
      entries.forEach(function (entry) {
        list.appendChild(collectionCard(entry));
      });
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  function collectionCard(entry) {
    var card = el("article", "card");

    var head = el("div", "entry-head");
    var title = el("h3", "card-title");
    title.appendChild(link(
      "#/collections/" + encodeURIComponent(entry.key),
      entry.name || entry.key
    ));
    if (entry.author) {
      title.appendChild(el("span", "muted small", " · @" + entry.author));
    }
    head.appendChild(title);
    head.appendChild(monoId(entry.key));
    card.appendChild(head);

    var meta = el("div", "entry-meta");
    meta.appendChild(badge(entry.status, toneFor(entry.status)));
    // Observed is an inventory fact; it is never labelled "processed".
    meta.appendChild(el("span", "muted small",
      "observed " + (entry.observed_count == null ? 0 : entry.observed_count)
      + " of "
      + (entry.declared_count == null ? "unknown" : entry.declared_count)
      + " declared"));
    card.appendChild(meta);

    if (entry.status_reason) {
      para(card, "Status reason: " + entry.status_reason, "muted small");
    }

    var facts = el("ul", "entry-facts");
    facts.appendChild(el("li", null,
      "End-of-list evidence: " + (entry.end_of_list_evidence || "none recorded")));
    facts.appendChild(el("li", null,
      "Access markers: " + (asList(entry.access_markers).join(", ") || "none")));
    card.appendChild(facts);

    var counts = (entry.plan && entry.plan.counts) || {};
    var chips = el("div", "chip-row");
    Object.keys(counts).forEach(function (name) {
      chips.appendChild(chip(name + " " + counts[name], toneFor(name)));
    });
    if (chips.childElementCount) card.appendChild(chips);
    return card;
  }

  function renderCollectionDetail(root, match) {
    var key = decodeURIComponent(match[1]);
    var ctx = resetView("Collection");
    var back = link("#/collections", "Collections");
    back.className = "back-link";
    view.insertBefore(back, ctx.status);
    getJSON("collection", { key: key }).then(function (entry) {
      clearStatus(ctx.status);
      ctx.heading.textContent = entry.name || key;

      var facts = el("dl", "facts");
      addFact(facts, "URL", entry.url || "n/a");
      addFact(facts, "Author", entry.author || "n/a");
      addFact(facts, "Kind", entry.kind);
      addFactNode(facts, "Status", badge(entry.status, toneFor(entry.status)));
      addFact(facts, "Status reason", entry.status_reason || "n/a");
      addFact(facts, "Declared count",
        entry.declared_count == null ? "unknown" : entry.declared_count);
      addFact(facts, "Observed count", entry.observed_count);
      addFact(facts, "End-of-list evidence",
        entry.end_of_list_evidence || "none recorded");
      addFact(facts, "Access markers",
        asList(entry.access_markers).join(", ") || "none");
      if (entry.plan) {
        addFact(facts, "Plan scan status", entry.plan.scan_status);
        addFact(facts, "Plan scan reason", entry.plan.scan_status_reason || "n/a");
      }
      view.appendChild(facts);

      if (entry.resume_command) {
        var resume = callout("info", "Continue this collection");
        para(resume, "Run this command in a terminal. Existing progress is "
          + "reused when valid; pending videos are selected by the run plan. "
          + "Safety checks and consent still apply.", "small");
        resume.appendChild(commandBox(entry.resume_command, "Resume collection"));
        view.appendChild(resume);
      }

      view.appendChild(el("h3", "section-title", "Items (with plan classification)"));
      var items = asList(entry.items);
      if (!items.length) {
        emptyNote(view, "No items observed.");
        return;
      }
      var wrap = el("div", "table-wrap");
      var t = newTable(["ID", "Kind", "Classification", "URL", "Detail"]);
      items.forEach(function (item) {
        var row = el("tr");
        var idCell = el("td");
        idCell.appendChild(monoId(item.id));
        var evidence = link("#/evidence/" + encodeURIComponent(item.id), "evidence");
        evidence.className = "small";
        idCell.appendChild(evidence);
        row.appendChild(idCell);
        addCell(row, item.kind);
        var classification = el("td");
        classification.appendChild(badge(item.classification,
          toneFor(item.classification)));
        row.appendChild(classification);
        var urlCell = el("td");
        var urlText = el("span", "mono truncate small", item.url || "");
        urlText.title = item.url || "";
        urlCell.appendChild(urlText);
        row.appendChild(urlCell);
        addCell(row, item.detail || "");
        t.body.appendChild(row);
      });
      wrap.appendChild(t.table);
      view.appendChild(wrap);
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  // ---- Runs view (P1-FR-05, S5) ------------------------------------------------

  function renderRuns() {
    var ctx = resetView("Runs");
    getJSON("runs").then(function (data) {
      var entries = asList(data.runs);
      clearStatus(ctx.status);
      view.appendChild(stageLegend());
      if (!entries.length) {
        emptyNote(view, "No saved run reports yet.");
        return;
      }
      var list = el("div", "stack");
      view.appendChild(list);
      // Newest first, exactly as the server orders them.
      entries.forEach(function (entry) {
        var card = el("article", "card run-row");
        var main = el("div", null);
        var title = el("h3", "card-title");
        title.appendChild(link(
          "#/runs/" + encodeURIComponent(entry.collection_key)
            + "/" + encodeURIComponent(entry.report),
          entry.collection_name || entry.collection_key
        ));
        main.appendChild(title);
        var sub = el("p", "muted small");
        sub.appendChild(timeEl(entry.saved_at));
        sub.appendChild(el("span", null, " · saved "
          + (entry.saved_at || "n/a")));
        main.appendChild(sub);
        card.appendChild(main);
        card.appendChild(countChips(entry.counts));
        list.appendChild(card);
      });
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  function renderRunDetail(root, match) {
    var key = decodeURIComponent(match[1]);
    var report = decodeURIComponent(match[2]);
    var ctx = resetView("Run report");
    var back = link("#/runs", "Runs");
    back.className = "back-link";
    view.insertBefore(back, ctx.status);

    var entryPromise = getJSON("runs").then(function (data) {
      var entry = asList(data.runs).filter(function (candidate) {
        return candidate.collection_key === key && candidate.report === report;
      })[0];
      if (!entry) throw new Error("run report not found in the saved runs");
      return entry;
    });
    // The saved report carries mode/interrupted/hard-stop facts the
    // summary rows do not; unreadable raw reports degrade to the rows.
    var rawPromise = getJSON("runReport", { key: key, report: report })
      .catch(function () { return null; });

    Promise.all([entryPromise, rawPromise]).then(function (results) {
      var entry = results[0];
      var raw = results[1] || {};
      clearStatus(ctx.status);
      ctx.heading.textContent = "Run · "
        + (entry.collection_name || entry.collection_key);

      var header = el("div", "card");
      var facts = el("dl", "facts");
      var saved = addFactNode(facts, "Saved at", timeEl(entry.saved_at));
      saved.title = entry.saved_at || "";
      addFact(facts, "Collection",
        (entry.collection_name ? entry.collection_name + " · " : "")
        + entry.collection_key);
      if (raw.mode) addFact(facts, "Mode", raw.mode);
      addFactNode(facts, "Counts", countChips(entry.counts));
      if (raw.interrupted) {
        addFactNode(facts, "Interrupted", badge("interrupted", "bad"));
      }
      header.appendChild(facts);
      view.appendChild(header);

      if (raw.hard_stop_reason) {
        var stop = callout("bad", "Hard stop");
        stop.appendChild(el("p", null, raw.hard_stop_reason));
        view.appendChild(stop);
      }

      var failures = [];
      var cleanupNotes = [];
      asList(raw.batches).forEach(function (batch) {
        failures = failures.concat(asList(batch.failures));
        cleanupNotes = cleanupNotes.concat(asList(batch.cleanup_notes));
      });
      if (failures.length) {
        var failBox = callout("warn", "Failures");
        failures.forEach(function (failure) {
          failBox.appendChild(el("p", "mono small", failureText(failure)));
        });
        view.appendChild(failBox);
      }
      if (cleanupNotes.length) {
        var notesBox = callout("info", "Cleanup notes");
        cleanupNotes.forEach(function (note) {
          notesBox.appendChild(el("p", null, note));
        });
        view.appendChild(notesBox);
      }

      var rawLink = link(endpoint("runReport", { key: key, report: report }),
        "Raw JSON");
      rawLink.className = "raw-link";
      rawLink.target = "_blank";
      rawLink.rel = "noopener";
      view.appendChild(rawLink);

      view.appendChild(el("h3", "section-title", "Items"));
      view.appendChild(stageLegend());
      var items = asList(entry.items);
      if (!items.length) emptyNote(view, "No items in this report.");
      var list = el("div", "stack");
      view.appendChild(list);
      // The saved report itself, readable in place (Raw JSON still opens it).
      if (results[1]) {
        view.appendChild(el("h3", "section-title", "Report JSON"));
        var reportCard = el("article", "card artifact-card");
        reportCard.appendChild(documentViewer(report, jsonText(results[1])));
        view.appendChild(reportCard);
      }
      items.forEach(function (item) {
        var card = el("article", "card");
        var head = el("div", "entry-head");
        var title = el("h3", "card-title");
        title.appendChild(link("#/evidence/" + encodeURIComponent(item.id),
          item.id));
        head.appendChild(title);
        if (item.author) head.appendChild(el("span", "muted small",
          "@" + item.author));
        card.appendChild(head);
        card.appendChild(stagePills(item.stages));
        if (item.next) {
          para(card, "Next: " + item.next, "muted small");
        }
        list.appendChild(card);
      });
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  function failureText(failure) {
    if (failure === null || typeof failure !== "object") {
      return String(failure);
    }
    return [failure.id, failure.stage, failure.reason].filter(function (part) {
      return part !== null && part !== undefined && part !== "";
    }).join(" · ");
  }

  // ---- Blocks view (P1-FR-06, S5) ----------------------------------------------

  function renderBlocks() {
    var ctx = resetView("Blocks");
    getJSON("blocks").then(function (data) {
      clearStatus(ctx.status);

      view.appendChild(el("h3", "section-title", "Blocked items"));
      var items = asList(data.items);
      if (!items.length) {
        emptyNote(view, "No blocked items.");
      } else {
        var list = el("div", "stack");
        view.appendChild(list);
        items.forEach(function (item) {
          var card = el("article", "card");
          var head = el("div", "entry-head");
          var title = el("h3", "card-title");
          title.appendChild(link("#/evidence/" + encodeURIComponent(item.id),
            item.id));
          head.appendChild(title);
          head.appendChild(monoId(item.id));
          card.appendChild(head);
          card.appendChild(stagePills(item.blocked_stages));
          if (item.next) {
            para(card, "Next: " + item.next, "muted small");
          }
          if (item.resume_command) {
            para(card, "Resume in a terminal after resolving the cause. "
              + "The existing consent and safety checks still apply; completed "
              + "work is reused when valid.", "muted small");
            card.appendChild(commandBox(item.resume_command, "Resume video"));
          } else {
            para(card, "No safe resume command is available for this video. "
              + "Check its identity and any permanent rejection first.", "muted small");
          }
          list.appendChild(card);
        });
      }

      view.appendChild(el("h3", "section-title", "Run-level blocks"));
      var runs = asList(data.runs);
      if (!runs.length) {
        emptyNote(view, "No run-level blocks.");
        return;
      }
      var runList = el("div", "stack");
      view.appendChild(runList);
      runs.forEach(function (run) {
        var card = el("article", "card");
        card.appendChild(el("h3", "card-title",
          run.collection_name || run.collection_key || "unknown collection"));
        var when = el("p", "muted small");
        when.appendChild(document.createTextNode("latest run saved "));
        when.appendChild(run.saved_at ? timeEl(run.saved_at) : el("span", null, "n/a"));
        when.appendChild(document.createTextNode(" · " + (run.collection_key || "")));
        card.appendChild(when);
        if (run.hard_stop_reason) {
          var stop = callout("bad", "Hard stop");
          stop.appendChild(el("p", null, run.hard_stop_reason));
          card.appendChild(stop);
        }
        if (run.failures) {
          var failBox = callout("warn", "Failures");
          asList(run.failures).forEach(function (failure) {
            failBox.appendChild(el("p", "mono small", failureText(failure)));
          });
          card.appendChild(failBox);
        }
        if (run.cleanup_notes) {
          var notesBox = callout("info", "Cleanup notes");
          asList(run.cleanup_notes).forEach(function (note) {
            notesBox.appendChild(el("p", null, note));
          });
          card.appendChild(notesBox);
        }
        if (run.resume_command) {
          para(card, "After resolving the stop, resume the collection in a "
            + "terminal. The run plan and its safety checks remain in effect.", "muted small");
          card.appendChild(commandBox(run.resume_command, "Resume collection"));
        } else {
          para(card, "No safe collection resume command is available.", "muted small");
        }
        runList.appendChild(card);
      });
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  // ---- Evidence view (P1-FR-07) ----------------------------------------------------

  function renderVideoOutcome(target, videoId, names) {
    function documentIfAvailable(name) {
      if (names.indexOf(name) === -1) return Promise.resolve(null);
      return getJSON("videoEvidence", { video_id: videoId, name: name })
        .catch(function () { return null; });
    }
    if (names.indexOf("synthesis.json") === -1
      && names.indexOf("verification.json") === -1) return;
    var card = el("article", "card");
    card.appendChild(el("h3", "card-title", "What this video produced"));
    var body = el("div", "stack");
    para(body, "Loading results...", "status");
    card.appendChild(body);
    target.appendChild(card);
    Promise.all([documentIfAvailable("synthesis.json"),
      documentIfAvailable("verification.json")]).then(function (docs) {
      body.textContent = "";
      var outcome = window.TIReader.videoOutcome(docs[0], docs[1]);
      if (!docs[0]) para(body, "Synthesis is unavailable or unreadable.", "muted small");
      if (outcome.classification) {
        para(body, "Classification: " + classificationText(outcome.classification));
      }
      if (outcome.entities.length) {
        para(body, "Entities: " + outcome.entities.join(", "));
      }
      if (outcome.actions.length) {
        body.appendChild(el("h4", "card-title", "Possible actions"));
        var actions = el("ul");
        outcome.actions.forEach(function (action) {
          actions.appendChild(el("li", null, action));
        });
        body.appendChild(actions);
      }
      body.appendChild(el("h4", "card-title", "Claims and provenance"));
      if (!outcome.verification_available) {
        para(body, "Verification is unavailable or unreadable. Synthesis "
          + "claims below are not independently verified.", "muted small");
      }
      if (!outcome.claims.length) emptyNote(body, "No claims recorded.");
      outcome.claims.forEach(function (claim) {
        var item = el("div", "card claim-card");
        var head = el("div", "claim-head");
        head.appendChild(el("p", "claim-text", claim.claim));
        if (outcome.verification_available) {
          var displayed = window.TIReader.verdictPresentation(claim);
          head.appendChild(badge(displayed.label, displayed.tone || toneFor(claim.verdict)));
        }
        item.appendChild(head);
        if (claim.source) para(item, "Video evidence source: " + claim.source, "muted small");
        if (claim.reason) para(item, "Verification reason: " + claim.reason, "small");
        if (outcome.verification_available && !claim.fetches.length) {
          para(item, "No source fetch recorded for this claim.", "muted small");
        }
        if (claim.fetches.length) {
          para(item, "Source fetch records: " + jsonText(claim.fetches), "mono small");
        }
        if (claim.evidence) para(item, "Evidence: " + (typeof claim.evidence === "object"
          ? jsonText(claim.evidence) : claim.evidence), "muted small");
        body.appendChild(item);
      });
    }).catch(function (err) { fail(body, err); });
  }

  function renderEvidence(root, match) {
    var ctx = resetView("Evidence");
    clearStatus(ctx.status);
    var initialId = match ? decodeURIComponent(match[1]) : "";

    var form = el("form", "search-form");
    var field = el("div", "field");
    var label = el("label", null, "Video ID");
    label.setAttribute("for", "evidence-id");
    var input = el("input");
    input.id = "evidence-id";
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("placeholder", "numeric video id");
    field.appendChild(label);
    field.appendChild(input);
    form.appendChild(field);
    var submit = el("button", "btn btn-primary", "Look up");
    submit.type = "submit";
    form.appendChild(submit);
    view.appendChild(form);

    var result = el("div", "stack");
    view.appendChild(result);

    function showArtifact(videoId, name, slot) {
      slot.textContent = "";
      var loading = para(slot, "Loading " + name + "...", "status");
      loading.setAttribute("role", "status");
      return getText("videoEvidence", { video_id: videoId, name: name })
        .then(function (text) {
          slot.textContent = "";
          slot.appendChild(documentViewer(name, text));
          return null;
        })
        .catch(function (err) {
          slot.textContent = "";
          fail(para(slot, ""), err);
          return null;
        });
    }

    function lookup(videoId) {
      result.textContent = "";
      var status = para(result, "Looking up " + videoId + "...", "status");
      status.setAttribute("role", "status");
      getJSON("video", { video_id: videoId }).then(function (payload) {
        status.remove();
        asList(payload.items).forEach(function (item) {
          // Status summary card
          var summary = el("article", "card");
          var head = el("div", "entry-head");
          head.appendChild(el("h3", "card-title", item.video_id));
          head.appendChild(monoId(item.video_id));
          summary.appendChild(head);
          var meta = el("div", "entry-meta");
          meta.appendChild(badge(item.processed ? "processed" : "not processed",
            item.processed ? "ok" : "muted"));
          meta.appendChild(badge(item.blocklisted ? "blocklisted" : "not blocklisted",
            item.blocklisted ? "bad" : "muted"));
          if (item.backlog && item.backlog.emitted) {
            meta.appendChild(badge("backlog " + (item.backlog.status || "n/a"),
              toneFor(item.backlog.status)));
          } else {
            meta.appendChild(badge("backlog not emitted", "muted"));
          }
          if (item.completed_at) {
            var completed = el("span", "muted small", "completed ");
            completed.appendChild(timeEl(item.completed_at));
            meta.appendChild(completed);
          }
          summary.appendChild(meta);
          if (item.stage_outcomes) {
            summary.appendChild(stagePills(item.stage_outcomes));
          } else if (item.stages) {
            para(summary, asList(item.stages).join(", "), "muted small");
          }
          if (item.extraction_outcome != null) {
            summary.appendChild(badge("extraction " + item.extraction_outcome,
              toneFor(item.extraction_outcome)));
          }
          result.appendChild(summary);

          // Artifact tabs + scrollable text with copy
          var heading = el("h4", "section-title", "Evidence artifacts");
          result.appendChild(heading);
          var names = asList(item.evidence);
          if (!names.length) {
            emptyNote(result, "No evidence artifacts recorded.");
            return;
          }
          renderVideoOutcome(result, item.video_id, names);
          var artifactCard = el("article", "card artifact-card");
          var tabs = el("div", "actions-row");
          var artifactHead = el("div", "narration-head");
          var artifactTitle = el("h3", "card-title", "Artifact");
          var artifactNote = el("p", "muted small artifact-note");
          var slot = el("div", "artifact-slot");
          var buttons = names.map(function (name) {
            var button = el("button", "btn btn-sm", name);
            button.type = "button";
            if (ARTIFACT_HELP[name]) button.title = ARTIFACT_HELP[name];
            button.setAttribute("aria-pressed", "false");
            button.addEventListener("click", function () {
              buttons.forEach(function (other) {
                other.setAttribute("aria-pressed", "false");
              });
              button.setAttribute("aria-pressed", "true");
              artifactTitle.textContent = name;
              artifactNote.textContent = ARTIFACT_HELP[name] || "";
              artifactNote.hidden = !ARTIFACT_HELP[name];
              showArtifact(item.video_id, name, slot);
            });
            tabs.appendChild(button);
            return button;
          });
          artifactHead.appendChild(artifactTitle);
          artifactCard.appendChild(tabs);
          artifactCard.appendChild(artifactHead);
          artifactCard.appendChild(artifactNote);
          artifactCard.appendChild(slot);
          result.appendChild(artifactCard);
          // Load the first artifact eagerly.
          buttons[0].click();
        });
      }).catch(function (err) { fail(status, err); return null; });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var videoId = input.value.trim();
      if (videoId) lookup(videoId);
    });

    if (initialId) {
      input.value = initialId;
      lookup(initialId);
    }
  }

  // ---- Review view (P1-FR-08/11/12, S4) ---------------------------------------------

  var REJECT_REASON_RULE =
    "a rejected decision requires a reason (it becomes the permanent " +
    "blocklist reason)";

  // Client-side record of decisions recorded in THIS session, for the
  // "not applied yet" counter and the list's decision dots.
  var sessionDecided = {};

  // Live review state for keyboard shortcuts (null outside #/review).
  var reviewState = null;

  function planDecisionIds(planData) {
    var ids = {};
    if (planData && planData.plan) {
      asList(planData.plan.decisions).forEach(function (item) {
        ids[String(item.id)] = true;
      });
    }
    return ids;
  }

  function renderReview(root, match) {
    var selectedId = match && match[1] ? decodeURIComponent(match[1]) : "";
    var ctx = resetView("Pending review");
    var banner = el("div", "review-banner");
    view.appendChild(banner);
    var layout = el("div", "review-layout");
    view.appendChild(layout);
    var listPane = el("section", "review-list card");
    listPane.setAttribute("aria-label", "Pending entries");
    var detailPane = el("section", "review-detail");
    layout.appendChild(listPane);
    layout.appendChild(detailPane);

    var entries = [];
    var planData = null;

    function renderBanner() {
      var decidedCount = planData && planData.plan
        ? asList(planData.plan.decisions).length
        : Object.keys(sessionDecided).length;
      banner.textContent = "";
      banner.hidden = !decidedCount;
      if (!decidedCount) return;
      banner.appendChild(el("p", null, decidedCount
        + (decidedCount === 1 ? " decision" : " decisions")
        + " recorded, not applied yet"));
      var preview = el("button", "btn btn-primary btn-sm", "Preview plan");
      preview.type = "button";
      preview.addEventListener("click", function () {
        loadPlanView();
      });
      banner.appendChild(preview);
    }

    function refreshPlan(after) {
      getJSON("backlogPlan").then(function (data) {
        planData = data;
        renderBanner();
        renderList();
        if (after) after();
        return null;
      }).catch(function () {
        // No decisions document (404) or unreadable plan: the session
        // counter still tells the truth.
        planData = null;
        renderBanner();
        if (after) after();
        return null;
      });
    }

    function renderList() {
      listPane.textContent = "";
      listPane.appendChild(el("h3", "card-title", "Pending entries"));
      var hint = el("p", "shortcuts-hint",
        "j/k move · a accept · r reject · ? help");
      listPane.appendChild(hint);
      if (!entries.length) {
        emptyNote(listPane, "No pending backlog entries.");
        return;
      }
      var decided = planDecisionIds(planData);
      Object.keys(sessionDecided).forEach(function (id) {
        decided[id] = true;
      });
      var ul = el("ul", "review-items");
      entries.forEach(function (entry) {
        var li = el("li");
        var row = el("a", "review-item");
        row.href = "#/review/" + encodeURIComponent(entry.id);
        var currentId = selectedId || (entries[0] && entries[0].id);
        if (entry.id === currentId) row.className = "review-item current";
        if (decided[entry.id]) {
          var dot = el("span", "dot dot-ok");
          dot.title = "decision recorded locally";
          row.appendChild(dot);
        }
        row.appendChild(el("span", "review-author", entry.author || "unknown"));
        row.appendChild(el("span", "mono", entry.id));
        row.appendChild(timeEl(entry.ingested_at, "small"));
        li.appendChild(row);
        ul.appendChild(li);
      });
      listPane.appendChild(ul);
    }

    function refresh() {
      renderList();
      renderBanner();
    }

    function loadPlanView() {
      banner.textContent = "";
      banner.hidden = false;
      var loading = el("p", "status", "Loading plan...");
      loading.setAttribute("role", "status");
      banner.appendChild(loading);
      getJSON("backlogPlan").then(function (data) {
        planData = data;
        banner.textContent = "";
        banner.hidden = false;
        banner.appendChild(receiptOrPlanCard(data, refresh, function () {
          // Applied: local decisions are published now.
          sessionDecided = {};
          refreshPlan();
          toast("apply finished", "ok");
        }));
        return null;
      }).catch(function (err) {
        banner.textContent = "";
        fail(banner, err);
        return null;
      });
    }

    getJSON("backlog", null, { status: "pending" }).then(function (data) {
      entries = asList(data.entries);
      clearStatus(ctx.status);
      renderList();
      reviewState = {
        entries: entries,
        selectedId: selectedId || (entries[0] && entries[0].id),
        acceptCurrent: null,
        rejectCurrent: null
      };
      renderReviewDetailPane(detailPane, entries,
        selectedId || (entries[0] && entries[0].id), refresh);
      refreshPlan();
      return null;
    }).catch(function (err) { fail(ctx.status, err); return null; });
  }

  function renderReviewDetailPane(pane, entries, selectedId, refresh) {
    pane.textContent = "";
    if (!selectedId) {
      var card = el("div", "card");
      card.appendChild(el("h3", "card-title", "No entry selected"));
      emptyNote(card, "Nothing is waiting for review.");
      pane.appendChild(card);
      return;
    }
    var status = para(pane, "Loading entry...", "status");
    status.setAttribute("role", "status");
    getJSON("backlogEntry", { video_id: selectedId }).then(function (doc) {
      status.remove();
      buildEntryDetail(pane, doc, entries, refresh);
      return null;
    }).catch(function (err) {
      status.remove();
      fail(pane, err);
      return null;
    });
  }

  function buildEntryDetail(pane, doc, entries, refresh) {
    pane.textContent = "";
    var entry = doc.entry || {};
    var videoId = String(doc.id || entry.id || "");

    var header = el("header", "detail-head");
    header.appendChild(el("h3", "card-title", videoId));
    if (entry.author) {
      para(header, "@" + entry.author, "muted small");
    }
    pane.appendChild(header);

    // URL: truncated display + copy + explicit open in a new context.
    if (entry.url) {
      var urlRow = el("div", "url-row");
      var urlLink = link(entry.url, entry.url);
      urlLink.className = "mono url-link truncate";
      urlLink.title = entry.url;
      urlRow.appendChild(urlLink);
      urlRow.appendChild(copyButton(function () { return entry.url; }));
      var open = link(entry.url, "Open");
      open.className = "btn btn-ghost btn-sm";
      open.setAttribute("target", "_blank");
      open.setAttribute("rel", "noopener noreferrer");
      urlRow.appendChild(open);
      pane.appendChild(urlRow);
    }

    var facts = el("dl", "facts");
    addFactNode(facts, "status",
      badge(entry.status || "pending", toneFor(entry.status)));
    if (entry.ingested_at) {
      addFactNode(facts, "ingested", timeEl(entry.ingested_at));
    }
    if (entry.classification) {
      addFact(facts, "classification", classificationText(entry.classification));
    }
    var hash = addFact(facts, "entry hash", doc.entry_sha256 || "n/a");
    hash.className = "dd mono small";
    pane.appendChild(facts);

    pane.appendChild(el("h4", "section-title", "Claims"));
    if (doc.verification) {
      var verificationMeta = el("p", "muted small",
        "rules " + (doc.verification.rules_version || "n/a")
        + " · generated " + (doc.verification.generated_at || "n/a"));
      pane.appendChild(verificationMeta);
      var claims = asList(doc.verification.claims);
      if (!claims.length) {
        emptyNote(pane, "No claims recorded.");
      } else {
        claims.forEach(function (claim) {
          var item = el("div", "card claim-card");
          var head = el("div", "claim-head");
          head.appendChild(el("p", "claim-text", claim.claim || ""));
          // A recorded verdict alone is not evidence of an external check.
          var displayed = window.TIReader.verdictPresentation(claim);
          head.appendChild(badge(displayed.label, displayed.tone || toneFor(claim.verdict)));
          item.appendChild(head);
          if (claim.reason) {
            para(item, "Reason: " + claim.reason, "muted small");
          }
          if (claim.note) {
            para(item, "Note: " + claim.note, "muted small");
          }
          if (claim.evidence !== null && claim.evidence !== undefined
            && claim.evidence !== "") {
            para(item, "evidence: " + (typeof claim.evidence === "object"
              ? jsonText(claim.evidence) : claim.evidence), "mono small");
          }
          pane.appendChild(item);
        });
      }
    } else {
      emptyNote(pane, doc.verification_note || "No verification results.");
    }

    // Sticky action bar: Accept / Reject (reveals the required reason) /
    // Next / Prev. One explicit decision per entry (P1-FR-12).
    pane.appendChild(el("h4", "section-title", "Decision"));
    para(pane, "The decision binds this exact entry hash; a changed entry "
      + "aborts with zero writes.", "muted small");

    var reasonPanel = el("div", "reason-panel");
    reasonPanel.hidden = true;
    var reasonField = el("div", "field");
    var reasonLabel = el("label", null, "Reason (required for reject)");
    reasonLabel.setAttribute("for", "reject-reason");
    var reasonInput = el("textarea");
    reasonInput.id = "reject-reason";
    reasonField.appendChild(reasonLabel);
    reasonField.appendChild(reasonInput);
    reasonPanel.appendChild(reasonField);
    var reasonActions = el("div", "actions-row");
    var reasonCancel = el("button", "btn btn-ghost", "Back");
    reasonCancel.type = "button";
    var reasonConfirm = el("button", "btn btn-danger", "Confirm reject");
    reasonConfirm.type = "button";
    reasonActions.appendChild(reasonCancel);
    reasonActions.appendChild(reasonConfirm);
    reasonPanel.appendChild(reasonActions);
    pane.appendChild(reasonPanel);

    var message = el("p", "form-message");
    message.setAttribute("role", "alert");
    pane.appendChild(message);

    var bar = el("div", "action-bar");
    var prevButton = el("button", "btn btn-ghost", "Prev");
    prevButton.type = "button";
    prevButton.title = "k";
    var acceptButton = el("button", "btn btn-primary", "Accept");
    acceptButton.type = "button";
    acceptButton.title = "a";
    var rejectButton = el("button", "btn btn-danger", "Reject");
    rejectButton.type = "button";
    rejectButton.title = "r";
    var nextButton = el("button", "btn btn-ghost", "Next");
    nextButton.type = "button";
    nextButton.title = "j";
    var helpButton = el("button", "btn btn-ghost btn-sm", "?");
    helpButton.type = "button";
    helpButton.title = "Keyboard shortcuts";
    bar.appendChild(prevButton);
    bar.appendChild(acceptButton);
    bar.appendChild(rejectButton);
    bar.appendChild(nextButton);
    bar.appendChild(helpButton);
    pane.appendChild(bar);

    var index = -1;
    entries.forEach(function (candidate, position) {
      if (String(candidate.id) === videoId) index = position;
    });
    prevButton.disabled = index <= 0;
    nextButton.disabled = index === -1 || index >= entries.length - 1;

    function navigate(step) {
      if (index === -1) return;
      var target = entries[index + step];
      if (target) location.hash = "#/review/" + encodeURIComponent(target.id);
    }

    prevButton.addEventListener("click", function () { navigate(-1); });
    nextButton.addEventListener("click", function () { navigate(1); });
    helpButton.addEventListener("click", function () {
      toggleShortcutHelp();
    });

    function setBusy(busy) {
      acceptButton.disabled = busy;
      rejectButton.disabled = busy;
      reasonConfirm.disabled = busy;
      reasonCancel.disabled = busy;
    }

    function decide(payload) {
      setBusy(true);
      message.className = "form-message";
      message.textContent = "";
      return postJSON("backlogDecision", payload, { video_id: videoId })
        .then(function () {
          sessionDecided[videoId] = true;
          message.className = "ok-note";
          message.textContent = "Decision recorded. Preview and apply it "
            + "from the banner above.";
          reasonPanel.hidden = true;
          refresh();
          return null;
        })
        .catch(function (err) {
          setBusy(false);
          message.className = "error";
          message.textContent = err.message;
          return null;
        });
    }

    acceptButton.addEventListener("click", function () {
      decide({ decision: "accepted", entry_sha256: doc.entry_sha256 });
    });

    rejectButton.addEventListener("click", function () {
      // Reject reveals the required reason; the mirror of the server
      // rule refuses an empty reason.
      reasonPanel.hidden = false;
      reasonInput.focus();
    });

    reasonCancel.addEventListener("click", function () {
      reasonPanel.hidden = true;
      message.className = "form-message";
      message.textContent = "";
    });

    reasonConfirm.addEventListener("click", function () {
      var reason = reasonInput.value.trim();
      if (!reason) {
        message.className = "error";
        message.textContent = REJECT_REASON_RULE;
        reasonInput.focus();
        return;
      }
      decide({
        decision: "rejected",
        entry_sha256: doc.entry_sha256,
        reason: reason
      });
    });

    // Register for keyboard shortcuts while this pane is live.
    if (reviewState) {
      reviewState.selectedId = videoId;
      reviewState.acceptCurrent = function () { acceptButton.click(); };
      reviewState.rejectCurrent = function () {
        rejectButton.click();
        reasonInput.focus();
      };
      reviewState.next = function () { navigate(1); };
      reviewState.prev = function () { navigate(-1); };
    }
  }

  // Plan preview (what will change) -> confirm apply -> receipt card.
  function receiptOrPlanCard(data, refresh, onApplied) {
    var plan = (data && data.plan) || {};
    var card = el("div", "card plan-card");
    card.appendChild(el("h3", "card-title",
      "Plan preview - nothing written yet"));

    var conflicts = asList(plan.conflicts);
    if (conflicts.length) {
      var conflictBox = callout("bad", "Conflicts - apply is refused");
      conflicts.forEach(function (conflict) {
        conflictBox.appendChild(el("p", null,
          conflict.id + ": " + conflict.reason));
      });
      card.appendChild(conflictBox);
    }

    card.appendChild(el("p", "muted small", "What will change:"));
    var changes = el("div", "stack");
    asList(plan.decisions).forEach(function (item) {
      var row = el("div", "plan-row");
      var id = el("div", null);
      id.appendChild(monoId(item.id));
      row.appendChild(id);
      row.appendChild(badge(item.decision, toneFor(item.decision)));
      var markers = [];
      if (item.current_status != null && item.current_status !== item.decision) {
        markers.push(item.current_status + " -> " + item.decision);
      }
      if (item.rewrite) markers.push("rewrites backlog line");
      if (item.already_applied) markers.push("already applied");
      if (item.append_blocklist) markers.push("appends blocklist entry");
      row.appendChild(el("span", "muted small",
        markers.join(" · ") || "no change"));
      changes.appendChild(row);
    });
    card.appendChild(changes);

    var files = el("div", "chip-row");
    asList(plan.files_to_write).forEach(function (name) {
      files.appendChild(chip("write " + name, "warn"));
    });
    if (files.childElementCount) card.appendChild(files);

    var hash = para(card, "plan_sha256: " + data.plan_sha256, "mono small");
    hash.title = data.plan_sha256;

    var actions = el("div", "actions-row");
    var cancelButton = el("button", "btn btn-ghost", "Do not apply");
    cancelButton.type = "button";
    var applyButton = el("button", "btn btn-danger", "Confirm apply");
    applyButton.type = "button";
    applyButton.disabled = conflicts.length > 0;
    actions.appendChild(cancelButton);
    actions.appendChild(applyButton);
    card.appendChild(actions);
    if (conflicts.length) {
      para(card, "Apply stays disabled until the conflicts above are "
        + "resolved.", "muted small");
    }

    cancelButton.addEventListener("click", function () { refresh(); });

    applyButton.addEventListener("click", function () {
      applyButton.disabled = true;
      cancelButton.disabled = true;
      // Explicit confirm step bound to the previewed plan fingerprint.
      postJSON("backlogApply", { plan_sha256: data.plan_sha256 })
        .then(function (receipt) {
          if (onApplied) onApplied();
          return receipt;
        })
        .catch(function (err) {
          applyButton.disabled = false;
          cancelButton.disabled = false;
          var note = el("p", "error", err.message);
          note.setAttribute("role", "alert");
          card.appendChild(note);
          return null;
        })
        .then(function (receipt) {
          if (!receipt) return null;
          var parent = card.parentElement;
          if (parent) {
            parent.textContent = "";
            parent.appendChild(receiptCard(receipt));
          }
          return null;
        });
    });

    return card;
  }

  function receiptCard(receipt) {
    var card = el("div", "card receipt-card");
    card.appendChild(el("h3", "card-title", "Apply finished - receipt"));
    var facts = el("dl", "facts");
    addFact(facts, "status", receipt.status == null ? "n/a" : receipt.status);
    addFact(facts, "applied", asList(receipt.applied).join(", ") || "none");
    addFact(facts, "already applied",
      asList(receipt.already_applied).join(", ") || "none");
    var files = asList(receipt.files_written).map(function (file) {
      return typeof file === "object" && file !== null ? file.path : file;
    });
    addFact(facts, "files written", files.join(", ") || "none");
    addFact(facts, "backups", String(asList(receipt.backups).length));
    if (receipt.recovery) addFact(facts, "recovery", receipt.recovery);
    card.appendChild(facts);
    card.appendChild(el("pre", "verbatim", jsonText(receipt)));
    return card;
  }

  // ---- consent modal (S6, P1-FR-09, P1-FR-19) -----------------------------------

  var modal = document.getElementById("consent-modal");
  var modalCard = modal.querySelector(".modal-card");
  var modalHeading = document.getElementById("consent-heading");
  var modalFields = document.getElementById("consent-fields");
  var modalRendered = document.getElementById("consent-rendered");
  var modalError = document.getElementById("consent-error");
  var denyButton = document.getElementById("consent-deny");
  var authorizeButton = document.getElementById("consent-authorize");
  var readyButton = document.getElementById("consent-ready");
  var currentPrompt = null;

  var WINDOW_FIELDS = [
    "kind", "title", "ids", "destinations", "operations", "limits",
    "effects", "model"
  ];

  function fieldText(value) {
    if (value === null || value === undefined) return "none";
    if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
    return String(value);
  }

  function setModalBusy(busy) {
    [denyButton, authorizeButton, readyButton].forEach(function (button) {
      button.disabled = busy;
    });
  }

  function showModal(prompt) {
    if (currentPrompt && prompt && currentPrompt.id === prompt.id) return;
    currentPrompt = prompt;
    modalFields.textContent = "";
    var readiness = prompt.kind === "readiness";
    var facts = el("dl", "facts");
    if (readiness) {
      modalHeading.textContent = "Browser readiness";
      addFact(facts, "kind", fieldText(prompt.kind));
      addFact(facts, "prompt", fieldText(prompt.prompt));
      denyButton.hidden = true;
      authorizeButton.hidden = true;
      readyButton.hidden = false;
    } else {
      modalHeading.textContent = "Authorization required";
      var consentWindow = prompt.window || {};
      WINDOW_FIELDS.forEach(function (name) {
        addFact(facts, name, fieldText(consentWindow[name]));
      });
      denyButton.hidden = false;
      authorizeButton.hidden = false;
      readyButton.hidden = true;
    }
    modalFields.appendChild(facts);
    modalRendered.textContent = prompt.rendered || "";
    modalError.textContent = "";
    modalError.hidden = true;
    setModalBusy(false);
    modal.hidden = false;
    (readiness ? readyButton : denyButton).focus();
  }

  function hideModal() {
    modal.hidden = true;
    currentPrompt = null;
  }

  function answerPrompt(action, payload) {
    if (!currentPrompt) return;
    setModalBusy(true);
    modalError.className = "";
    postJSON(action, payload)
      .then(function () {
        // The consent-resolved event dismisses the modal; a transport
        // hiccup leaves it open until that event arrives.
        modalError.hidden = true;
        return null;
      })
      .catch(function (err) {
        setModalBusy(false);
        modalError.className = "error";
        modalError.textContent = err.message;
        modalError.hidden = false;
        return null;
      });
  }

  denyButton.addEventListener("click", function () {
    answerPrompt("consent", {
      prompt_id: currentPrompt.id, granted: false
    });
  });

  authorizeButton.addEventListener("click", function () {
    answerPrompt("consent", {
      prompt_id: currentPrompt.id, granted: true
    });
  });

  readyButton.addEventListener("click", function () {
    answerPrompt("ready", { prompt_id: currentPrompt.id });
  });

  // Keyboard: Tab cycles inside the modal only. Escape is deliberately
  // not handled: nothing may dismiss the modal except a decision.
  modalCard.addEventListener("keydown", function (event) {
    if (event.key !== "Tab") return;
    var focusables = [denyButton, authorizeButton, readyButton].filter(
      function (button) { return !button.hidden; }
    );
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  // ---- run stream (S5 + S8, P1-FR-13) -----------------------------------------------

  // The narration log is DERIVED stream output, never an authority. The
  // buffer survives view switches; the element is rebuilt per render.
  var NARRATION_LIMIT = 2000;
  var narrationBuffer = [];
  var narrationEl = null;
  var autoScroll = true;
  var events = null;

  function appendLine(text) {
    narrationBuffer.push(String(text));
    if (narrationBuffer.length > NARRATION_LIMIT) {
      narrationBuffer.shift();
    }
    if (!narrationEl) return;
    var line = el("div", null, text);
    narrationEl.appendChild(line);
    if (narrationEl.childElementCount > NARRATION_LIMIT) {
      narrationEl.removeChild(narrationEl.firstChild);
    }
    if (autoScroll) narrationEl.scrollTop = narrationEl.scrollHeight;
  }

  function eventJSON(event) {
    try { return JSON.parse(event.data); } catch (err) { return {}; }
  }

  function stopEvents() {
    if (events) {
      events.close();
      events = null;
    }
  }

  function connectEvents() {
    if (events) return;
    events = new EventSource(endpoint("events"));
    events.addEventListener("line", function (event) {
      var data = eventJSON(event);
      if (typeof data.text === "string") appendLine(data.text);
    });
    events.addEventListener("consent", function (event) {
      var data = eventJSON(event);
      if (data.prompt && data.prompt.id) showModal(data.prompt);
    });
    events.addEventListener("consent-resolved", function () {
      hideModal();
    });
    events.addEventListener("error", function (event) {
      // A run error event carries data; a dropped connection does not.
      if (event && typeof event.data === "string") {
        var data = eventJSON(event);
        if (data.message) appendLine("error: " + data.message);
      }
    });
    events.addEventListener("finished", function (event) {
      var data = eventJSON(event);
      var parts = ["run finished: exit code "
        + (data.exit_code === undefined ? "?" : data.exit_code)];
      if (data.interrupted) parts.push("interrupted");
      if (data.error) parts.push(data.error);
      appendLine(parts.join(" - "));
      if (data.report_path) appendLine("report: " + data.report_path);
      stopEvents();
      hideModal();
      refreshActive();
    });
  }

  var enginePill = document.getElementById("engine-pill");
  var runStatePill = document.getElementById("run-state-pill");

  function setPill(pill, text, tone) {
    pill.textContent = text;
    pill.className = "pill pill-" + (tone || "muted");
  }

  var activeRunData = null;

  function refreshActive() {
    return getJSON("activeRun").then(function (data) {
      activeRunData = data;
      setPill(runStatePill, "run: " + data.state,
        data.state === "running" ? "info" : "muted");
      if (data.state === "running") {
        connectEvents();
        if (data.pending_prompt && data.pending_prompt.id) {
          showModal(data.pending_prompt);
        } else if (currentPrompt) {
          hideModal();
        }
      } else {
        stopEvents();
        if (currentPrompt) hideModal();
      }
      return null;
    }).catch(function (err) {
      setPill(runStatePill, "run: unavailable", "bad");
      runStatePill.title = err.message;
      return null;
    });
  }

  // ---- Run section (S3): its own view at #/run ---------------------------------------

  function renderRun() {
    var ctx = resetView("Run");
    var status = ctx.status;
    getJSON("activeRun").then(function (data) {
      clearStatus(status);
      activeRunData = data;
      renderRunView(data);
      return null;
    }).catch(function (err) { fail(status, err); return null; });
  }

  function renderRunView(data) {
    var box = el("div", "stack");
    view.appendChild(box);

    // state pill row
    var stateCard = el("div", "card");
    var stateHead = el("div", "entry-meta");
    stateHead.appendChild(el("h3", "card-title", "Launching"));
    stateHead.appendChild(badge(data.state === "running" ? "running" : "idle",
      data.state === "running" ? "info" : "muted"));
    stateCard.appendChild(stateHead);
    box.appendChild(stateCard);

    if (data.allow_runs === false) {
      // The L3 hold, explained: what is missing and what works instead.
      var info = callout("info", "Launching from the web is disabled");
      info.appendChild(el("p", null,
        "Run launching stays disabled until the Phase 0 acceptance "
        + "checklist items 7 and 8 have live evidence. The engine itself "
        + "is not on hold - it runs the same from a terminal:"));
      info.appendChild(commandBox("ti <url>", "Terminal"));
      box.appendChild(info);
    }

    if (data.state === "running" && data.run) {
      var activeCard = el("div", "card");
      activeCard.appendChild(el("h3", "card-title", "Active run"));
      var facts = el("dl", "facts");
      addFact(facts, "url", data.run.url || "n/a");
      addFact(facts, "kind", data.run.kind || "n/a");
      addFact(facts, "started at", data.run.started_at || "n/a");
      activeCard.appendChild(facts);
      var cancelButton = el("button", "btn btn-danger", "Cancel run");
      cancelButton.type = "button";
      activeCard.appendChild(cancelButton);
      cancelButton.addEventListener("click", function () {
        cancelButton.disabled = true;
        postJSON("cancel", {}).then(function (result) {
          appendLine(result && result.joined
            ? "cancel: run stopped at a safe point"
            : "cancel requested; the run stops at its next safe point");
          refreshActive();
          return null;
        }).catch(function (err) {
          cancelButton.disabled = false;
          toast(err.message, "error");
          return null;
        });
      });
      box.appendChild(activeCard);
    }

    // Start form: shown disabled while launching is on hold.
    var formCard = el("div", "card");
    formCard.appendChild(el("h3", "card-title", "Start a run"));
    var disabled = data.allow_runs === false;
    var form = el("form", "run-form");
    var urlField = el("div", "field");
    var urlLabel = el("label", null, "Video or collection URL");
    urlLabel.setAttribute("for", "run-url");
    var urlInput = el("input");
    urlInput.id = "run-url";
    urlInput.name = "url";
    urlInput.type = "text";
    urlInput.autocomplete = "off";
    urlInput.spellcheck = false;
    urlField.appendChild(urlLabel);
    urlField.appendChild(urlInput);
    form.appendChild(urlField);

    var limitField = el("div", "field");
    var limitLabel = el("label", null, "Limit (items per batch)");
    limitLabel.setAttribute("for", "run-limit");
    var limitInput = el("input");
    limitInput.id = "run-limit";
    limitInput.name = "limit";
    limitInput.type = "number";
    limitInput.min = "1";
    limitInput.step = "1";
    limitField.appendChild(limitLabel);
    limitField.appendChild(limitInput);
    form.appendChild(limitField);

    var options = el("fieldset", "options");
    options.appendChild(el("legend", null, "Options"));
    var optionSpecs = [
      ["opt-dry-run", "dry_run", "Dry run"],
      ["opt-no-auto-verify", "no_auto_verify", "No auto verify"],
      ["opt-rescan", "rescan", "Rescan"]
    ];
    var optionInputs = {};
    optionSpecs.forEach(function (spec) {
      var field = el("div", "field option");
      var input = el("input");
      input.type = "checkbox";
      input.id = spec[0];
      input.name = spec[1];
      var label = el("label", null, spec[2]);
      label.setAttribute("for", spec[0]);
      field.appendChild(input);
      field.appendChild(label);
      options.appendChild(field);
      optionInputs[spec[1]] = input;
    });
    form.appendChild(options);

    var startButton = el("button", "btn btn-primary", "Start");
    startButton.type = "submit";
    if (disabled) startButton.title =
      "disabled until the Phase 0 checklist items 7 and 8 have live evidence";
    form.appendChild(startButton);

    var runError = el("p", "error");
    runError.setAttribute("role", "alert");
    runError.hidden = true;
    formCard.appendChild(form);
    formCard.appendChild(runError);

    [urlInput, limitInput, startButton].forEach(function (control) {
      control.disabled = disabled;
    });
    Object.keys(optionInputs).forEach(function (name) {
      optionInputs[name].disabled = disabled;
    });

    function showRunError(message) {
      runError.hidden = false;
      runError.textContent = message;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      runError.hidden = true;
      var payload = { url: urlInput.value.trim() };
      if (!payload.url) {
        showRunError("a URL is required");
        return;
      }
      var limitRaw = limitInput.value.trim();
      if (limitRaw) {
        var limit = Number(limitRaw);
        if (!Number.isInteger(limit) || limit < 1) {
          showRunError("limit must be an integer");
          return;
        }
        payload.limit = limit;
      }
      if (optionInputs.dry_run.checked) payload.dry_run = true;
      if (optionInputs.no_auto_verify.checked) payload.no_auto_verify = true;
      if (optionInputs.rescan.checked) payload.rescan = true;
      startButton.disabled = true;
      postJSON("runs", payload).then(function () {
        startButton.disabled = false;
        appendLine("run started: " + payload.url);
        urlInput.value = "";
        refreshActive();
        renderRun();
        return null;
      }).catch(function (err) {
        startButton.disabled = false;
        showRunError(err.message);
        return null;
      });
    });

    box.appendChild(formCard);

    // Narration log: monospace, autoscroll toggle, polite live region.
    var narrationCard = el("div", "card narration-card");
    var narrationHead = el("div", "narration-head");
    narrationHead.appendChild(el("h3", "card-title", "Run narration"));
    var autoscrollField = el("label", "autoscroll-field");
    var autoscrollInput = el("input");
    autoscrollInput.type = "checkbox";
    autoscrollInput.checked = autoScroll;
    autoscrollInput.id = "autoscroll";
    autoscrollField.setAttribute("for", "autoscroll");
    autoscrollField.appendChild(autoscrollInput);
    autoscrollField.appendChild(el("span", null, "Autoscroll"));
    narrationHead.appendChild(autoscrollField);
    narrationCard.appendChild(narrationHead);
    var narration = el("div", "narration");
    narration.id = "narration";
    narration.setAttribute("aria-live", "polite");
    narration.setAttribute("aria-label", "Run narration log");
    narration.setAttribute("role", "log");
    narration.tabIndex = 0;
    narrationCard.appendChild(narration);
    box.appendChild(narrationCard);

    narrationEl = narration;
    narrationBuffer.forEach(function (text) {
      narration.appendChild(el("div", null, text));
    });
    narration.scrollTop = narration.scrollHeight;
    autoscrollInput.addEventListener("change", function () {
      autoScroll = autoscrollInput.checked;
    });
  }

  // ---- keyboard shortcuts (S4, P1-FR-19) -------------------------------------------

  function shortcutEnabled() {
    var active = document.activeElement;
    var tag = active ? active.tagName : "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      return false;
    }
    if (!modal.hidden) return false;  // a consent window owns the keyboard
    return true;
  }

  var shortcutHelp = null;

  function toggleShortcutHelp() {
    if (!shortcutHelp) {
      shortcutHelp = el("div", "shortcuts-panel");
      shortcutHelp.appendChild(el("h3", null, "Review shortcuts"));
      var facts = el("dl", "facts");
      var pairs = [
        ["j", "next entry"],
        ["k", "previous entry"],
        ["a", "accept the shown entry"],
        ["r", "reject (focuses the reason)"],
        ["?", "toggle this help"]
      ];
      pairs.forEach(function (pair) {
        var key = el("dt", null);
        var kbd = el("kbd", null, pair[0]);
        key.appendChild(kbd);
        facts.appendChild(key);
        facts.appendChild(el("dd", null, pair[1]));
      });
      shortcutHelp.appendChild(facts);
      para(shortcutHelp, "Shortcuts are ignored while typing in a field "
        + "or while the authorization window is open.", "muted small");
      document.body.appendChild(shortcutHelp);
    }
    shortcutHelp.hidden = !shortcutHelp.hidden;
  }

  document.addEventListener("keydown", function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "Escape") {
      if (shortcutHelp && !shortcutHelp.hidden) shortcutHelp.hidden = true;
      return;
    }
    if (event.key === "?") {
      if (shortcutEnabled()) toggleShortcutHelp();
      return;
    }
    if (!reviewState || !shortcutEnabled()) return;
    if (event.key === "j") {
      if (reviewState.next) reviewState.next();
    } else if (event.key === "k") {
      if (reviewState.prev) reviewState.prev();
    } else if (event.key === "a") {
      if (reviewState.acceptCurrent) reviewState.acceptCurrent();
    } else if (event.key === "r") {
      if (reviewState.rejectCurrent) reviewState.rejectCurrent();
    } else {
      return;
    }
    event.preventDefault();
  });

  // ---- router ---------------------------------------------------------------------------

  var ROUTES = [
    { pattern: /^\/$/, render: renderHome, title: "Home" },
    { pattern: /^\/collections$/, render: renderCollections, title: "Collections" },
    { pattern: /^\/collections\/([^/]+)$/, render: renderCollectionDetail, title: "Collections" },
    { pattern: /^\/runs$/, render: renderRuns, title: "Runs" },
    { pattern: /^\/runs\/([^/]+)\/([^/]+)$/, render: renderRunDetail, title: "Runs" },
    { pattern: /^\/blocks$/, render: renderBlocks, title: "Blocks" },
    { pattern: /^\/evidence$/, render: renderEvidence, title: "Evidence" },
    { pattern: /^\/evidence\/([^/]+)$/, render: renderEvidence, title: "Evidence" },
    { pattern: /^\/review$/, render: renderReview, title: "Review" },
    { pattern: /^\/review\/([^/]+)$/, render: renderReview, title: "Review" },
    { pattern: /^\/run$/, render: renderRun, title: "Run" }
  ];

  var navLinks = Array.prototype.slice.call(
    document.querySelectorAll("#nav a")
  );

  function currentPath() {
    return (location.hash || "#/").replace(/^#/, "");
  }

  function route() {
    reviewState = null;
    narrationEl = null;
    var path = currentPath();
    var matched = null;
    ROUTES.forEach(function (entry) {
      var match = path.match(entry.pattern);
      if (match) matched = { render: entry.render, match: match, title: entry.title };
    });
    navLinks.forEach(function (anchor) {
      var hrefPath = (anchor.getAttribute("href") || "").replace(/^#/, "");
      var isCurrent = path === hrefPath
        || (hrefPath !== "/" && path.indexOf(hrefPath + "/") === 0);
      if (isCurrent) {
        anchor.setAttribute("aria-current", "page");
      } else {
        anchor.removeAttribute("aria-current");
      }
    });
    if (!matched) {
      view.textContent = "";
      var note = para(view, "Unknown view: " + path, "error");
      note.setAttribute("role", "alert");
      document.getElementById("section-title").textContent = "Not found";
      return;
    }
    document.getElementById("section-title").textContent = matched.title;
    matched.render(view, matched.match);
  }

  window.addEventListener("hashchange", route);

  // ---- app shell: nav icons, mobile menu -------------------------------------------------

  function injectNavIcons() {
    var brandMark = document.getElementById("brand-mark");
    if (brandMark) brandMark.appendChild(svgIcon("brand", 22));
    navLinks.forEach(function (anchor) {
      var name = anchor.getAttribute("data-icon");
      if (name && anchor.firstChild) {
        anchor.insertBefore(svgIcon(name), anchor.firstChild);
      }
    });
  }

  var appShell = document.getElementById("app");
  var menuToggle = document.getElementById("menu-toggle");

  function setSidebar(open) {
    if (open) {
      appShell.classList.add("sidebar-open");
    } else {
      appShell.classList.remove("sidebar-open");
    }
    menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  menuToggle.addEventListener("click", function () {
    setSidebar(!appShell.classList.contains("sidebar-open"));
  });

  navLinks.forEach(function (anchor) {
    anchor.addEventListener("click", function () {
      setSidebar(false);
    });
  });

  // ---- boot -------------------------------------------------------------------------------

  getJSON("health").then(function (data) {
    if (data && data.ok) {
      setPill(enginePill, "engine ok · v" + data.version, "ok");
    } else {
      setPill(enginePill, "engine unreachable", "bad");
    }
    return null;
  }).catch(function () {
    setPill(enginePill, "engine unreachable", "bad");
    return null;
  });

  injectNavIcons();
  route();
  refreshActive();
})();
