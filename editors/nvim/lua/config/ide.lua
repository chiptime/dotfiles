local M = {}
local WIDTH = 34
local state = { activities = {}, active = nil, central = nil, manager = nil, maximize = nil }
local activity_names = { explorer = "Explorer", open_editors = "Open Editors", search = "Search", source_control = "Source Control", history = "History", debug = "Run/Debug" }
local function notify(message, level)
  vim.notify(message, level or vim.log.levels.INFO, { title = "IDE" })
end
local function valid_win(win) return win and vim.api.nvim_win_is_valid(win) end
local function valid_buf(buf) return buf and vim.api.nvim_buf_is_valid(buf) end
local function owned(record)
  return record and valid_win(record.win) and valid_buf(record.buf) and vim.api.nvim_win_get_buf(record.win) == record.buf
end
local function managed_window(win)
  for _, record in pairs(state.activities) do
    if record.win == win and owned(record) then return true end
  end
  return false
end
local function normal_editor(win) return valid_win(win) and not managed_window(win) and vim.bo[vim.api.nvim_win_get_buf(win)].buftype == "" end
local function remember_central()
  local win = vim.api.nvim_get_current_win()
  if normal_editor(win) then state.central = win end
end
local function focus_central()
  if normal_editor(state.central) then vim.api.nvim_set_current_win(state.central); return true end
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
    if normal_editor(win) then state.central = win; vim.api.nvim_set_current_win(win); return true end
  end
  return false
end
local function hide_record(record) if owned(record) then vim.api.nvim_win_hide(record.win) end end
local function reset_search_window() state.pending_search_win, state.pending_search_anchor = nil, nil end
local function begin_search_window()
  reset_search_window(); local anchor = vim.api.nvim_get_current_win()
  if normal_editor(anchor) then state.pending_search_anchor = anchor; return true end
end
local function hide_search_window()
  local win = state.pending_search_win; reset_search_window()
  if valid_win(win) then pcall(vim.api.nvim_win_hide, win) end
end
function M._create_search_window()
  local anchor = state.pending_search_anchor; if not normal_editor(anchor) then error("Search lost its editor anchor.") end
  local win = vim.api.nvim_open_win(0, true, { split = "left", win = anchor, width = WIDTH })
  state.pending_search_win = win
  if not valid_win(win) then error("Search window was closed during creation.") end
  vim.api.nvim_set_current_win(win); if vim.api.nvim_get_current_win() ~= win then error("Search window focus changed during creation.") end
  return win
end
local function hide_other_activities(name)
  for activity, record in pairs(state.activities) do if activity ~= name then hide_record(record) end end
end
local function manager_window()
  for _, record in pairs(state.activities) do if state.manager == record.win and owned(record) then return state.manager end end
  state.manager = nil; remember_central(); vim.cmd("topleft " .. WIDTH .. "vsplit")
  state.manager = vim.api.nvim_get_current_win(); vim.cmd("enew")
  local buf = vim.api.nvim_get_current_buf()
  vim.bo[buf].buftype = "nofile"; vim.bo[buf].bufhidden = "hide"; vim.bo[buf].swapfile = false
  vim.bo[buf].buflisted = false; vim.bo[buf].modifiable = false
  return state.manager
end
local function prepare_activity(name)
  remember_central()
  local previous = state.active and state.activities[state.active]
  previous = owned(previous) and previous or nil
  hide_other_activities(name)
  local record = state.activities[name]
  if owned(record) then
    vim.api.nvim_set_current_win(record.win)
    state.active = name
    return record, true
  end
  state.activities[name] = nil
  local win = manager_window()
  vim.api.nvim_set_current_win(win)
  return { win = win, previous = previous }, false
end
local function rollback_activity(prepared)
  local previous, win = prepared.previous, prepared.win
  if valid_win(win) and previous and valid_buf(previous.buf) then
    vim.api.nvim_win_set_buf(win, previous.buf)
    state.activities[previous.name] = { name = previous.name, win = win, buf = previous.buf }
    state.manager, state.active = win, previous.name; vim.api.nvim_set_current_win(win)
    return
  end
  if previous and owned(previous) then
    state.manager, state.active = previous.win, previous.name; vim.api.nvim_set_current_win(previous.win)
    return
  end
  if valid_win(win) then vim.api.nvim_win_hide(win) end
  if state.manager == win then state.manager = nil end
  state.active = nil; focus_central()
end
local function prepare_search()
  remember_central()
  local record = state.activities.search
  if owned(record) then vim.api.nvim_set_current_win(record.win); state.active = "search"; return record, true end
  state.activities.search = nil
  local previous = state.active and state.activities[state.active]
  previous = owned(previous) and previous or nil
  focus_central()
  return { previous = previous }, false
end
local function adopt(name, win)
  win = win or vim.api.nvim_get_current_win()
  local record = { name = name, win = win, buf = vim.api.nvim_win_get_buf(win) }
  state.activities[name], state.manager, state.active = record, win, name
  return record
end
local function complete_search(prepared, ok, err)
  local win = state.pending_search_win
  if ok and valid_win(win) then hide_other_activities("search"); adopt("search", win); reset_search_window(); return true end
  hide_search_window(); notify("Search is unavailable: " .. tostring(err or "provider did not create an owned window."), vim.log.levels.WARN); rollback_activity(prepared); return false
end
local function provider(name, module)
  local label = ({ ["neo-tree.nvim"] = "Explorer", ["grug-far.nvim"] = "Search", neogit = "Source Control", ["vim-fugitive"] = "History", ["vim-flog"] = "History" })[name] or name
  local ok_lazy, lazy = pcall(require, "lazy")
  if not ok_lazy then
    notify(label .. " is unavailable: lazy.nvim is not ready.", vim.log.levels.WARN)
    return nil
  end
  local ok_load, load_error = pcall(lazy.load, { plugins = { name }, wait = true })
  if not ok_load then
    notify(label .. " is unavailable: " .. tostring(load_error) .. ". Run :Lazy sync after updating configuration.", vim.log.levels.WARN)
    return nil
  end
  if not module then
    return true
  end
  local ok_module, value = pcall(require, module)
  if not ok_module then
    notify(label .. " is unavailable; run :Lazy sync after updating configuration.", vim.log.levels.WARN)
    return nil
  end
  return value
end
local function context_dir()
  local function directory(win)
    local name = normal_editor(win) and vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(win)) or ""
    return name ~= "" and vim.fn.fnamemodify(name, ":p:h") or nil
  end
  return directory(vim.api.nvim_get_current_win()) or directory(state.central)
end
local function git_root()
  if vim.fn.executable("git") ~= 1 then return nil, "Git is unavailable. Install git and retry." end
  local cwd = context_dir()
  if not cwd then return nil, "Open a file in the workspace and retry." end
  local result = vim.fn.systemlist({ "git", "-C", cwd, "rev-parse", "--show-toplevel" })
  if vim.v.shell_error ~= 0 or not result[1] or result[1] == "" then return nil, "This activity is available only inside a Git repository." end
  return result[1]
end
local function workspace_root() return git_root() or context_dir() end
local function require_git(name)
  local root, reason = git_root()
  if not root then
    notify(activity_names[name] .. " is inactive: " .. reason, vim.log.levels.WARN)
  end
  return root
end
local function open_neotree(source, reveal)
  local neo = provider("neo-tree.nvim", "neo-tree.command")
  if not neo then
    return
  end
  local args = { action = "focus", source = source, position = "left" }
  if reveal then
    args.reveal = true
  end
  local prepared, reused = prepare_activity(source == "buffers" and "open_editors" or "explorer")
  local ok, err = pcall(neo.execute, args)
  if not ok then
    notify("Neo-tree is unavailable: " .. tostring(err), vim.log.levels.WARN)
    rollback_activity(prepared)
    return
  end
  if not reused then adopt(source == "buffers" and "open_editors" or "explorer") end
end
local function open_search()
  if vim.version().major == 0 and vim.version().minor < 11 then
    notify("Search requires Neovim 0.11+.", vim.log.levels.WARN)
    return
  end
  if vim.fn.executable("rg") ~= 1 then
    notify("Search is unavailable: install ripgrep (rg) and retry.", vim.log.levels.WARN)
    return
  end
  local root = workspace_root()
  local grug = provider("grug-far.nvim", "grug-far")
  if not grug then return end
  local prepared, reused = prepare_search()
  if reused then return end
  local known, instance = pcall(grug.get_instance, "ide-search")
  if not begin_search_window() then notify("Search is unavailable: no normal editor window is available.", vim.log.levels.WARN); rollback_activity(prepared); return end
  if known then
    local ok, err = pcall(instance.open, instance)
    local got, buf = pcall(instance.get_buf, instance)
    local win = ok and got and vim.fn.bufwinid(buf) or -1
    if not state.pending_search_win and win ~= -1 and valid_win(win) and vim.api.nvim_win_get_buf(win) == buf then state.pending_search_win = win end
    complete_search(prepared, ok, err); return
  end
  if complete_search(prepared, pcall(grug.open, {
    instanceName = "ide-search", prefills = { paths = root }, windowCreationCommand = "lua require('config.ide')._create_search_window()",
  })) then notify("Search uses Grug-Far preview and confirmation controls; replacements are never applied by the coordinator.") end
end
local function open_neogit()
  local root = require_git("source_control")
  if not root then return end
  local neogit = provider("neogit", "neogit")
  if not neogit then
    return
  end
  local prepared, reused = prepare_activity("source_control")
  if reused then return end
  local ok, err = pcall(neogit.open, { cwd = root, kind = "replace" })
  if not ok then
    notify("Source Control is unavailable: " .. tostring(err), vim.log.levels.WARN)
    rollback_activity(prepared)
    return
  end
  adopt("source_control")
end
local function open_history()
  local root = require_git("history")
  if not root then
    return
  end
  if not provider("vim-fugitive") or not provider("vim-flog") then return end
  if not (vim.fn.exists("*FugitiveDetect") == 1 and vim.fn.exists(":Flog") == 2) then
    notify("History is unavailable; run :Lazy sync after updating configuration.", vim.log.levels.WARN)
    return
  end
  local prepared, reused = prepare_activity("history")
  if reused then return end
  local ok, err = pcall(vim.api.nvim_win_call, prepared.win, function()
    vim.cmd("lcd " .. vim.fn.fnameescape(root))
    vim.cmd("call FugitiveDetect(" .. vim.fn.string(root) .. ")")
    vim.cmd("Flog -open-cmd=edit")
  end)
  if not ok then
    notify("History is unavailable: " .. tostring(err), vim.log.levels.WARN)
    rollback_activity(prepared)
    return
  end
  adopt("history")
end
local function open_debug()
  local record = state.activities.debug
  local prepared, reused = prepare_activity("debug")
  if reused then return end
  local win = prepared.win
  if record and valid_buf(record.buf) then
    vim.api.nvim_win_set_buf(win, record.buf)
  else
    vim.cmd("enew")
  end
  local buf = vim.api.nvim_get_current_buf()
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
    "Run / Debug",
    "",
    "Debug configuration is deferred until Slice 5.",
    "No adapter, UI, filetype event, or automatic debug session is configured.",
    "",
    "Explicit nvim-dap commands remain available when configured:",
    ":DapContinue  :DapStepInto  :DapStepOver  :DapStepOut",
    ":DapToggleBreakpoint  :DapTerminate",
  })
  vim.bo[buf].modifiable = false
  vim.api.nvim_buf_set_name(buf, "IDE Run Debug")
  state.activities.debug = { name = "debug", win = win, buf = buf }
  state.manager = win
  state.active = "debug"
end
function M.open_activity(name, opts)
  if not activity_names[name] then
    notify("Unknown IDE activity: " .. tostring(name), vim.log.levels.WARN)
    return
  end
  if name == "explorer" then
    open_neotree("filesystem", opts and opts.reveal)
  elseif name == "open_editors" then
    open_neotree("buffers", false)
  elseif name == "search" then
    open_search()
  elseif name == "source_control" then
    open_neogit()
  elseif name == "history" then
    open_history()
  else
    open_debug()
  end
end
function M.toggle_sidebar()
  local record = state.active and state.activities[state.active]
  if owned(record) then
    hide_record(record)
    focus_central()
  elseif state.active then
    M.open_activity(state.active)
  else
    M.open_activity("explorer")
  end
end
function M.maximize_or_restore_active()
  local record = state.active and state.activities[state.active]
  if not owned(record) then
    notify("No managed IDE activity is active.", vim.log.levels.WARN)
    return
  end
  if state.maximize and state.maximize.win == record.win then
    if valid_win(state.maximize.win) then
      vim.api.nvim_set_current_win(state.maximize.win)
      vim.cmd(state.maximize.layout)
    end
    state.maximize = nil
    return
  end
  state.maximize = { win = record.win, layout = vim.fn.winrestcmd() }
  vim.api.nvim_set_current_win(record.win)
  vim.cmd("wincmd |")
  vim.cmd("wincmd _")
end
local function central_step(direction)
  local wins = {}
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
    if not managed_window(win) and vim.bo[vim.api.nvim_win_get_buf(win)].buftype == "" then
      table.insert(wins, win)
    end
  end
  if #wins == 0 then
    notify("No central editor window is available.", vim.log.levels.WARN)
    return
  end
  local current = vim.api.nvim_get_current_win()
  local index = 1
  for i, win in ipairs(wins) do
    if win == current then
      index = i
      break
    end
  end
  index = ((index - 1 + direction) % #wins) + 1
  state.central = wins[index]
  vim.api.nvim_set_current_win(wins[index])
end
local function map(lhs, rhs, desc)
  local existing = vim.fn.maparg(lhs, "n", false, true)
  if type(existing) == "table" and next(existing) then
    notify("IDE fallback " .. lhs .. " was not installed because an existing mapping owns it.", vim.log.levels.WARN)
    return
  end
  vim.keymap.set("n", lhs, rhs, { desc = desc })
end
local function command(name, callback, opts)
  if vim.fn.exists(":" .. name) == 2 then
    notify("IDE command :" .. name .. " was not installed because it already exists.", vim.log.levels.WARN)
    return
  end
  vim.api.nvim_create_user_command(name, callback, opts or { desc = "IDE coordinator action" })
end
command("IdeExplorer", function()
  M.open_activity("explorer")
end, { desc = "Open IDE Explorer" })
command("IdeSearch", function()
  M.open_activity("search")
end, { desc = "Open IDE Search" })
command("IdeSourceControl", function()
  M.open_activity("source_control")
end, { desc = "Open IDE Source Control" })
command("IdeHistory", function()
  M.open_activity("history")
end, { desc = "Open IDE History" })
command("IdeDebug", function()
  M.open_activity("debug")
end, { desc = "Open IDE Run/Debug" })
command("IdeSidebarToggle", M.toggle_sidebar, { desc = "Toggle IDE sidebar" })
command("IdePanelMaximize", M.maximize_or_restore_active, { desc = "Maximize or restore IDE panel" })
command("IdeSplitBelow", function()
  vim.cmd("belowright split")
end, { desc = "Split editor below" })
map("<leader>b", M.toggle_sidebar, "Toggle IDE sidebar")
map("<leader>e", function() M.open_activity("explorer") end, "IDE Explorer")
map("<leader>E", function() M.open_activity("explorer", { reveal = true }) end, "IDE reveal current file")
map("<leader>fo", function() M.open_activity("open_editors") end, "IDE Open Editors")
map("<leader>fG", function() M.open_activity("search") end, "IDE Search")
map("<leader>gC", function() M.open_activity("source_control") end, "IDE Source Control")
map("<leader>gG", function() M.open_activity("history") end, "IDE Git graph history")
map("<leader>dd", function() M.open_activity("debug") end, "IDE Run/Debug")
map("<leader>m", M.maximize_or_restore_active, "Maximize IDE panel")
map("K", vim.lsp.buf.hover, "LSP hover")
map("[b", function() central_step(-1) end, "Previous central editor")
map("]b", function() central_step(1) end, "Next central editor")
M._state = state
M._valid_win = valid_win
return M
