return {
  filetypes = { "cds" },
  parsers = { "cds" },
  servers = {
    cds_lsp = {
      filetypes = { "cds" },
      root_markers = { "package.json", "db", "srv", ".git" },
      single_file_support = true,
      settings = {
        cds = {
          validate = true,
        },
      },
    },
  },
}


