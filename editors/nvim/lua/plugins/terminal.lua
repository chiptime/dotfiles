local features = require("config.features")

return {
  {
    "akinsho/toggleterm.nvim",
    cond = not features.legacy,
    cmd = { "ToggleTerm", "TermSelect" },
    opts = {
      size = 12,
      direction = "horizontal",
    },
  },
}
