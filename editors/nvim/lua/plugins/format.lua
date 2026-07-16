local lang = require("lang")

local format_on_save_filetypes = {
  bash = true,
  fish = true,
  lua = true,
  sh = true,
  toml = true,
  zsh = true,
}

local prettier_config_files = {
  ".prettierrc",
  ".prettierrc.cjs",
  ".prettierrc.js",
  ".prettierrc.json",
  ".prettierrc.json5",
  ".prettierrc.mjs",
  ".prettierrc.toml",
  ".prettierrc.yaml",
  ".prettierrc.yml",
  "prettier.config.cjs",
  "prettier.config.js",
  "prettier.config.mjs",
}

local biome_config_files = {
  "biome.json",
  "biome.jsonc",
}

local function buffer_name(bufnr)
  local name = vim.api.nvim_buf_get_name(bufnr or 0)
  if name == "" then
    return vim.loop.cwd()
  end

  return name
end

local function find_upward(names, path)
  local start = path
  if start == "" then
    start = vim.loop.cwd()
  end

  local found = vim.fs.find(names, {
    path = start,
    upward = true,
    stop = vim.loop.os_homedir(),
  })

  return found[1]
end

local function has_project_file(ctx, names)
  return find_upward(names, ctx.filename or buffer_name(ctx.buf)) ~= nil
end

local function local_executable(ctx, executable)
  local file = ctx.filename or buffer_name(ctx.buf)
  local dir = vim.fn.fnamemodify(file, ":p:h")
  local home = vim.loop.os_homedir()

  while dir and dir ~= "" and dir ~= home and dir ~= "/" do
    local candidate = dir .. "/node_modules/.bin/" .. executable
    if vim.fn.executable(candidate) == 1 then
      return candidate
    end

    local parent = vim.fn.fnamemodify(dir, ":h")
    if parent == dir then
      break
    end
    dir = parent
  end

  return nil
end

local function has_local_executable(ctx, executable)
  return local_executable(ctx, executable) ~= nil
end

local function project_formatter(util, executable, config_files)
  local formatter = {
    cwd = util.root_file(config_files),
    require_cwd = true,
    condition = function(_, ctx)
      return has_project_file(ctx, config_files) and has_local_executable(ctx, executable)
    end,
  }

  if util.from_node_modules then
    formatter.command = util.from_node_modules(executable)
  end

  return formatter
end

local function formatter_status(conform, bufnr)
  local ok_configured, configured = pcall(conform.list_formatters_for_buffer, bufnr)
  if not ok_configured then
    configured = {}
  end

  local ok, formatters = pcall(conform.list_formatters, bufnr)
  if not ok then
    return {}, configured
  end

  local available = {}
  local available_lookup = {}
  local unavailable = {}

  for _, formatter in ipairs(formatters) do
    if formatter.available ~= false then
      table.insert(available, formatter.name)
      available_lookup[formatter.name] = true
    else
      table.insert(unavailable, formatter.name)
    end
  end

  for _, name in ipairs(configured) do
    if not available_lookup[name] and not vim.tbl_contains(unavailable, name) then
      table.insert(unavailable, name)
    end
  end

  return available, unavailable
end

local function has_available_formatter(conform, bufnr)
  local available = formatter_status(conform, bufnr)
  return #available > 0
end

local function format_buffer(conform, bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()

  if not has_available_formatter(conform, bufnr) then
    vim.notify(
      "No available formatter for filetype " .. vim.bo[bufnr].filetype,
      vim.log.levels.INFO,
      { title = "Format" }
    )
    return
  end

  conform.format({
    bufnr = bufnr,
    async = true,
    timeout_ms = 2000,
    lsp_format = "never",
  })
end

local function format_info(conform, bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  local available, unavailable = formatter_status(conform, bufnr)
  local filetype = vim.bo[bufnr].filetype
  local auto = format_on_save_filetypes[filetype] == true and "enabled" or "manual-only"

  print("format filetype=" .. filetype .. " auto=" .. auto)
  print("available=" .. (#available > 0 and table.concat(available, ",") or "none"))
  print("unavailable=" .. (#unavailable > 0 and table.concat(unavailable, ",") or "none"))
end

return {
  {
    "stevearc/conform.nvim",
    branch = "nvim-0.9",
    commit = "4e97712607bfdcadc097823339599e5bf05f97f9",
    cmd = {
      "ConformInfo",
      "Format",
      "FormatInfo",
    },
    ft = lang.filetypes(),
    event = "BufWritePre",
    keys = {
      {
        "<leader>cf",
        function()
          format_buffer(require("conform"))
        end,
        desc = "Format buffer",
      },
      {
        "<leader>cF",
        function()
          format_info(require("conform"))
        end,
        desc = "Format policy info",
      },
    },
    opts = function()
      local util = require("conform.util")

      return {
        notify_on_error = true,
        notify_no_formatters = false,
        formatters_by_ft = {
          lua = { "stylua" },
          sh = { "shfmt" },
          bash = { "shfmt" },
          zsh = { "shfmt" },
          fish = { "fish_indent" },
          json = { "biome", "prettier" },
          jsonc = { "biome", "prettier" },
          yaml = { "prettier" },
          toml = { "taplo" },
          markdown = { "prettier" },
          ["markdown.mdx"] = { "prettier" },
          python = { "ruff_format" },
          javascript = { "biome", "prettier" },
          javascriptreact = { "biome", "prettier" },
          typescript = { "biome", "prettier" },
          typescriptreact = { "biome", "prettier" },
          html = { "biome", "prettier" },
          css = { "biome", "prettier" },
          scss = { "biome", "prettier" },
        },
        formatters = {
          biome = project_formatter(util, "biome", biome_config_files),
          prettier = project_formatter(util, "prettier", prettier_config_files),
        },
        format_on_save = function(bufnr)
          if not format_on_save_filetypes[vim.bo[bufnr].filetype] then
            return nil
          end

          local conform = require("conform")
          if not has_available_formatter(conform, bufnr) then
            return nil
          end

          return {
            timeout_ms = 1200,
            lsp_format = "never",
          }
        end,
      }
    end,
    config = function(_, opts)
      local conform = require("conform")
      conform.setup(opts)

      vim.api.nvim_create_user_command("Format", function()
        format_buffer(conform)
      end, { desc = "Format current buffer with the configured formatter" })

      vim.api.nvim_create_user_command("FormatInfo", function()
        format_info(conform)
      end, { desc = "Show formatter availability for the current buffer" })
    end,
  },
}
