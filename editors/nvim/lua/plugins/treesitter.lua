local lang = require("lang")

-- nvim-treesitter (main branch) removed the legacy configs.setup API:
-- highlighting is now started by nvim core (vim.treesitter.start), parser
-- installation goes through require("nvim-treesitter").install. This spec
-- keeps the dotfiles contract: parsers come only from the lang registry
-- (no implicit per-filetype installs) and highlighting attaches lazily on
-- the registry filetypes.

local function supported_parsers(parser_names)
  local ok, nts = pcall(require, "nvim-treesitter")
  if not ok then
    return parser_names
  end

  local available = {}
  for _, name in ipairs(nts.get_available()) do
    available[name] = true
  end

  local supported = {}
  for _, parser_name in ipairs(parser_names) do
    if available[parser_name] then
      supported[#supported + 1] = parser_name
    end
  end
  return supported
end

return {
  {
    "nvim-treesitter/nvim-treesitter",
    cmd = {
      "TSInstall",
      "TSInstallFromGrammar",
      "TSUpdate",
      "TSUninstall",
    },
    ft = lang.filetypes(),
    config = function()
      local nts = require("nvim-treesitter")

      -- Explicit-list install only (registry parsers); nothing installs
      -- implicitly for unlisted filetypes.
      local wanted = supported_parsers(lang.treesitter_parsers())
      local installed = {}
      for _, name in ipairs(nts.get_installed()) do
        installed[name] = true
      end
      local missing = {}
      for _, name in ipairs(wanted) do
        if not installed[name] then
          missing[#missing + 1] = name
        end
      end
      if #missing > 0 then
        nts.install(missing)
      end

      local filetypes = {}
      for _, ft in ipairs(lang.filetypes()) do
        filetypes[ft] = true
      end

      vim.api.nvim_create_autocmd("FileType", {
        group = vim.api.nvim_create_augroup("DotfilesTreesitter", { clear = true }),
        desc = "Start core treesitter highlighting for registry filetypes",
        callback = function(args)
          if not filetypes[args.match] then
            return
          end
          pcall(vim.treesitter.start, args.buf)
          if args.match ~= "yaml" then
            vim.bo[args.buf].indentexpr = "v:lua.require'nvim-treesitter'.indentexpr()"
          end
        end,
      })
    end,
  },
}
