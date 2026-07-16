return {
  filetypes = { "python" },
  parsers = { "python" },
  server_choices = {
    {
      names = { "basedpyright", "pyright" },
      config = {
        filetypes = { "python" },
        root_markers = {
          "pyrightconfig.json",
          "pyproject.toml",
          "setup.py",
          "setup.cfg",
          "requirements.txt",
          "Pipfile",
          ".git",
        },
        single_file_support = true,
      },
    },
  },
}


