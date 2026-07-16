local function powershell_editor_services_bundle()
  for _, name in ipairs({ "PSES_BUNDLE_PATH", "POWERSHELL_EDITOR_SERVICES_BUNDLE_PATH" }) do
    local value = os.getenv(name)
    if value and value ~= "" then
      return value
    end
  end

  return nil
end

return {
  filetypes = { "ps1" },
  parsers = { "powershell" },
  servers = {
    powershell_es = {
      bundle_path = powershell_editor_services_bundle(),
      condition = function(config)
        return type(config.bundle_path) == "string" and config.bundle_path ~= ""
      end,
      filetypes = { "ps1" },
      root_markers = { "PSScriptAnalyzerSettings.psd1", ".git" },
      single_file_support = true,
    },
  },
}


