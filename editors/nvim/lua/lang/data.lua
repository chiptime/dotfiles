return {
  filetypes = { "json", "jsonc", "yaml", "toml" },
  parsers = { "json", "jsonc", "yaml", "toml" },
  servers = {
    jsonls = {
      filetypes = { "json", "jsonc" },
      root_markers = {
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        ".git",
      },
      single_file_support = true,
    },
    yamlls = {
      filetypes = { "yaml", "yaml.docker-compose", "yaml.gitlab" },
      root_markers = {
        ".yamllint",
        ".yamllint.yaml",
        ".yamllint.yml",
        ".git",
      },
      single_file_support = true,
      settings = {
        yaml = {
          keyOrdering = false,
        },
      },
    },
    taplo = {
      filetypes = { "toml" },
      root_markers = { "taplo.toml", ".taplo.toml", "pyproject.toml", ".git" },
      single_file_support = true,
    },
  },
}
