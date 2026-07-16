local lang = require("lang")

local function lsp_root_dir(util, markers)
  if not markers or vim.tbl_isempty(markers) then
    return nil
  end

  local root_pattern = util.root_pattern(unpack(markers))

  return function(fname)
    return root_pattern(fname)
  end
end

local function server_enabled(config)
  local condition = config and config.condition
  if condition == nil then
    return true
  end

  if type(condition) == "function" then
    local ok, result = pcall(condition, config)
    return ok and result == true
  end

  return condition == true
end

local function strip_internal_server_config(config)
  local normalized = vim.tbl_deep_extend("force", {}, config or {})
  normalized.condition = nil
  return normalized
end

local function normalize_server_config(util, config)
  local normalized = strip_internal_server_config(config)
  local markers = normalized.root_markers

  normalized.root_markers = nil

  if markers then
    normalized.root_dir = lsp_root_dir(util, markers)
  end

  return normalized
end

local function native_lsp_config_available()
  return vim.lsp.config ~= nil and type(vim.lsp.enable) == "function"
end

local function setup_native_lsp(server_name, server_config, capabilities)
  local normalized = strip_internal_server_config(server_config)
  normalized.capabilities = vim.tbl_deep_extend("force", capabilities, normalized.capabilities or {})

  local ok_config = pcall(vim.lsp.config, server_name, normalized)
  if not ok_config then
    return false
  end

  return pcall(vim.lsp.enable, server_name)
end

local function setup_legacy_lsp(lspconfig, server_name, server_config, capabilities)
  local server = lspconfig[server_name]
  if server == nil then
    return false
  end

  local normalized = normalize_server_config(lspconfig.util, server_config)
  normalized.capabilities = vim.tbl_deep_extend("force", capabilities, normalized.capabilities or {})

  server.setup(normalized)
  return true
end

local function buffer_lsp_clients()
  if vim.lsp.get_clients then
    return vim.lsp.get_clients({ bufnr = 0 })
  end

  return vim.lsp.get_active_clients({ bufnr = 0 })
end

local function create_lsp_command(name, callback, opts)
  if vim.fn.exists(":" .. name) == 0 then
    vim.api.nvim_create_user_command(name, callback, opts or {})
  end
end

local function setup_lsp_commands()
  create_lsp_command("LspInfo", function()
    vim.cmd("checkhealth vim.lsp")
  end, { desc = "Show Neovim LSP health" })

  create_lsp_command("LspLog", function()
    vim.cmd("edit " .. vim.fn.fnameescape(vim.lsp.get_log_path()))
  end, { desc = "Open the Neovim LSP log" })

  create_lsp_command("LspStop", function(command)
    for _, client in ipairs(buffer_lsp_clients()) do
      if command.args == "" or client.name == command.args then
        client:stop()
      end
    end
  end, { complete = "custom,v:lua.vim.lsp._complete_client", desc = "Stop active LSP clients", nargs = "?" })

  create_lsp_command("LspStart", function(command)
    if command.args ~= "" and type(vim.lsp.enable) == "function" then
      pcall(vim.lsp.enable, command.args)
    end

    vim.cmd("edit")
  end, { desc = "Start or refresh LSP clients for the current buffer", nargs = "?" })

  create_lsp_command("LspRestart", function(command)
    vim.cmd("LspStop " .. command.args)
    vim.defer_fn(function()
      vim.cmd("LspStart " .. command.args)
    end, 100)
  end, { desc = "Restart LSP clients for the current buffer", nargs = "?" })
end

return {
  {
    "williamboman/mason.nvim",
    cmd = "Mason",
    opts = {
      ui = {
        border = "rounded",
      },
    },
  },
  {
    "williamboman/mason-lspconfig.nvim",
    ft = lang.filetypes(),
    dependencies = {
      "williamboman/mason.nvim",
    },
    opts = {
      ensure_installed = {},
      automatic_installation = false,
    },
  },
  {
    "neovim/nvim-lspconfig",
    cmd = {
      "LspInfo",
      "LspLog",
      "LspRestart",
      "LspStart",
      "LspStop",
    },
    ft = lang.filetypes(),
    dependencies = {
      "williamboman/mason.nvim",
      "williamboman/mason-lspconfig.nvim",
      "hrsh7th/cmp-nvim-lsp",
    },
    config = function()
      vim.diagnostic.config({
        severity_sort = true,
        underline = true,
        update_in_insert = false,
        virtual_text = {
          spacing = 2,
          source = "if_many",
        },
        float = {
          border = "rounded",
          source = "if_many",
        },
        signs = true,
      })

      local ok_mason, mason = pcall(require, "mason")
      if ok_mason then
        mason.setup({
          ui = {
            border = "rounded",
          },
        })
      end

      local ok_mason_lspconfig, mason_lspconfig = pcall(require, "mason-lspconfig")
      if ok_mason_lspconfig then
        mason_lspconfig.setup({
          ensure_installed = {},
          automatic_installation = false,
        })
      end

      setup_lsp_commands()

      local use_native_lsp_config = native_lsp_config_available()
      local lspconfig = nil
      local server_registry = function()
        return true
      end

      if not use_native_lsp_config then
        local ok_lspconfig
        ok_lspconfig, lspconfig = pcall(require, "lspconfig")
        if not ok_lspconfig then
          return
        end
        server_registry = lspconfig
      end

      local capabilities = vim.lsp.protocol.make_client_capabilities()
      local ok_cmp_lsp, cmp_lsp = pcall(require, "cmp_nvim_lsp")
      if ok_cmp_lsp then
        capabilities = cmp_lsp.default_capabilities(capabilities)
      end

      vim.api.nvim_create_autocmd("LspAttach", {
        group = vim.api.nvim_create_augroup("DotfilesNvimLsp", { clear = true }),
        desc = "Configure buffer-local LSP keymaps",
        callback = function(event)
          local map = function(mode, lhs, rhs, desc)
            vim.keymap.set(mode, lhs, rhs, { buffer = event.buf, desc = desc })
          end

          map("n", "gd", vim.lsp.buf.definition, "Go to definition")
          map("n", "gD", vim.lsp.buf.declaration, "Go to declaration")
          map("n", "gr", vim.lsp.buf.references, "List references")
          map("n", "gi", vim.lsp.buf.implementation, "Go to implementation")
          map("n", "K", vim.lsp.buf.hover, "LSP hover")
          map("n", "<leader>rn", vim.lsp.buf.rename, "Rename symbol")
          map("n", "<leader>ca", vim.lsp.buf.code_action, "Code action")
          map("n", "<leader>cd", vim.diagnostic.open_float, "Line diagnostics")
          map("n", "[d", vim.diagnostic.goto_prev, "Previous diagnostic")
          map("n", "]d", vim.diagnostic.goto_next, "Next diagnostic")
        end,
      })

      for server_name, server_config in pairs(lang.lsp_servers(server_registry)) do
        if server_enabled(server_config) then
          if use_native_lsp_config then
            setup_native_lsp(server_name, server_config, capabilities)
          else
            setup_legacy_lsp(lspconfig, server_name, server_config, capabilities)
          end
        end
      end
    end,
  },
}
