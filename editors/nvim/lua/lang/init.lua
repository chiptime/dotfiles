local M = {}

local modules = {
  require("lang.lua"),
  require("lang.shell"),
  require("lang.markdown"),
  require("lang.data"),
  require("lang.git"),
  require("lang.frontend"),
  require("lang.sap"),
  require("lang.powershell"),
  require("lang.batch"),
  require("lang.python"),
}

local function append_unique(target, values)
  for _, value in ipairs(values or {}) do
    if not vim.tbl_contains(target, value) then
      table.insert(target, value)
    end
  end
end

function M.filetypes()
  local filetypes = {}

  for _, module in ipairs(modules) do
    append_unique(filetypes, module.filetypes)
  end

  table.sort(filetypes)
  return filetypes
end

function M.treesitter_parsers()
  local parsers = {}

  for _, module in ipairs(modules) do
    append_unique(parsers, module.parsers)
  end

  table.sort(parsers)
  return parsers
end

local function server_available(registry, name)
  if registry == nil then
    return true
  end

  if type(registry) == "function" then
    return registry(name)
  end

  return registry[name] ~= nil
end

function M.lsp_servers(server_registry)
  local servers = {}

  for _, module in ipairs(modules) do
    for name, config in pairs(module.servers or {}) do
      servers[name] = config
    end

    for _, choice in ipairs(module.server_choices or {}) do
      for _, name in ipairs(choice.names or {}) do
        if server_available(server_registry, name) then
          servers[name] = choice.config or {}
          break
        end
      end
    end
  end

  return servers
end

return M
