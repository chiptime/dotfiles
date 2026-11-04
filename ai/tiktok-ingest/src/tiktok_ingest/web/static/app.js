// tiktok-ingest Phase 1 web client (T5: S6 consent modal + S7 views).
//
// Vanilla JS, hash routing, no frameworks. Rules pinned by
// tests/test_web_static.py:
// - Rendering uses createElement/textContent only; server data never
//   becomes markup.
// - ONE request wrapper performs HTTP; only postJSON mutates state and
//   it always attaches the per-start token header. The token comes from
//   the meta tag the server injects and is never displayed or logged.
// - Every API path is a quoted literal in PATHS so the tests can prove
//   each one resolves on the real server.

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
    if (text !== undefined && text !== null) node.textContent = String(text);
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

  function addFact(list, term, value) {
    list.appendChild(el("dt", null, term));
    list.appendChild(el("dd", null, value));
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

  // ---- view plumbing -----------------------------------------------------

  var view = document.getElementById("view");

  function resetView(title) {
    view.textContent = "";
    view.appendChild(el("h2", null, title));
    var status = el("p", "status", "Loading...");
    status.setAttribute("role", "status");
    view.appendChild(status);
    return status;
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

  // ---- Collections view (P1-FR-04) ---------------------------------------

  function renderCollections(root) {
    var status = resetView("Collections");
    getJSON("collections").then(function (data) {
      var entries = asList(data.collections);
      clearStatus(status);
      if (!entries.length) {
        status.textContent = "No collections recorded yet.";
        return;
      }
      var t = newTable([
        "Collection", "Kind", "Declared", "Observed", "Status",
        "Status reason", "End-of-list evidence", "Access markers",
        "Plan counts"
      ]);
      entries.forEach(function (entry) {
        var row = el("tr");
        var name = el("td");
        name.appendChild(link(
          "#/collections/" + encodeURIComponent(entry.key),
          entry.key + (entry.author ? " (" + entry.author + ")" : "")
        ));
        row.appendChild(name);
        addCell(row, entry.kind);
        addCell(row, entry.declared_count == null ? "unknown" : entry.declared_count);
        // Observed is an inventory fact; it is never labelled "processed".
        addCell(row, entry.observed_count);
        addCell(row, entry.status);
        addCell(row, entry.status_reason);
        addCell(row, entry.end_of_list_evidence || "none recorded");
        addCell(row, asList(entry.access_markers).join(", ") || "none");
        var counts = (entry.plan && entry.plan.counts) || {};
        addCell(row, Object.keys(counts).map(function (key) {
          return key + ": " + counts[key];
        }).join(", ") || "n/a");
        t.body.appendChild(row);
      });
      view.appendChild(t.table);
    }).catch(function (err) { fail(status, err); });
  }

  function renderCollectionDetail(root, match) {
    var key = decodeURIComponent(match[1]);
    var status = resetView("Collection " + key);
    view.insertBefore(link("#/collections", "Back to collections"), status);
    getJSON("collection", { key: key }).then(function (entry) {
      clearStatus(status);
      var facts = el("dl", "facts");
      addFact(facts, "URL", entry.url);
      addFact(facts, "Author", entry.author || "n/a");
      addFact(facts, "Kind", entry.kind);
      addFact(facts, "Declared count", entry.declared_count == null ? "unknown" : entry.declared_count);
      addFact(facts, "Observed count", entry.observed_count);
      addFact(facts, "Status", entry.status);
      addFact(facts, "Status reason", entry.status_reason);
      addFact(facts, "End-of-list evidence", entry.end_of_list_evidence || "none recorded");
      addFact(facts, "Access markers", asList(entry.access_markers).join(", ") || "none");
      if (entry.plan) {
        addFact(facts, "Plan scan status", entry.plan.scan_status);
        addFact(facts, "Plan scan reason", entry.plan.scan_status_reason || "n/a");
      }
      view.appendChild(facts);

      view.appendChild(el("h3", null, "Items (with plan classification)"));
      var items = asList(entry.items);
      if (!items.length) {
        para(view, "No items observed.");
        return;
      }
      var t = newTable(["ID", "Kind", "URL", "Classification", "Detail"]);
      items.forEach(function (item) {
        var row = el("tr");
        var idCell = el("td");
        idCell.appendChild(link("#/evidence/" + encodeURIComponent(item.id), item.id));
        row.appendChild(idCell);
        addCell(row, item.kind);
        addCell(row, item.url);
        addCell(row, item.classification);
        addCell(row, item.detail || "");
        t.body.appendChild(row);
      });
      view.appendChild(t.table);
    }).catch(function (err) { fail(status, err); });
  }

  // ---- Runs view (P1-FR-05) ----------------------------------------------

  function renderRuns(root) {
    var status = resetView("Saved runs");
    getJSON("runs").then(function (data) {
      var entries = asList(data.runs);
      clearStatus(status);
      if (!entries.length) {
        status.textContent = "No saved run reports yet.";
        return;
      }
      var t = newTable([
        "Generated at", "Collection", "Report", "Complete", "Pending", "Other"
      ]);
      entries.forEach(function (entry) {
        var row = el("tr");
        addCell(row, entry.generated_at || "n/a");
        addCell(row, entry.collection_key);
        var report = el("td");
        report.appendChild(link(
          "#/runs/" + encodeURIComponent(entry.collection_key)
            + "/" + encodeURIComponent(entry.report),
          entry.report
        ));
        row.appendChild(report);
        var counts = entry.counts || {};
        addCell(row, counts.complete);
        addCell(row, counts.pending);
        addCell(row, counts.other);
        t.body.appendChild(row);
      });
      view.appendChild(t.table);
      para(view, "Newest first.", "status");
    }).catch(function (err) { fail(status, err); });
  }

  function renderRunDetail(root, match) {
    var key = decodeURIComponent(match[1]);
    var report = decodeURIComponent(match[2]);
    var status = resetView("Run " + report);
    view.insertBefore(link("#/runs", "Back to saved runs"), status);
    getJSON("runs").then(function (data) {
      var entry = asList(data.runs).filter(function (candidate) {
        return candidate.collection_key === key && candidate.report === report;
      })[0];
      if (!entry) throw new Error("run report not found in the saved runs");
      clearStatus(status);
      var counts = entry.counts || {};
      para(view, "Collection: " + key + " - generated at "
        + (entry.generated_at || "n/a") + " - counts: "
        + counts.complete + " complete, " + counts.pending
        + " pending, " + counts.other + " other.");
      var raw = link(endpoint("runReport", { key: key, report: report }),
        "Raw report JSON");
      raw.className = "back-link";
      view.appendChild(raw);

      view.appendChild(el("h3", null, "Items"));
      var items = asList(entry.items);
      if (!items.length) {
        para(view, "No items in this report.");
        return;
      }
      var t = newTable(["ID", "Author", "Stages", "Next"]);
      items.forEach(function (item) {
        var row = el("tr");
        addCell(row, item.id);
        addCell(row, item.author || "");
        var stages = el("td");
        Object.keys(item.stages || {}).forEach(function (stage) {
          stages.appendChild(el("div", null, stage + ": " + item.stages[stage]));
        });
        row.appendChild(stages);
        addCell(row, item.next);
        t.body.appendChild(row);
      });
      view.appendChild(t.table);
    }).catch(function (err) { fail(status, err); });
  }

  // ---- Blocks view (P1-FR-06) ---------------------------------------------

  function renderBlocks(root) {
    var status = resetView("Blocks");
    getJSON("blocks").then(function (data) {
      clearStatus(status);

      view.appendChild(el("h3", null, "Item blocks"));
      var items = asList(data.items);
      if (!items.length) {
        para(view, "No blocked items.");
      } else {
        var t = newTable(["ID", "Blocked stages", "Next (resume line)"]);
        items.forEach(function (item) {
          var row = el("tr");
          var idCell = el("td");
          idCell.appendChild(link("#/evidence/" + encodeURIComponent(item.id), item.id));
          row.appendChild(idCell);
          var stages = el("td");
          Object.keys(item.blocked_stages || {}).forEach(function (stage) {
            stages.appendChild(el("div", null,
              stage + ": " + item.blocked_stages[stage]));
          });
          row.appendChild(stages);
          addCell(row, item.next);
          t.body.appendChild(row);
        });
        view.appendChild(t.table);
      }

      view.appendChild(el("h3", null, "Run-level blocks"));
      var runs = asList(data.runs);
      if (!runs.length) {
        para(view, "No run-level blocks.");
        return;
      }
      runs.forEach(function (run) {
        view.appendChild(el("h4", null, run.collection_key + " - " + run.report));
        if (run.hard_stop_reason) {
          para(view, "Hard stop: " + run.hard_stop_reason, "error");
        }
        if (run.failures) {
          view.appendChild(el("p", null, "Failures:"));
          view.appendChild(el("pre", "verbatim", jsonText(run.failures)));
        }
        if (run.cleanup_notes) {
          view.appendChild(el("p", null, "Cleanup notes:"));
          var notes = el("ul");
          asList(run.cleanup_notes).forEach(function (note) {
            notes.appendChild(el("li", null, note));
          });
          view.appendChild(notes);
        }
      });
    }).catch(function (err) { fail(status, err); });
  }

  // ---- Evidence view (P1-FR-07) ---------------------------------------------

  function renderEvidence(root, match) {
    resetView("Evidence");
    var initialId = match ? decodeURIComponent(match[1]) : "";

    var form = el("form");
    var field = el("div", "field");
    var label = el("label", null, "Video ID");
    label.setAttribute("for", "evidence-id");
    var input = el("input");
    input.id = "evidence-id";
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    field.appendChild(label);
    field.appendChild(input);
    form.appendChild(field);
    var submit = el("button", null, "Look up");
    submit.type = "submit";
    form.appendChild(submit);
    view.appendChild(form);

    var result = el("div");
    view.appendChild(result);

    var artifactPre = null;

    function showArtifact(videoId, name) {
      if (!artifactPre) {
        artifactPre = el("pre", "verbatim");
        artifactPre.setAttribute("role", "region");
        artifactPre.setAttribute("aria-label", "Artifact content");
        result.appendChild(artifactPre);
      }
      artifactPre.textContent = "Loading " + name + "...";
      return getText("videoEvidence", { video_id: videoId, name: name })
        .then(function (text) { artifactPre.textContent = text; })
        .catch(function (err) { artifactPre.textContent = err.message; });
    }

    function lookup(videoId) {
      result.textContent = "";
      artifactPre = null;
      var status = para(result, "Looking up " + videoId + "...", "status");
      status.setAttribute("role", "status");
      getJSON("video", { video_id: videoId }).then(function (payload) {
        status.textContent = "";
        asList(payload.items).forEach(function (item) {
          result.appendChild(el("h3", null, item.video_id));
          var facts = el("dl", "facts");
          addFact(facts, "Processed", item.processed ? "yes" : "no");
          addFact(facts, "Blocklisted", item.blocklisted ? "yes" : "no");
          addFact(facts, "Backlog", item.backlog && item.backlog.emitted
            ? "emitted (" + (item.backlog.status || "n/a") + ")"
            : "not emitted");
          if (item.completed_at) addFact(facts, "Completed at", item.completed_at);
          if (item.stages) addFact(facts, "Stages", asList(item.stages).join(", "));
          if (item.stage_outcomes) {
            addFact(facts, "Stage outcomes", Object.keys(item.stage_outcomes)
              .map(function (stage) {
                return stage + ": " + item.stage_outcomes[stage];
              }).join(", "));
          }
          if (item.extraction_outcome != null) {
            addFact(facts, "Extraction outcome", item.extraction_outcome);
          }
          result.appendChild(facts);

          var heading = el("h4", null, "Evidence artifacts");
          result.appendChild(heading);
          var names = asList(item.evidence);
          if (!names.length) {
            para(result, "No evidence artifacts recorded.");
            return;
          }
          var actions = el("div", "actions-row");
          names.forEach(function (name) {
            var button = el("button", null, name);
            button.type = "button";
            button.addEventListener("click", function () {
              showArtifact(item.video_id, name);
            });
            actions.appendChild(button);
          });
          result.appendChild(actions);
        });
      }).catch(function (err) { fail(status, err); });
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

  // ---- Review view (P1-FR-08, P1-FR-11, P1-FR-12) -----------------------------

  var REJECT_REASON_RULE =
    "a rejected decision requires a reason (it becomes the permanent " +
    "blocklist reason)";

  function renderReview(root) {
    var status = resetView("Pending review");
    getJSON("backlog", null, { status: "pending" }).then(function (data) {
      var entries = asList(data.entries);
      clearStatus(status);
      if (!entries.length) {
        status.textContent = "No pending backlog entries.";
      } else {
        var t = newTable(["ID", "Author", "URL", "Ingested at", ""]);
        entries.forEach(function (entry) {
          var row = el("tr");
          addCell(row, entry.id);
          addCell(row, entry.author || "");
          addCell(row, entry.url || "");
          addCell(row, entry.ingested_at || "");
          var actions = el("td");
          actions.appendChild(link(
            "#/review/" + encodeURIComponent(entry.id), "Open detail"
          ));
          row.appendChild(actions);
          t.body.appendChild(row);
        });
        view.appendChild(t.table);
        // One explicit decision per entry only; there is intentionally
        // no bulk action (P1-FR-12).
      }
      renderPlanSection(view);
    }).catch(function (err) { fail(status, err); });
  }

  function renderPlanSection(parent) {
    parent.appendChild(el("h3", null, "Apply decisions"));
    para(parent,
      "Preview the plan built from the recorded decisions, then apply it. " +
      "Applying revalidates the plan and writes it with backups and a " +
      "receipt, exactly like the review CLI.");

    var area = el("div");
    parent.appendChild(area);
    var previewButton = el("button", null, "Preview plan");
    previewButton.type = "button";
    area.appendChild(previewButton);

    function showPlan(data) {
      area.textContent = "";
      area.appendChild(el("p", null, "Plan preview (nothing written yet):"));
      area.appendChild(el("pre", "verbatim", jsonText(data.plan)));
      var hash = para(area, "plan_sha256: " + data.plan_sha256, "mono");
      hash.className = "mono";
      var applyButton = el("button", null, "Apply this plan");
      applyButton.type = "button";
      area.appendChild(applyButton);

      applyButton.addEventListener("click", function () {
        // Explicit confirm step before anything is written.
        area.appendChild(el("p", null,
          "Confirm: apply this plan? The changes listed above will be " +
          "written now.", "error"));
        var confirmButton = el("button", null, "Confirm apply");
        confirmButton.type = "button";
        var cancelButton = el("button", null, "Do not apply");
        cancelButton.type = "button";
        var actions = el("div", "actions-row");
        actions.appendChild(confirmButton);
        actions.appendChild(cancelButton);
        area.appendChild(actions);
        applyButton.disabled = true;

        cancelButton.addEventListener("click", function () {
          applyButton.disabled = false;
          confirmButton.remove();
          cancelButton.remove();
          actions.remove();
        });

        confirmButton.addEventListener("click", function () {
          confirmButton.disabled = true;
          cancelButton.disabled = true;
          postJSON("backlogApply", { plan_sha256: data.plan_sha256 })
            .then(function (receipt) {
              area.textContent = "";
              para(area, "Apply finished. Receipt:", "ok-note");
              area.appendChild(el("pre", "verbatim", jsonText(receipt)));
              var files = asList(receipt.files_written).length;
              var backups = asList(receipt.backups).length;
              para(area, "Files written: " + files + ", backups: " + backups
                + ", status: " + (receipt.status || "n/a") + ".");
            })
            .catch(function (err) {
              confirmButton.disabled = false;
              cancelButton.disabled = false;
              var note = el("p", "error", err.message);
              note.setAttribute("role", "alert");
              area.appendChild(note);
            });
        });
      });
    }

    previewButton.addEventListener("click", function () {
      previewButton.disabled = true;
      getJSON("backlogPlan").then(function (data) {
        showPlan(data);
      }).catch(function (err) {
        previewButton.disabled = false;
        area.textContent = "";
        var note = el("p", "error", err.message);
        note.setAttribute("role", "alert");
        area.appendChild(note);
      });
    });
  }

  function renderReviewDetail(root, match) {
    var videoId = decodeURIComponent(match[1]);
    var status = resetView("Backlog entry " + videoId);
    view.insertBefore(link("#/review", "Back to pending review"), status);
    getJSON("backlogEntry", { video_id: videoId }).then(function (doc) {
      clearStatus(status);

      view.appendChild(el("h3", null, "Entry"));
      var facts = el("dl", "facts");
      var entry = doc.entry || {};
      Object.keys(entry).forEach(function (key) {
        var value = entry[key];
        if (value !== null && typeof value === "object") return;
        addFact(facts, key, value === null || value === undefined ? "n/a" : value);
      });
      view.appendChild(facts);
      Object.keys(entry).forEach(function (key) {
        var value = entry[key];
        if (value === null || typeof value !== "object") return;
        view.appendChild(el("p", null, key + ":"));
        view.appendChild(el("pre", "verbatim", jsonText(value)));
      });

      view.appendChild(el("h3", null, "Verification"));
      if (doc.verification) {
        para(view, "Rules version: " + (doc.verification.rules_version || "n/a")
          + " - generated at " + (doc.verification.generated_at || "n/a") + ".");
        var claims = asList(doc.verification.claims);
        if (!claims.length) {
          para(view, "No claims recorded.");
        } else {
          var t = newTable(["Claim", "Verdict", "Reason", "Evidence", "Note"]);
          claims.forEach(function (claim) {
            var row = el("tr");
            addCell(row, claim.claim || "");
            addCell(row, claim.verdict || "");
            addCell(row, claim.reason || "");
            addCell(row, claim.evidence === null || claim.evidence === undefined
              ? "" : typeof claim.evidence === "object"
                ? jsonText(claim.evidence) : claim.evidence);
            addCell(row, claim.note || "");
            t.body.appendChild(row);
          });
          view.appendChild(t.table);
        }
      } else {
        para(view, doc.verification_note || "No verification results.");
      }

      view.appendChild(el("h3", null, "Decision"));
      para(view,
        "The decision binds this exact entry hash; a changed entry aborts " +
        "with zero writes.");
      var hashNote = para(view, "Entry hash: " + doc.entry_sha256);
      hashNote.className = "mono";

      var reasonField = el("div", "field");
      var reasonLabel = el("label", null, "Reason (required for reject)");
      reasonLabel.setAttribute("for", "reject-reason");
      var reasonInput = el("textarea");
      reasonInput.id = "reject-reason";
      reasonField.appendChild(reasonLabel);
      reasonField.appendChild(reasonInput);
      view.appendChild(reasonField);

      var message = el("p");
      message.setAttribute("role", "alert");
      view.appendChild(message);
      var actions = el("div", "actions-row");
      var acceptButton = el("button", null, "Accept");
      acceptButton.type = "button";
      var rejectButton = el("button", null, "Reject");
      rejectButton.type = "button";
      actions.appendChild(acceptButton);
      actions.appendChild(rejectButton);
      view.appendChild(actions);

      function decide(payload) {
        acceptButton.disabled = true;
        rejectButton.disabled = true;
        message.className = "";
        message.textContent = "";
        return postJSON("backlogDecision", payload, { video_id: videoId })
          .then(function () {
            message.className = "ok-note";
            message.textContent = "Decision recorded. Preview and apply it " +
              "from the Review view.";
            return null;
          })
          .catch(function (err) {
            acceptButton.disabled = false;
            rejectButton.disabled = false;
            message.className = "error";
            message.textContent = err.message;
            return null;
          });
      }

      acceptButton.addEventListener("click", function () {
        decide({ decision: "accepted", entry_sha256: doc.entry_sha256 });
      });

      rejectButton.addEventListener("click", function () {
        var reason = reasonInput.value.trim();
        // Mirror of the server rule: reject without a reason is refused.
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
    }).catch(function (err) { fail(status, err); });
  }

  // ---- Consent modal (S6, P1-FR-09, P1-FR-19) ---------------------------------

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

  // ---- Run panel (S5 + S8, P1-FR-13) --------------------------------------------

  var runState = document.getElementById("run-state");
  var runForm = document.getElementById("run-form");
  var urlInput = document.getElementById("run-url");
  var limitInput = document.getElementById("run-limit");
  var dryRunInput = document.getElementById("opt-dry-run");
  var noAutoVerifyInput = document.getElementById("opt-no-auto-verify");
  var rescanInput = document.getElementById("opt-rescan");
  var runError = document.getElementById("run-error");
  var activeSection = document.getElementById("active-run");
  var activeMeta = document.getElementById("active-run-meta");
  var cancelButton = document.getElementById("cancel-run");
  var narrationSection = document.getElementById("narration-section");
  var narration = document.getElementById("narration");
  var events = null;

  function showRunError(message) {
    runError.hidden = false;
    runError.textContent = message;
  }

  runForm.addEventListener("submit", function (event) {
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
    if (dryRunInput.checked) payload.dry_run = true;
    if (noAutoVerifyInput.checked) payload.no_auto_verify = true;
    if (rescanInput.checked) payload.rescan = true;
    postJSON("runs", payload).then(function () {
      narration.textContent = "";
      appendLine("run started: " + payload.url);
      urlInput.value = "";
      refreshActive();
      return null;
    }).catch(function (err) {
      showRunError(err.message);
      return null;
    });
  });

  cancelButton.addEventListener("click", function () {
    cancelButton.disabled = true;
    postJSON("cancel", {}).then(function (data) {
      cancelButton.disabled = false;
      appendLine(data && data.joined
        ? "cancel: run stopped at a safe point"
        : "cancel requested; the run stops at its next safe point");
      refreshActive();
      return null;
    }).catch(function (err) {
      cancelButton.disabled = false;
      showRunError(err.message);
      return null;
    });
  });

  function appendLine(text) {
    var line = el("div", null, text);
    narration.appendChild(line);
    if (narration.childElementCount > 2000) {
      narration.removeChild(narration.firstChild);
    }
    narrationSection.hidden = false;
    narration.scrollTop = narration.scrollHeight;
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

  function refreshActive() {
    return getJSON("activeRun").then(function (data) {
      runState.textContent = "Launching from the web: "
        + (data.allow_runs ? "enabled" : "disabled")
        + ". Run state: " + data.state + ".";
      if (data.state === "running") {
        activeSection.hidden = false;
        var run = data.run || {};
        activeMeta.textContent = [run.url, run.kind, run.started_at]
          .filter(Boolean).join(" - ");
        connectEvents();
        if (data.pending_prompt && data.pending_prompt.id) {
          showModal(data.pending_prompt);
        } else if (currentPrompt) {
          hideModal();
        }
      } else {
        activeSection.hidden = true;
        stopEvents();
        hideModal();
      }
      return null;
    }).catch(function (err) {
      runState.textContent = "run state unavailable: " + err.message;
      return null;
    });
  }

  // ---- Router --------------------------------------------------------------------

  var ROUTES = [
    { pattern: /^\/collections$/, render: renderCollections },
    { pattern: /^\/collections\/([^/]+)$/, render: renderCollectionDetail },
    { pattern: /^\/runs$/, render: renderRuns },
    { pattern: /^\/runs\/([^/]+)\/([^/]+)$/, render: renderRunDetail },
    { pattern: /^\/blocks$/, render: renderBlocks },
    { pattern: /^\/evidence$/, render: renderEvidence },
    { pattern: /^\/evidence\/([^/]+)$/, render: renderEvidence },
    { pattern: /^\/review$/, render: renderReview },
    { pattern: /^\/review\/([^/]+)$/, render: renderReviewDetail }
  ];

  var navLinks = Array.prototype.slice.call(
    document.querySelectorAll("#nav a")
  );

  function route() {
    var path = (location.hash || "#/collections").replace(/^#/, "");
    var matched = null;
    ROUTES.forEach(function (entry) {
      var match = path.match(entry.pattern);
      if (match) matched = { render: entry.render, match: match };
    });
    navLinks.forEach(function (anchor) {
      if (anchor.getAttribute("href") === "#" + path) {
        anchor.setAttribute("aria-current", "page");
      } else {
        anchor.removeAttribute("aria-current");
      }
    });
    if (!matched) {
      view.textContent = "";
      var note = para(view, "Unknown view: " + path, "error");
      note.setAttribute("role", "alert");
      return;
    }
    matched.render(view, matched.match);
  }

  window.addEventListener("hashchange", route);

  // ---- Boot -------------------------------------------------------------------------

  getJSON("health").then(function (data) {
    var health = document.getElementById("health");
    health.textContent = data && data.ok
      ? "engine ok (v" + data.version + ")"
      : "engine unreachable";
    return null;
  }).catch(function () {
    document.getElementById("health").textContent = "engine unreachable";
    return null;
  });

  route();
  refreshActive();
})();
