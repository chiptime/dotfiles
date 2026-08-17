local features = require("config.features")
local new_route = not features.legacy

return {
  {
    "nvim-neo-tree/neo-tree.nvim",
    cond = new_route,
    cmd = new_route and "Neotree" or nil,
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-tree/nvim-web-devicons",
      { "MunifTanjim/nui.nvim", cond = new_route },
    },
  },
  {
    "MagicDuck/grug-far.nvim",
    cond = new_route,
    cmd = new_route and "GrugFar" or nil,
  },
  {
    "NeogitOrg/neogit",
    cond = new_route,
    cmd = new_route and "Neogit" or nil,
  },
  {
    "tpope/vim-fugitive",
    cond = new_route,
    cmd = new_route and {
      "Git",
      "G",
      "Gdiffsplit",
      "Gvdiffsplit",
      "Gwrite",
    } or nil,
  },
  {
    "rbong/vim-flog",
    cond = new_route,
    cmd = new_route and "Flog" or nil,
    dependencies = {
      "tpope/vim-fugitive",
    },
  },
  {
    "mfussenegger/nvim-dap",
    cond = new_route,
    cmd = new_route and {
      "DapContinue",
      "DapStepInto",
      "DapStepOut",
      "DapStepOver",
      "DapTerminate",
      "DapToggleBreakpoint",
    } or nil,
  },
  {
    "rcarriga/nvim-dap-ui",
    cond = new_route,
    lazy = true,
    dependencies = {
      "mfussenegger/nvim-dap",
      { "nvim-neotest/nvim-nio", cond = new_route },
    },
  },
  {
    "mxsdev/nvim-dap-vscode-js",
    cond = new_route,
    lazy = true,
    dependencies = {
      "mfussenegger/nvim-dap",
    },
  },
  {
    "mfussenegger/nvim-dap-python",
    cond = new_route,
    lazy = true,
    dependencies = {
      "mfussenegger/nvim-dap",
    },
  },
}
