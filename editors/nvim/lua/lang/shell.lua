return {
  filetypes = { "sh", "bash", "zsh", "fish" },
  parsers = { "bash", "fish" },
  servers = {
    bashls = {
      filetypes = { "sh", "bash", "zsh" },
      root_markers = { ".git", ".shellcheckrc", "shellcheckrc" },
      single_file_support = true,
    },
  },
}
