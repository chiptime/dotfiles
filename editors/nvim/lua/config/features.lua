local version = vim.version()
local supported = version.major > 0 or version.minor >= 11
local legacy

if not supported then
  legacy = true

  if not vim.g.dotfiles_ide_layout_unsupported_version_notified then
    vim.g.dotfiles_ide_layout_unsupported_version_notified = true
    vim.notify(
      string.format(
        "The new IDE layout requires Neovim 0.11+ (detected %d.%d.%d). Start the normal 0.12 installation or correct your PATH before retrying.",
        version.major,
        version.minor,
        version.patch
      ),
      vim.log.levels.WARN,
      { title = "Neovim IDE layout", once = true }
    )
  end
else
  legacy = vim.env.NVIM_IDE_LAYOUT == "0"
end

local decision = {
  legacy = legacy,
}

return setmetatable({}, {
  __index = decision,
  __newindex = function()
    error("config.features route decision is immutable")
  end,
})
