local lang = require("lang")

local linters_by_ft = {
  lua = { "selene", "luacheck" },
  sh = { "shellcheck" },
  bash = { "shellcheck" },
  zsh = { "shellcheck" },
  fish = { "fish" },
  markdown = { "markdownlint" },
  ["markdown.mdx"] = { "markdownlint" },
  yaml = { "yamllint" },
  json = { "jsonlint" },
  jsonc = { "jsonlint" },
  toml = { "taplo" },
  python = { "ruff" },
}

local frontend_filetypes = {
  css = true,
  html = true,
  javascript = true,
  javascriptreact = true,
  scss = true,
  typescript = true,
  typescriptreact = true,
}

local eslint_config_files = {
  ".eslintrc",
  ".eslintrc.cjs",
  ".eslintrc.js",
  ".eslintrc.json",
  ".eslintrc.yaml",
  ".eslintrc.yml",
  "eslint.config.cjs",
  "eslint.config.js",
  "eslint.config.mjs",
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

local function has_project_file(bufnr, names)
  return find_upward(names, buffer_name(bufnr)) ~= nil
end

local function local_executable(bufnr, executable)
  local dir = vim.fn.fnamemodify(buffer_name(bufnr), ":p:h")
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

local function copy(values)
  return vim.list_extend({}, values or {})
end

local function executable_command(command)
  if type(command) == "function" then
    local ok, value = pcall(command)
    if not ok then
      return false
    end
    command = value
  end

  if type(command) ~= "string" or command == "" then
    return true
  end

  return vim.fn.executable(command) == 1
end

local function add_frontend_linters(lint, bufnr, names)
  if not frontend_filetypes[vim.bo[bufnr].filetype] then
    return
  end

  if has_project_file(bufnr, biome_config_files) and lint.linters.biomejs then
    local biome = local_executable(bufnr, "biome")
    if biome then
      lint.linters.biomejs.cmd = biome
      table.insert(names, "biomejs")
    end
  end

  if has_project_file(bufnr, eslint_config_files) then
    local eslint = local_executable(bufnr, "eslint")
    if eslint and lint.linters.eslint then
      lint.linters.eslint.cmd = eslint
      table.insert(names, "eslint")
    end
  end
end

local function configured_linters(lint, bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  local names = copy(linters_by_ft[vim.bo[bufnr].filetype])
  add_frontend_linters(lint, bufnr, names)
  return names
end

local function available_linters(lint, bufnr)
  local available = {}
  local unavailable = {}

  for _, name in ipairs(configured_linters(lint, bufnr)) do
    local linter = lint.linters[name]
    if linter and executable_command(linter.cmd) then
      table.insert(available, name)
    else
      table.insert(unavailable, name)
    end
  end

  return available, unavailable
end

local function try_lint(lint, bufnr, opts)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  opts = opts or {}

  if vim.bo[bufnr].buftype ~= "" then
    return
  end

  local available, unavailable = available_linters(lint, bufnr)
  if #available == 0 then
    if opts.notify then
      local suffix = #unavailable > 0 and (" Missing optional: " .. table.concat(unavailable, ",")) or ""
      vim.notify(
        "No available linter for filetype " .. vim.bo[bufnr].filetype .. "." .. suffix,
        vim.log.levels.INFO,
        { title = "Lint" }
      )
    end
    return
  end

  lint.try_lint(available, { bufnr = bufnr })
end

local function lint_info(lint, bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  local available, unavailable = available_linters(lint, bufnr)
  local configured = configured_linters(lint, bufnr)

  print("lint filetype=" .. vim.bo[bufnr].filetype .. " events=BufWritePost,InsertLeave")
  print("configured=" .. (#configured > 0 and table.concat(configured, ",") or "none"))
  print("available=" .. (#available > 0 and table.concat(available, ",") or "none"))
  print("unavailable=" .. (#unavailable > 0 and table.concat(unavailable, ",") or "none"))
end

local function ensure_custom_linters(lint)
  if not lint.linters.taplo then
    local parser = require("lint.parser")

    lint.linters.taplo = {
      cmd = "taplo",
      stdin = false,
      args = {
        "check",
        "--colors",
        "never",
        "--format",
        "short",
      },
      append_fname = true,
      stream = "stderr",
      ignore_exitcode = true,
      parser = parser.from_errorformat("%f:%l:%c: %m", {
        source = "taplo",
        severity = vim.diagnostic.severity.ERROR,
      }),
    }
  end
end

return {
  {
    "mfussenegger/nvim-lint",
    cmd = {
      "Lint",
      "LintInfo",
    },
    ft = lang.filetypes(),
    keys = {
      {
        "<leader>cl",
        function()
          try_lint(require("lint"), nil, { notify = true })
        end,
        desc = "Lint buffer",
      },
      {
        "<leader>cL",
        function()
          lint_info(require("lint"))
        end,
        desc = "Lint policy info",
      },
    },
    config = function()
      local lint = require("lint")
      ensure_custom_linters(lint)
      lint.linters_by_ft = linters_by_ft

      vim.api.nvim_create_user_command("Lint", function()
        try_lint(lint, nil, { notify = true })
      end, { desc = "Run scoped linting for the current buffer" })

      vim.api.nvim_create_user_command("LintInfo", function()
        lint_info(lint)
      end, { desc = "Show linter availability for the current buffer" })

      vim.api.nvim_create_autocmd({ "BufWritePost", "InsertLeave" }, {
        group = vim.api.nvim_create_augroup("DotfilesNvimLint", { clear = true }),
        desc = "Run scoped linting for supported buffers",
        callback = function(event)
          try_lint(lint, event.buf, { notify = false })
        end,
      })
    end,
  },
}
