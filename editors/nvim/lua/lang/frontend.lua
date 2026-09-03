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
      names = { "vtsls", "ts_ls", "tsserver" },
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
        settings = {
          vtsls = {
            autoUseWorkspaceTsdk = true,
          },
          typescript = {
            updateImportsOnFileMove = "always",
            suggest = {
              completeFunctionCalls = true,
            },
            inlayHints = {
              parameterNames = { enabled = "all" },
              parameterTypes = { enabled = true },
              variableTypes = { enabled = true },
              propertyDeclarationTypes = { enabled = true },
              functionLikeReturnType = { enabled = true },
              enumMemberValues = { enabled = true },
            },
          },
        },
      },
    },
  },
}
