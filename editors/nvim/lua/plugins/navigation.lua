return {
  {
    "nvim-tree/nvim-tree.lua",
    cmd = {
      "NvimTreeClose",
      "NvimTreeFindFile",
      "NvimTreeFocus",
      "NvimTreeOpen",
      "NvimTreeToggle",
    },
    keys = {
      { "<leader>e", "<cmd>NvimTreeToggle<CR>", desc = "Toggle file tree" },
      { "<leader>E", "<cmd>NvimTreeFindFile<CR>", desc = "Reveal current file in tree" },
    },
    dependencies = {
      "nvim-tree/nvim-web-devicons",
    },
    init = function()
      vim.g.loaded_netrw = 1
      vim.g.loaded_netrwPlugin = 1
    end,
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
  {
    "ibhagwan/fzf-lua",
    cmd = "FzfLua",
    keys = {
      { "<leader>ff", "<cmd>FzfLua files<CR>", desc = "Find files" },
      { "<leader>fg", "<cmd>FzfLua live_grep<CR>", desc = "Search text" },
      { "<leader>fb", "<cmd>FzfLua buffers<CR>", desc = "Find buffers" },
      { "<leader>fs", "<cmd>FzfLua lsp_document_symbols<CR>", desc = "Document symbols" },
      { "<leader>fS", "<cmd>FzfLua lsp_workspace_symbols<CR>", desc = "Workspace symbols" },
    },
    dependencies = {
      "nvim-tree/nvim-web-devicons",
    },
    opts = {
      winopts = {
        height = 0.85,
        width = 0.85,
        preview = {
          layout = "flex",
        },
      },
      files = {
        cwd_prompt = false,
        git_icons = true,
        file_icons = true,
      },
      grep = {
        rg_opts = "--column --line-number --no-heading --color=always --smart-case --max-columns=4096 -e",
      },
    },
  },
}
