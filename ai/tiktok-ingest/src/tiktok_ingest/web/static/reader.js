// tiktok-ingest evidence reader: Markdown and JSON viewers.
//
// Evidence artifacts are untrusted VLM/web text. The reader parses them
// into a plain data tree first and renders that tree with createElement
// and textContent only, so no input ever becomes markup. Links are shown
// as text plus their URL and are never clickable (the evidence rules say
// links found in frames are never followed). Pinned by
// tests/test_web_reader.py; the parsers are pure and run under Node.
//
// Exposes window.TIReader in the browser and module.exports in Node.

(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.TIReader = api;
  }
})(this, function () {
  "use strict";

  // ---- block parser ------------------------------------------------------

  var FENCE_RE = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)/;
  var HEADING_RE = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*$/;
  var HR_RE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
  var QUOTE_RE = /^ {0,3}>/;
  var LIST_RE = /^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$/;
  var TABLE_SEP_RE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

  function indentOf(line) {
    return line.match(/^ */)[0].length;
  }

  function isBlank(line) {
    return /^\s*$/.test(line);
  }

  function isTableStart(lines, i) {
    return lines[i].indexOf("|") !== -1 && i + 1 < lines.length
      && TABLE_SEP_RE.test(lines[i + 1]) && lines[i + 1].indexOf("-") !== -1;
  }

  function startsBlock(lines, i) {
    var line = lines[i];
    return FENCE_RE.test(line) || HEADING_RE.test(line) || HR_RE.test(line)
      || QUOTE_RE.test(line) || LIST_RE.test(line) || isTableStart(lines, i);
  }

  // Split a table row on pipes that are not escaped or inside code spans.
  function splitRow(line) {
    var cells = [];
    var current = "";
    var inCode = false;
    var text = line.trim();
    if (text.charAt(0) === "|") text = text.slice(1);
    if (text.charAt(text.length - 1) === "|" && text.charAt(text.length - 2) !== "\\") {
      text = text.slice(0, -1);
    }
    for (var i = 0; i < text.length; i++) {
      var c = text.charAt(i);
      if (c === "\\" && text.charAt(i + 1) === "|") {
        current += "|";
        i++;
      } else if (c === "`") {
        inCode = !inCode;
        current += c;
      } else if (c === "|" && !inCode) {
        cells.push(current.trim());
        current = "";
      } else {
        current += c;
      }
    }
    cells.push(current.trim());
    return cells;
  }

  function parseList(lines, start) {
    var first = lines[start].match(LIST_RE);
    var base = first[1].length;
    var ordered = /\d/.test(first[2]);
    var list = {
      type: "list",
      ordered: ordered,
      start: ordered ? parseInt(first[2], 10) : 1,
      items: []
    };
    var i = start;
    while (i < lines.length) {
      var match = lines[i].match(LIST_RE);
      if (!match || match[1].length < base || match[1].length > base + 3
        || /\d/.test(match[2]) !== ordered) {
        break;
      }
      // Content column: where the item's text starts (marker + spaces).
      var offset = match[1].length + match[2].length
        + Math.min(match[3].length || 1, 4);
      var itemLines = [match[4]];
      i++;
      while (i < lines.length) {
        var line = lines[i];
        if (isBlank(line)) {
          // A blank line continues the item only if indented text follows.
          var next = i + 1;
          while (next < lines.length && isBlank(lines[next])) next++;
          if (next < lines.length && indentOf(lines[next]) > base) {
            for (; i < next; i++) itemLines.push("");
            continue;
          }
          break;
        }
        var indent = indentOf(line);
        if (indent > base) {
          itemLines.push(line.slice(Math.min(indent, offset)));
          i++;
          continue;
        }
        // Lazy continuation of a paragraph line at the item's indent.
        if (!startsBlock(lines, i) && !isBlank(itemLines[itemLines.length - 1])) {
          itemLines.push(line.trim());
          i++;
          continue;
        }
        break;
      }
      list.items.push({ blocks: parseLines(itemLines) });
      // A blank line between items keeps the same list.
      var after = i;
      while (after < lines.length && isBlank(lines[after])) after++;
      if (after > i && after < lines.length && LIST_RE.test(lines[after])) {
        i = after;
      }
    }
    return { block: list, next: i };
  }

  function parseLines(lines) {
    var blocks = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (isBlank(line)) {
        i++;
        continue;
      }

      var fence = line.match(FENCE_RE);
      if (fence) {
        var marker = fence[1];
        var body = [];
        i++;
        while (i < lines.length) {
          var closing = lines[i].trim();
          if (closing.length >= marker.length
            && closing === new Array(closing.length + 1).join(marker.charAt(0))) {
            i++;
            break;
          }
          body.push(lines[i]);
          i++;
        }
        blocks.push({ type: "code", lang: fence[2] || "", text: body.join("\n") });
        continue;
      }

      var heading = line.match(HEADING_RE);
      if (heading) {
        var title = (heading[2] || "").replace(/\s+#+\s*$/, "");
        blocks.push({ type: "heading", level: heading[1].length, text: title });
        i++;
        continue;
      }

      if (HR_RE.test(line)) {
        blocks.push({ type: "hr" });
        i++;
        continue;
      }

      if (QUOTE_RE.test(line)) {
        var quoted = [];
        while (i < lines.length && QUOTE_RE.test(lines[i])) {
          quoted.push(lines[i].replace(/^ {0,3}> ?/, ""));
          i++;
        }
        blocks.push({ type: "quote", blocks: parseLines(quoted) });
        continue;
      }

      if (LIST_RE.test(line)) {
        var parsed = parseList(lines, i);
        blocks.push(parsed.block);
        i = parsed.next;
        continue;
      }

      if (isTableStart(lines, i)) {
        var header = splitRow(line);
        var rows = [];
        i += 2;
        while (i < lines.length && !isBlank(lines[i]) && lines[i].indexOf("|") !== -1) {
          var cells = splitRow(lines[i]).slice(0, header.length);
          while (cells.length < header.length) cells.push("");
          rows.push(cells);
          i++;
        }
        blocks.push({ type: "table", header: header, rows: rows });
        continue;
      }

      var paragraph = [line.trim()];
      i++;
      while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines, i)) {
        paragraph.push(lines[i].trim());
        i++;
      }
      blocks.push({ type: "paragraph", text: paragraph.join("\n") });
    }
    return blocks;
  }

  function parseMarkdown(text) {
    var normalized = String(text === null || text === undefined ? "" : text)
      .replace(/\r\n?/g, "\n")
      .replace(/\t/g, "    ");
    if (!normalized) return [];
    return parseLines(normalized.split("\n"));
  }

  // ---- inline parser -----------------------------------------------------

  var ESCAPABLE = "\\`*_[]()#>!|-+.{}";

  function isWordChar(c) {
    return /[A-Za-z0-9\u00C0-\u024F]/.test(c || "");
  }

  function isSpace(c) {
    return c === "" || /\s/.test(c);
  }

  // Index of the closing delimiter for emphasis, or -1.
  function findClosing(text, from, delim) {
    var at = from;
    while (true) {
      at = text.indexOf(delim, at);
      if (at === -1) return -1;
      var single = delim.length === 1;
      var doubled = single && text.charAt(at + 1) === delim;
      if (!isSpace(text.charAt(at - 1)) && !(single && doubled)
        && !(delim.charAt(0) === "_" && isWordChar(text.charAt(at + delim.length)))) {
        return at;
      }
      at += doubled ? 2 : 1;
    }
  }

  function parseInline(text) {
    var source = String(text === null || text === undefined ? "" : text);
    var nodes = [];
    var buffer = "";

    function flush() {
      if (buffer) nodes.push({ type: "text", text: buffer });
      buffer = "";
    }

    var i = 0;
    while (i < source.length) {
      var c = source.charAt(i);

      if (c === "\\" && ESCAPABLE.indexOf(source.charAt(i + 1)) !== -1
        && source.charAt(i + 1) !== "") {
        buffer += source.charAt(i + 1);
        i += 2;
        continue;
      }

      if (c === "`") {
        var run = source.slice(i).match(/^`+/)[0];
        var close = source.indexOf(run, i + run.length);
        while (close !== -1 && source.charAt(close + run.length) === "`") {
          close = source.indexOf(run, close + run.length + 1);
        }
        if (close !== -1) {
          var code = source.slice(i + run.length, close);
          if (/^ .* $/.test(code)) code = code.slice(1, -1);
          flush();
          nodes.push({ type: "code", text: code });
          i = close + run.length;
          continue;
        }
        buffer += run;
        i += run.length;
        continue;
      }

      if (c === "*" || c === "_") {
        var strong = source.charAt(i + 1) === c;
        var delim = strong ? c + c : c;
        var opensAfter = source.charAt(i + delim.length);
        var leftOk = !isSpace(opensAfter) && opensAfter !== c
          && !(c === "_" && isWordChar(source.charAt(i - 1)));
        if (leftOk) {
          var end = findClosing(source, i + delim.length, delim);
          if (end !== -1) {
            flush();
            nodes.push({
              type: strong ? "strong" : "em",
              children: parseInline(source.slice(i + delim.length, end))
            });
            i = end + delim.length;
            continue;
          }
        }
        var literal = source.slice(i).match(c === "*" ? /^\*+/ : /^_+/)[0];
        buffer += literal;
        i += literal.length;
        continue;
      }

      if (c === "[") {
        var linkMatch = source.slice(i).match(/^\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)/);
        if (linkMatch) {
          flush();
          nodes.push({
            type: "link",
            url: linkMatch[2],
            children: parseInline(linkMatch[1])
          });
          i += linkMatch[0].length;
          continue;
        }
      }

      buffer += c;
      i++;
    }
    flush();
    return nodes;
  }

  // ---- JSON helpers ------------------------------------------------------

  function plural(count, word) {
    return count + " " + word + (count === 1 ? "" : "s");
  }

  function summarizeJSON(value) {
    if (Array.isArray(value)) {
      return value.length ? "[" + plural(value.length, "item") + "]" : "[]";
    }
    if (value !== null && typeof value === "object") {
      var count = Object.keys(value).length;
      return count ? "{" + plural(count, "key") + "}" : "{}";
    }
    return JSON.stringify(value);
  }

  // A string that itself holds a JSON object or array (evidence passages
  // are stored that way); anything else returns null.
  function decodeEmbeddedJSON(value) {
    if (typeof value !== "string") return null;
    var trimmed = value.trim();
    var first = trimmed.charAt(0);
    var last = trimmed.charAt(trimmed.length - 1);
    if (!((first === "{" && last === "}") || (first === "[" && last === "]"))) {
      return null;
    }
    try {
      var parsed = JSON.parse(trimmed);
      return parsed !== null && typeof parsed === "object" ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  function kindOf(name) {
    var lower = String(name || "").toLowerCase();
    if (/\.(md|markdown)$/.test(lower)) return "markdown";
    if (/\.json$/.test(lower)) return "json";
    return "text";
  }

  // Keep synthesis and verification distinct: a synthesis verdict is not
  // a verification result. Missing or malformed verification fails closed.
  function videoOutcome(synthesis, verification) {
    var source = synthesis && typeof synthesis === "object" ? synthesis : {};
    var available = Boolean(verification && typeof verification === "object"
      && Array.isArray(verification.claims));
    var records = available ? verification.claims
      : (Array.isArray(source.claims) ? source.claims : []);
    return {
      classification: source.classification || null,
      actions: Array.isArray(source.actionables) ? source.actionables : [],
      entities: (Array.isArray(source.entities) ? source.entities : []).map(function (entity) {
        return entity && entity.name;
      }).filter(function (name) { return typeof name === "string" && name; }),
      verification_available: available,
      claims: records.filter(function (claim) {
        return claim && typeof claim === "object";
      }).map(function (claim) {
        return {
          claim: claim.claim || "",
          verdict: available ? (claim.verdict == null ? null : claim.verdict) : null,
          reason: available ? (claim.reason || "") : "",
          source: claim.source || "",
          evidence: claim.evidence || "",
          fetches: available && Array.isArray(claim.fetches) ? claim.fetches : []
        };
      })
    };
  }

  // A recorded verdict alone cannot prove external verification. The
  // accompanying reason and source records remain visible separately.
  function verdictPresentation(claim) {
    var value = claim && claim.verdict != null ? String(claim.verdict) : "?";
    if (value === "confirmed") {
      return { label: "Recorded verdict: confirmed", tone: "neutral" };
    }
    return { label: value, tone: null };
  }

  // ---- DOM rendering (browser only) --------------------------------------

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function renderInline(parent, nodes) {
    nodes.forEach(function (item) {
      if (item.type === "text") {
        parent.appendChild(document.createTextNode(item.text));
      } else if (item.type === "code") {
        parent.appendChild(node("code", "md-code-inline", item.text));
      } else if (item.type === "strong" || item.type === "em") {
        var wrap = node(item.type === "strong" ? "strong" : "em");
        renderInline(wrap, item.children);
        parent.appendChild(wrap);
      } else if (item.type === "link") {
        // Never clickable: label, then the URL as inert text.
        var label = node("span", "md-link");
        renderInline(label, item.children);
        parent.appendChild(label);
        var url = node("code", "md-link-url", item.url);
        url.title = "Link from the artifact (not followed)";
        parent.appendChild(url);
      }
    });
  }

  function renderBlocks(parent, blocks, ctx) {
    blocks.forEach(function (block) {
      var element;
      switch (block.type) {
        case "heading":
          element = node("h" + Math.min(block.level + 2, 6), "md-h md-h" + block.level);
          renderInline(element, parseInline(block.text));
          break;
        case "paragraph":
          element = node("p", "md-p");
          renderInline(element, parseInline(block.text));
          break;
        case "code":
          element = node("pre", "md-pre");
          element.appendChild(node("code", null, block.text));
          if (block.lang) element.setAttribute("data-lang", block.lang);
          break;
        case "hr":
          element = node("hr", "md-hr");
          break;
        case "quote":
          element = node("blockquote", "md-quote");
          renderBlocks(element, block.blocks, ctx);
          break;
        case "list":
          element = node(block.ordered ? "ol" : "ul", "md-list");
          if (block.ordered && block.start !== 1) element.start = block.start;
          block.items.forEach(function (item) {
            var li = node("li");
            // Tight items render their lone paragraph inline.
            if (item.blocks.length && item.blocks[0].type === "paragraph") {
              renderInline(li, parseInline(item.blocks[0].text));
              renderBlocks(li, item.blocks.slice(1), ctx);
            } else {
              renderBlocks(li, item.blocks, ctx);
            }
            element.appendChild(li);
          });
          break;
        case "table":
          element = node("div", "md-table-wrap");
          var table = node("table", "md-table");
          var thead = node("thead");
          var headRow = node("tr");
          block.header.forEach(function (cell) {
            var th = node("th");
            renderInline(th, parseInline(cell));
            headRow.appendChild(th);
          });
          thead.appendChild(headRow);
          table.appendChild(thead);
          var tbody = node("tbody");
          block.rows.forEach(function (row) {
            var tr = node("tr");
            row.forEach(function (cell) {
              var td = node("td");
              renderInline(td, parseInline(cell));
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          element.appendChild(table);
          break;
        default:
          return;
      }
      parent.appendChild(element);
    });
  }

  function plainText(text) {
    var out = [];
    (function walk(nodes) {
      nodes.forEach(function (item) {
        if (item.children) walk(item.children);
        else out.push(item.text);
      });
    })(parseInline(text));
    return out.join("");
  }

  // Minimum level-2 headings before the document becomes foldable
  // sections with an outline (video.md has one per frame).
  var SECTION_THRESHOLD = 3;

  function renderMarkdown(text) {
    var blocks = parseMarkdown(text);
    var root = node("div", "md-body");
    var sections = [];
    var current = null;
    var intro = [];
    blocks.forEach(function (block) {
      if (block.type === "heading" && block.level === 2) {
        current = { title: block.text, blocks: [] };
        sections.push(current);
      } else if (current) {
        current.blocks.push(block);
      } else {
        intro.push(block);
      }
    });

    if (sections.length < SECTION_THRESHOLD) {
      renderBlocks(root, blocks, {});
      return root;
    }

    renderBlocks(root, intro, {});
    var outline = node("nav", "md-outline");
    outline.setAttribute("aria-label", "Sections");
    outline.appendChild(node("p", "md-outline-title",
      "Sections (" + sections.length + ")"));
    var outlineList = node("div", "md-outline-list");
    outline.appendChild(outlineList);
    root.appendChild(outline);

    sections.forEach(function (section, index) {
      var details = node("details", "md-section");
      if (index === 0) details.open = true;
      var summary = node("summary", "md-section-title");
      renderInline(summary, parseInline(section.title));
      details.appendChild(summary);
      var body = node("div", "md-section-body");
      renderBlocks(body, section.blocks, {});
      details.appendChild(body);
      root.appendChild(details);

      var jump = node("button", "md-outline-item", plainText(section.title));
      jump.type = "button";
      jump.addEventListener("click", function () {
        details.open = true;
        details.scrollIntoView({ block: "start" });
        summary.focus();
      });
      outlineList.appendChild(jump);
    });
    return root;
  }

  // Containers deeper than this start collapsed.
  var OPEN_DEPTH = 2;

  function jsonPrimitive(value) {
    var type = value === null ? "null" : typeof value;
    var text = type === "string" ? JSON.stringify(value) : String(value);
    return node("span", "json-value json-" + type, text);
  }

  function jsonKey(parent, key) {
    if (key === undefined) return;
    parent.appendChild(node("span", "json-key",
      typeof key === "number" ? String(key) : JSON.stringify(key)));
    parent.appendChild(node("span", "json-punct", ": "));
  }

  function jsonNode(key, value, depth) {
    var isContainer = value !== null && typeof value === "object";
    if (!isContainer || !Object.keys(value).length) {
      var row = node("div", "json-row");
      jsonKey(row, key);
      row.appendChild(isContainer
        ? node("span", "json-punct", Array.isArray(value) ? "[]" : "{}")
        : jsonPrimitive(value));
      var embedded = decodeEmbeddedJSON(value);
      if (embedded) {
        var wrap = node("div", "json-node");
        wrap.appendChild(row);
        var decoded = node("details", "json-embedded");
        decoded.appendChild(node("summary", "json-summary",
          "decoded JSON string " + summarizeJSON(embedded)));
        decoded.appendChild(jsonChildren(embedded, depth + 1));
        wrap.appendChild(decoded);
        return wrap;
      }
      return row;
    }
    var details = node("details", "json-node");
    if (depth < OPEN_DEPTH) details.open = true;
    var summary = node("summary", "json-summary");
    jsonKey(summary, key);
    summary.appendChild(node("span", "json-count", summarizeJSON(value)));
    details.appendChild(summary);
    details.appendChild(jsonChildren(value, depth));
    return details;
  }

  function jsonChildren(value, depth) {
    var children = node("div", "json-children");
    if (Array.isArray(value)) {
      value.forEach(function (item, index) {
        children.appendChild(jsonNode(index, item, depth + 1));
      });
    } else {
      Object.keys(value).forEach(function (name) {
        children.appendChild(jsonNode(name, value[name], depth + 1));
      });
    }
    return children;
  }

  function renderJSON(value) {
    var root = node("div", "json-tree");
    root.appendChild(jsonNode(undefined, value, 0));
    return root;
  }

  // Open or close every foldable node inside a rendered document.
  function setExpanded(container, open) {
    var all = container.querySelectorAll("details");
    for (var i = 0; i < all.length; i++) all[i].open = open;
  }

  return {
    parseMarkdown: parseMarkdown,
    parseInline: parseInline,
    summarizeJSON: summarizeJSON,
    decodeEmbeddedJSON: decodeEmbeddedJSON,
    kindOf: kindOf,
    videoOutcome: videoOutcome,
    verdictPresentation: verdictPresentation,
    renderMarkdown: renderMarkdown,
    renderJSON: renderJSON,
    setExpanded: setExpanded
  };
});
