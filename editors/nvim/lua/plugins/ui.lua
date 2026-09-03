local features = require("config.features")
local tree_filetype = features.legacy and "NvimTree" or "neo-tree"

return {
  {
    "folke/which-key.nvim",
    event = "VeryLazy",
    opts = {
      delay = 400,
      preset = "modern",
    },
  },
  {
    "akinsho/bufferline.nvim",
    event = "VeryLazy",
    cond = not features.legacy,
    dependencies = {
      "nvim-tree/nvim-web-devicons",
    },
    opts = {
      options = {
        close_command = "confirm bdelete %d",
        right_mouse_command = "confirm bdelete %d",
        offsets = {
          {
            filetype = tree_filetype,
            text = "Explorer",
            text_align = "left",
            highlight = "Directory",
            separator = true,
          },
        },
      },
    },
  },
  {
    "nvim-lualine/lualine.nvim",
    event = "VeryLazy",
    dependencies = {
      "nvim-tree/nvim-web-devicons",
    },
    opts = {
      options = {
        component_separators = "",
        section_separators = "",
        disabled_filetypes = {
          statusline = { tree_filetype, "lazy" },
        },
        globalstatus = true,
        theme = "auto",
      },
      sections = {
        lualine_a = { "mode" },
        lualine_b = { "branch" },
        lualine_c = {
          {
            "filename",
            path = 1,
          },
        },
        lualine_x = { "filetype" },
        lualine_y = { "progress" },
        lualine_z = { "location" },
      },
      inactive_sections = {
        lualine_a = {},
        lualine_b = {},
        lualine_c = { "filename" },
        lualine_x = { "location" },
        lualine_y = {},
        lualine_z = {},
      },
    },
  },
}
