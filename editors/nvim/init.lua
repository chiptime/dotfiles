vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

if vim.loader then
  vim.loader.enable()
end

require("config.options")
require("config.filetypes")
require("config.keymaps")
require("config.autocmds")

local features = require("config.features")

if features.legacy then
  require("config.terminal_legacy")
else
  require("config.terminal")

  if vim.fn.filereadable(vim.fn.stdpath("config") .. "/lua/config/ide.lua") == 1 then
    require("config.ide")
  end
end

require("config.lazy")
