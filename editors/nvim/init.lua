vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

if vim.loader then
  vim.loader.enable()
end

require("config.options")
require("config.filetypes")
require("config.keymaps")
require("config.autocmds")
require("config.terminal")
require("config.lazy")
