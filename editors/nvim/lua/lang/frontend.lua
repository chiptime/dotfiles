return {
  filetypes = {
    "css",
    "html",
    "javascript",
    "javascriptreact",
    "json",
    "scss",
    "typescript",
    "typescriptreact",
  },
  parsers = {
    "css",
    "html",
    "javascript",
    "jsdoc",
    "scss",
    "tsx",
    "typescript",
  },
  servers = {
    cssls = {
      filetypes = { "css", "scss" },
      root_markers = { "package.json", ".git" },
      single_file_support = true,
    },
    html = {
      filetypes = { "html" },
      root_markers = { "package.json", ".git" },
      single_file_support = true,
    },
  },
  server_choices = {
    {
      names = { "ts_ls", "tsserver" },
      config = {
        filetypes = {
          "javascript",
          "javascriptreact",
          "typescript",
          "typescriptreact",
        },
        root_markers = {
          "tsconfig.json",
          "jsconfig.json",
          "package.json",
          "deno.json",
          "deno.jsonc",
          "vite.config.js",
          "vite.config.ts",
          "next.config.js",
          "next.config.mjs",
        },
        single_file_support = false,
      },
    },
  },
}
