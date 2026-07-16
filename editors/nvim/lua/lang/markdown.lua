return {
  filetypes = { "markdown", "markdown.mdx" },
  parsers = { "markdown", "markdown_inline" },
  servers = {
    marksman = {
      filetypes = { "markdown", "markdown.mdx" },
      root_markers = { ".marksman.toml", ".git" },
      single_file_support = true,
    },
  },
}
