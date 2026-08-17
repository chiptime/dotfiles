local features = require("config.features")
local legacy = features.legacy

return {
  {
    "nvim-tree/nvim-tree.lua",
    cond = legacy,
    cmd = legacy and {
      "NvimTreeClose",
      "NvimTreeFindFile",
      "NvimTreeFocus",
      "NvimTreeOpen",
      "NvimTreeToggle",
    } or nil,
    keys = legacy and {
      { "<leader>e", "<cmd>NvimTreeToggle<CR>", desc = "Toggle file tree" },
      { "<leader>E", "<cmd>NvimTreeFindFile<CR>", desc = "Reveal current file in tree" },
    } or nil,
    dependencies = {
      "nvim-tree/nvim-web-devicons",
    },
    init = legacy and function()
      vim.g.loaded_netrw = 1
      vim.g.loaded_netrwPlugin = 1
    end or nil,
    opts = {
      disable_netrw = true,
      hijack_netrw = false,
      hijack_directories = {
        enable = false,
      },
      sync_root_with_cwd = true,
      view = {
        width = 34,
        signcolumn = "yes",
      },
      renderer = {
        group_empty = true,
        highlight_git = true,
        icons = {
          show = {
            file = true,
            folder = true,
            folder_arrow = true,
            git = true,
          },
        },
      },
      filters = {
        git_ignored = false,
      },
      git = {
        enable = true,
        ignore = false,
      },
      update_focused_file = {
        enable = true,
        update_root = false,
      },
      actions = {
        open_file = {
          quit_on_open = false,
          window_picker = {
            enable = true,
          },
        },
      },
    },
  },
}
