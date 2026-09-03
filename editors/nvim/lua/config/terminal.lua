-- Persistent terminal workflow (non-legacy route), backed by toggleterm.nvim.
--
-- The bottom panel group owns a one-visible invariant: at most one panel
-- terminal window is visible; cycling hides the visible terminal and opens
-- the next hidden one. Hide/show never notifies.
--
-- Panel terminals use toggleterm counts 1-8. Count 9 is RESERVED for a
-- future vertical agent panel (see openspec/changes/nvim-ide-environment/
-- design.md, "Reserved agent-panel pattern"): this module never allocates it
-- and panel iteration is bounded to counts 1-8, so adding a count-9 panel
-- later changes no existing code path.

local PROFILE_RELATIVE_PATH = ".nvim/terminals.json"
local PANEL_COUNT_MIN = 1
local PANEL_COUNT_MAX = 8

local M = { _state = { last_opened_count = nil } }

-- Load toggleterm on demand; module setup must stay safe before lazy.nvim runs.
local function terminal_api()
  require("lazy").load({ plugins = { "toggleterm.nvim" } })
  return require("toggleterm.terminal")
end

--- Parse a repo terminal profile as pure data.
--- Schema: { version = 1, terminals = { { name = "...", cmd = "..." } } };
--- unknown keys are ignored. The file is only ever decoded (vim.json.decode
--- under pcall) — nothing in it is executed, and a sibling .nvim/terminals.lua
--- is deliberately ignored.
--- Returns the list of { name = , cmd = } entries, or nil when the profile is
--- absent, unparsable (one WARN), or yields no entry with non-empty name+cmd.
function M.parse_profile(root)
  root = root or vim.fs.root(0, { ".git" }) or (vim.uv or vim.loop).cwd()
  local path = root and (root .. "/" .. PROFILE_RELATIVE_PATH) or PROFILE_RELATIVE_PATH

  local file = io.open(path, "r")
  if not file then
    return nil
  end
  local content = file:read("*a")
  file:close()

  local ok, data = pcall(vim.json.decode, content)
  if not ok or type(data) ~= "table" or type(data.terminals) ~= "table" then
    vim.notify(
      "Ignoring " .. PROFILE_RELATIVE_PATH .. ": not parsable JSON",
      vim.log.levels.WARN,
      { title = "Terminal profile" }
    )
    return nil
  end

  local entries = {}
  for _, entry in ipairs(data.terminals) do
    if
      type(entry) == "table"
      and type(entry.name) == "string"
      and entry.name ~= ""
      and type(entry.cmd) == "string"
      and entry.cmd ~= ""
    then
      entries[#entries + 1] = { name = entry.name, cmd = entry.cmd }
    end
  end
  if #entries == 0 then
    return nil
  end
  return entries
end

local function panel_terms()
  local terms = {}
  for _, term in ipairs(terminal_api().get_all(true)) do
    if term.id >= PANEL_COUNT_MIN and term.id <= PANEL_COUNT_MAX then
      terms[#terms + 1] = term
    end
  end
  table.sort(terms, function(a, b)
    return a.id < b.id
  end)
  return terms
end

local function visible_panel_term()
  for _, term in ipairs(panel_terms()) do
    if term:is_open() then
      return term
    end
  end
  return nil
end

local function new_panel_term(cmd)
  local used = {}
  local terms = panel_terms()
  for _, term in ipairs(terms) do
    used[term.id] = true
  end
  local count = nil
  for c = PANEL_COUNT_MIN, PANEL_COUNT_MAX do
    if not used[c] then
      count = c
      break
    end
  end
  if not count then
    vim.notify(
      string.format("Terminal panel is full (%d). Close one with <leader>tq first.", PANEL_COUNT_MAX),
      vim.log.levels.WARN,
      { title = "Terminal" }
    )
    return nil
  end
  local term = terminal_api().Terminal:new({
    cmd = cmd,
    count = count,
    direction = "horizontal",
    hidden = true,
  })
  -- Spawn immediately: the job and buffer exist before any window opens, and
  -- the count is registered so the next allocation cannot collide.
  term:spawn()
  return term
end

local function open_exclusive(term)
  local visible = visible_panel_term()
  if visible and visible ~= term then
    visible:close()
  end
  term:open()
  M._state.last_opened_count = term.id
  return term
end

--- <leader>tp: start the repo profile; absent profile opens a plain shell.
function M.start_profile()
  local entries = M.parse_profile()
  if not entries then
    local terms = panel_terms()
    if #terms == 0 then
      local shell = new_panel_term(vim.o.shell)
      if shell then
        open_exclusive(shell)
      end
    else
      local pick = nil
      for _, term in ipairs(terms) do
        if term.id == M._state.last_opened_count then
          pick = term
        end
      end
      open_exclusive(pick or terms[#terms])
    end
    return
  end

  local visible = visible_panel_term()
  if visible then
    visible:close()
  end

  local created = {}
  for _, entry in ipairs(entries) do
    local term = new_panel_term(entry.cmd)
    if term then
      created[#created + 1] = term
    end
  end
  if #created == 0 then
    return
  end
  if #created < #entries then
    vim.notify(
      string.format("Terminal panel is full: started %d of %d profile terminals.", #created, #entries),
      vim.log.levels.WARN,
      { title = "Terminal profile" }
    )
  end

  -- Every terminal spawned with its command while hidden; open only the
  -- first so the one-visible invariant holds from the start.
  open_exclusive(created[1])
end

--- <leader>to: toggle the visible panel terminal.
function M.toggle_visible()
  local visible = visible_panel_term()
  if visible then
    visible:close()
    return
  end
  local terms = panel_terms()
  if #terms == 0 then
    local shell = new_panel_term(vim.o.shell)
    if shell then
      open_exclusive(shell)
    end
    return
  end
  local pick = nil
  for _, term in ipairs(terms) do
    if term.id == M._state.last_opened_count then
      pick = term
    end
  end
  open_exclusive(pick or terms[#terms])
end

--- <leader>tn: open a new shell terminal.
function M.new_terminal()
  local shell = new_panel_term(vim.o.shell)
  if shell then
    return open_exclusive(shell)
  end
  return nil
end

--- <leader>t] / <leader>t[: cycle next/previous, keeping one visible.
function M.cycle(next)
  local terms = panel_terms()
  if #terms == 0 then
    M.toggle_visible()
    return
  end
  local visible = visible_panel_term()
  local index = 0
  if visible then
    for i, term in ipairs(terms) do
      if term == visible then
        index = i
      end
    end
    visible:close()
  end
  local target
  if index == 0 then
    target = next and terms[1] or terms[#terms]
  elseif next then
    target = terms[(index % #terms) + 1]
  else
    target = terms[((index - 2) % #terms) + 1]
  end
  open_exclusive(target)
end

--- <leader>tq: close and shut down the visible terminal.
function M.close_current()
  local visible = visible_panel_term()
  if visible then
    visible:shutdown()
  end
end

--- <leader>ts: pick a terminal.
function M.select()
  terminal_api()
  vim.cmd("TermSelect")
end

local function setup()
  vim.keymap.set("n", "<leader>tp", M.start_profile, { desc = "Terminal: start repo profile" })
  vim.keymap.set("n", "<leader>to", M.toggle_visible, { desc = "Terminal: toggle visible" })
  vim.keymap.set("n", "<leader>tn", M.new_terminal, { desc = "Terminal: new shell" })
  vim.keymap.set("n", "<leader>t]", function()
    M.cycle(true)
  end, { desc = "Terminal: cycle next" })
  vim.keymap.set("n", "<leader>t[", function()
    M.cycle(false)
  end, { desc = "Terminal: cycle previous" })
  vim.keymap.set("n", "<leader>tq", M.close_current, { desc = "Terminal: close and shutdown current" })
  vim.keymap.set("n", "<leader>ts", M.select, { desc = "Terminal: select" })
  vim.keymap.set("t", "<Esc><Esc>", [[<C-\><C-n>]], { desc = "Leave terminal mode" })
end

setup()

return M
