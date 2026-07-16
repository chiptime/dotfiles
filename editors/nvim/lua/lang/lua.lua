return {
  filetypes = { "lua" },
  parsers = { "lua", "luadoc", "luap" },
  servers = {
    lua_ls = {
      root_markers = {
        ".luarc.json",
        ".luarc.jsonc",
        ".stylua.toml",
        "stylua.toml",
        ".git",
      },
      single_file_support = true,
      settings = {
        Lua = {
          completion = {
            callSnippet = "Replace",
          },
          diagnostics = {
            globals = { "vim" },
          },
          runtime = {
            version = "LuaJIT",
          },
          telemetry = {
            enable = false,
          },
          workspace = {
            checkThirdParty = false,
          },
        },
      },
    },
  },
}
