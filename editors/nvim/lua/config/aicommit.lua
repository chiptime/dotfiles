-- AI commit: generate a strict Conventional Commits message from the staged
-- diff with the opencode CLI, review it in a COMMIT_EDITMSG buffer, and
-- commit on :wq only. Failures notify and abort; no buffer ever opens on
-- failure, and this flow never stages anything (git add is forbidden).
-- Design: openspec/changes/nvim-ide-environment/design.md (P2, R9).

local M = {}

local DIFF_THRESHOLD = 65536 -- bytes; at/above this the payload becomes stat + hunk headers
local MAX_HEADERS = 200     -- hunk-header cap, plus an explicit truncation line
M.timeout_ms = 90000        -- opencode gets 90 s, then vim.system SIGTERMs it
M.opencode_bin = "opencode" -- overridable so headless fixtures can fake classes

-- Prompt template lives as a Lua literal here, versioned with the dotfiles
-- (spec: Strict Conventional Commits In English).
M.prompt = [[You write git commit messages.
Answer with EXACTLY one line: a strict Conventional Commits message in English.
Format: type(scope): subject - or type: subject; the scope and a
breaking-change "!" are optional. type is one of:
feat fix docs style refactor perf test build ci chore revert
Subject: concise, imperative mood, lowercase, no trailing period, at most 72
characters. No markdown, no code fences, no quotes, no explanation - only
that one line.

Staged changes:
]]

-- Abort: notify and stop. Returning nil never opens a buffer.
local function abort(msg, level)
  vim.notify("aicommit: " .. msg, level or vim.log.levels.WARN)
  return nil
end

-- Every git call is pinned to the resolved repo root with an explicit cwd
-- (threat: Git repository selection); no inherited relative paths.
local function git(args, root)
  local argv = { "git" }
  for i = 1, #args do
    argv[i + 1] = args[i]
  end
  return vim.system(argv, { cwd = root, text = true }):wait()
end

function M.repo_root(buf)
  return vim.fs.root(buf or 0, { ".git" })
end

-- True when the staging area is empty (git diff --cached --quiet exits 0).
function M.staged_empty(root)
  return git({ "diff", "--cached", "--quiet" }, root).code == 0
end

-- Size gate: below the threshold send the full diff; at or above it send
-- --stat plus every @@ header from --unified=0, capped at MAX_HEADERS with an
-- explicit truncation line when hunks were dropped.
function M.build_payload(root)
  local diff = git({ "diff", "--cached" }, root).stdout or ""
  if #diff < DIFF_THRESHOLD then
    return diff
  end
  local stat = git({ "diff", "--cached", "--stat" }, root).stdout or ""
  local unified = git({ "diff", "--cached", "--unified=0" }, root).stdout or ""
  local headers, total = {}, 0
  for line in unified:gmatch("[^\r\n]+") do
    if line:sub(1, 2) == "@@" then
      total = total + 1
      if #headers < MAX_HEADERS then
        headers[#headers + 1] = line
      end
    end
  end
  local out = { stat, "", "Hunk headers:", table.concat(headers, "\n") }
  if total > #headers then
    out[#out + 1] = string.format("(hunk headers truncated: %d of %d shown)", #headers, total)
  end
  return table.concat(out, "\n")
end

-- Strip terminal decoration opencode may emit: CSI/OSC escape sequences and
-- carriage returns (design Open Question 2 mitigation).
function M.strip_ansi(s)
  s = s:gsub("\27%[[0-9;:?<=>!%-/]*[\32-\47]*[\64-\126]", "") -- CSI ... final byte
  s = s:gsub("\27%][^\7\27]*\7", "") -- OSC ... BEL
  s = s:gsub("\27%][^\7\27]*\27\\", "") -- OSC ... ST
  s = s:gsub("\27.", "") -- any other escape
  return (s:gsub("\r", ""))
end

-- The first non-empty line must be a Conventional Commits message.
-- Lua patterns have no alternation, so the type list becomes a set lookup
-- around the structural match.
-- Deviation from the design's `\(..\)`: the spec mandates type(scope)?:
-- subject, so any non-empty word-like scope passes (a strict two-character
-- scope would reject this repo's own "feat(nvim): ..." style).
local TYPES = {
  feat = true, fix = true, docs = true, style = true, refactor = true,
  perf = true, test = true, build = true, ci = true, chore = true, revert = true,
}
-- Lua patterns cannot quantify groups, so the optional scope becomes two
-- patterns: with scope (bang optional) and without.
local CC_SCOPED = "^(%a+)%([%w%-%_.]+%)!?: (.+)$"
local CC_PLAIN = "^(%a+)!?: (.+)$"

function M.validate(message)
  for line in message:gmatch("[^\r\n]+") do
    if line:find("%S") then
      local typ, subject = line:match(CC_SCOPED)
      if not typ then
        typ, subject = line:match(CC_PLAIN)
      end
      return typ ~= nil and TYPES[typ] == true and subject:find("%S") ~= nil, line
    end
  end
  return false, nil
end

-- Success path: editable COMMIT_EDITMSG buffer. BufWriteCmd runs
-- `git commit -F <file> --cleanup=strip` (commits the index, never stages);
-- :q or any non-write exit discards without committing.
local function commit_buffer(root, message)
  local path = root .. "/.git/COMMIT_EDITMSG"
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_get_name(b) == path then
      vim.api.nvim_buf_delete(b, { force = true })
    end
  end
  vim.cmd("botright split " .. vim.fn.fnameescape(path))
  local buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(message, "\n", { plain = true }))
  vim.bo[buf].buftype = "acwrite"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].modified = false
  vim.api.nvim_create_autocmd("BufWriteCmd", {
    buffer = buf,
    desc = "aicommit: commit the reviewed message (git commit -F)",
    callback = function()
      vim.fn.writefile(vim.api.nvim_buf_get_lines(buf, 0, -1, false), path)
      local r = git({ "commit", "-F", path, "--cleanup=strip" }, root)
      if r.code ~= 0 then
        vim.notify("aicommit: git commit failed: " .. (r.stderr or ""):gsub("^%s+", ""):gsub("%s+$", ""), vim.log.levels.ERROR)
        return -- buffer stays modified: :wq stops here, nothing was committed
      end
      vim.bo[buf].buftype = "nofile" -- refuse further writes
      vim.bo[buf].modified = false -- let :wq's quit (or a plain :q) close it
    end,
  })
end

-- <leader>gc flow: repo root -> index gate -> size gate -> opencode -> buffer.
function M.run()
  local root = M.repo_root()
  if not root then
    return abort("not inside a git repository")
  end
  if M.staged_empty(root) then
    return abort("staging area is empty - stage changes first (this flow never stages)")
  end
  local payload = M.build_payload(root)
  if payload == "" then
    return abort("staged diff is empty - stage changes first (this flow never stages)")
  end
  vim.system({ M.opencode_bin, "run", M.prompt .. payload }, {
    cwd = root,
    timeout = M.timeout_ms,
    text = true,
  }, function(res)
    vim.schedule(function()
      if res.code == 124 and res.signal == 15 then
        return abort(string.format("opencode timed out after %d s", M.timeout_ms / 1000), vim.log.levels.ERROR)
      end
      if res.code ~= 0 then
        return abort("opencode failed (exit " .. tostring(res.code) .. ")", vim.log.levels.ERROR)
      end
      local out = M.strip_ansi(res.stdout or ""):match("^%s*(.-)%s*$")
      if not out or out == "" then
        return abort("opencode returned no usable output", vim.log.levels.ERROR)
      end
      local ok, line = M.validate(out)
      if not ok then
        return abort("not a Conventional Commits message: " .. (line or out):sub(1, 80), vim.log.levels.ERROR)
      end
      commit_buffer(root, out)
    end)
  end)
end

vim.keymap.set("n", "<leader>gc", M.run, { desc = "AI commit from staged diff" })

return M
