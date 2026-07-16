local lang = require("lang")

local function supported_parsers(parser_names)
  local ok, parsers = pcall(require, "nvim-treesitter.parsers")
  if not ok then
    return parser_names
  end

  local parser_configs = parsers.get_parser_configs()
  local supported = {}

  for _, parser_name in ipairs(parser_names) do
    if parser_configs[parser_name] then
      table.insert(supported, parser_name)
    end
  end

  return supported
end

return {
  {
    "nvim-treesitter/nvim-treesitter",
    commit = "979beffc1a86e7ba19bd6535c0370d8e1aaaad3c",
    cmd = {
      "TSInstall",
      "TSInstallInfo",
      "TSModuleInfo",
      "TSUpdate",
    },
    ft = lang.filetypes(),
    opts = function()
      return {
        ensure_installed = supported_parsers(lang.treesitter_parsers()),
        sync_install = false,
        auto_install = false,
        highlight = {
          enable = true,
          additional_vim_regex_highlighting = { "markdown" },
        },
        indent = {
          enable = true,
          disable = { "yaml" },
        },
      }
    end,
    config = function(_, opts)
      require("nvim-treesitter.configs").setup(opts)
    end,
  },
}
