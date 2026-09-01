-- ============================================================================
-- NOTA DE INSTALACIÓN PARA OTRAS PC (WINDOWS):
-- WezTerm en Windows lee la configuración desde C:\Users\<TuUsuario>\.wezterm.lua
-- Para usar este archivo de dotfiles dentro de WSL, debes crear el archivo
-- proxy en Windows con el siguiente contenido:
--[[
local config_file = "\\\\wsl.localhost\\Ubuntu\\home\\bruno\\.dotfiles\\os\\windows\\.wezterm.lua"
local f = io.open(config_file, "r")
if f then
  f:close()
  return dofile(config_file)
else
  local wezterm = require 'wezterm'
  local config = wezterm.config_builder()
  config.color_scheme = 'Batman'
  return config
end
--]]
-- ============================================================================

-- Importar la API de WezTerm
local wezterm = require 'wezterm'

local startup_done = false

wezterm.on('gui-startup', function(cmd)
  if os.getenv("STARTUP_WORKSPACES") == "1" then
    if not startup_done then
      startup_done = true

      local HOME = '/home/bruno'
      local CP  = HOME .. '/Code/personal/hungry/compare-prices'
      local VC   = HOME .. '/Code/personal/voice-assistant'
      local SIS  = HOME .. '/Code/Work/Stratesys/Clece/sis'
      -- opencode web via the portable dotfiles launcher (~/.local/bin/opencode-web,
      -- dotbot-linked from ai/opencode-router/bin/opencode-web.sh; exports OPENCODE_CONFIG
      -- + PATH for the routed sdd-explore binary). Rollback: 'opencode web'.
      local OC_WEB = HOME .. '/.local/bin/opencode-web'

      -- 1. Spawn the default window WezTerm expects to avoid phantom windows
      local default_tab, default_pane, win = wezterm.mux.spawn_window(cmd or {})
      local window_gui = win:gui_window()

      local function wsl_args(cwd, cmd_str)
        return {
          domain = { DomainName = 'WSL:Ubuntu' },
          args = {'zsh', '-ic', 'cd ' .. cwd .. ' && ' .. cmd_str .. '; exec zsh'}
        }
      end

      local function make_2x2(cwd, cmds, title)
        local spawn_cmd = wsl_args(cwd, cmds[1])
        -- Todas las tabs se agregan a la ventana 'win' existente
        local tab = win:spawn_tab({
          domain = spawn_cmd.domain,
          args = spawn_cmd.args
        })
        if title then tab:set_title(title) end
        local pane_a = tab:panes()[1]

        local spawn_cmd_c = wsl_args(cwd, cmds[3])
        window_gui:perform_action(wezterm.action.SplitPane{
          direction = 'Down', command = { domain = spawn_cmd_c.domain, args = spawn_cmd_c.args }
        }, pane_a)
        local pane_c = tab:active_pane()

        local spawn_cmd_d = wsl_args(cwd, cmds[4])
        window_gui:perform_action(wezterm.action.SplitPane{
          direction = 'Right', command = { domain = spawn_cmd_d.domain, args = spawn_cmd_d.args }
        }, pane_c)
        local pane_d = tab:active_pane()

        window_gui:perform_action(wezterm.action.ActivatePaneDirection('Up'), pane_d)
        local pane_a_again = tab:active_pane()

        local spawn_cmd_b = wsl_args(cwd, cmds[2])
        window_gui:perform_action(wezterm.action.SplitPane{
          direction = 'Right', command = { domain = spawn_cmd_b.domain, args = spawn_cmd_b.args }
        }, pane_a_again)
      end

      local function make_2x1(cwd, cmds, title)
        local spawn_cmd = wsl_args(cwd, cmds[1])
        local tab = win:spawn_tab({
          domain = spawn_cmd.domain,
          args = spawn_cmd.args
        })
        if title then tab:set_title(title) end
        local pane = tab:panes()[1]

        local spawn_cmd2 = wsl_args(cwd, cmds[2])
        window_gui:perform_action(wezterm.action.SplitPane{
          direction = 'Right', command = { domain = spawn_cmd2.domain, args = spawn_cmd2.args }
        }, pane)
      end

      local function get_oc_cmd(idx)
        local jq_idx = idx - 1
        local sleep_time = jq_idx % 4
        return string.format(
          "sleep %d; export CODEGRAPH_MCP_LOG_ATTACH=1 SID=$(opencode session list --format json -n 12 2>/dev/null | jq -r '.[%d].id'); if [ -n \"$SID\" ] && [ \"$SID\" != \"null\" ]; then opencode -s \"$SID\"; else opencode; fi",
          sleep_time, jq_idx
        )
      end

      local function staggered_oc()
        return {'sleep 0; opencode', 'sleep 1; opencode', 'sleep 2; opencode', 'sleep 3; opencode'}
      end

      -- make_2x2(CP, { get_oc_cmd(1), get_oc_cmd(2), get_oc_cmd(3), get_oc_cmd(4) }, "CP 1-4")
      -- make_2x2(CP, { get_oc_cmd(5), get_oc_cmd(6), get_oc_cmd(7), get_oc_cmd(8) }, "CP 5-8")
      -- make_2x2(CP, { get_oc_cmd(9), get_oc_cmd(10), get_oc_cmd(11), get_oc_cmd(12) }, "CP 9-12")

      -- make_2x2(VC, { get_oc_cmd(1), get_oc_cmd(2), get_oc_cmd(3), get_oc_cmd(4) }, "Voice Assistant")
      -- make_2x2(SIS, { get_oc_cmd(1), get_oc_cmd(2), get_oc_cmd(3), get_oc_cmd(4) }, "SIS")

      make_2x2(CP, {
        OC_WEB .. ' --hostname 0.0.0.0 --port 4096',
        'bun run scripts/mobile-proxy.ts',
        'bun run scripts/copilot-backend.ts',
        'gentle-ai',
      }, "AI Tools")

      make_2x1(VC, {
         'htop',
         'cd ~ && agy',
        --  'claude',
         './deploy_electron_to_windows.sh',
         './run_dev.sh'
    }, "VC Windows Deploy")

      -- 3. Cerrar la pestaña default inicial enviando un 'exit' para dejar solo nuestras 7 pestañas
      default_pane:send_text("exit\r")
    end

    return
  end

  local tab, pane, window = wezterm.mux.spawn_window(cmd or {})
end)

local config = wezterm.config_builder()
config.default_prog = { 'wsl.exe', '~', '-e', 'zsh', '-i' }
config.color_scheme = 'Tokyo Night'
config.font = wezterm.font('Hack NF')
config.font_size = 13.5
config.scrollback_lines = 10000
config.hide_tab_bar_if_only_one_tab = true
config.default_cursor_style = 'BlinkingBar'
config.cursor_blink_rate = 500
config.audible_bell = "Disabled"
config.window_decorations = "RESIZE"
config.window_background_opacity = 0.95

config.keys = {
  { key = 'c', mods = 'CTRL|SHIFT', action = wezterm.action.CopyTo 'Clipboard' },
  { key = 'v', mods = 'CTRL|SHIFT', action = wezterm.action.PasteFrom 'Clipboard' },
  { key = 'w', mods = 'CTRL', action = wezterm.action.CloseCurrentPane { confirm = true } },
  { key = 'd', mods = 'CTRL|SHIFT', action = wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' } },
  { key = 'Enter', mods = 'CTRL|SHIFT', action = wezterm.action.SplitVertical { domain = 'CurrentPaneDomain' } },
  { key = 'LeftArrow', mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Left' },
  { key = 'RightArrow', mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Right' },
  { key = 'UpArrow', mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Up' },
  { key = 'DownArrow', mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Down' },
  { key = 'z', mods = 'CTRL|SHIFT', action = wezterm.action.TogglePaneZoomState },
  { key = 'q', mods = 'CTRL|SHIFT', action = wezterm.action.QuickSelect },
  { key = 'f', mods = 'CTRL|SHIFT', action = wezterm.action.Search 'CurrentSelectionOrEmptyString' },
  { key = 'x', mods = 'CTRL|SHIFT', action = wezterm.action.ActivateCopyMode },
  { key = 'PageUp', mods = 'CTRL|SHIFT', action = wezterm.action.ScrollToPrompt(-1) },
  { key = 'PageDown', mods = 'CTRL|SHIFT', action = wezterm.action.ScrollToPrompt(1) },
  -- Intercepta Ctrl + Z y no ejecuta ninguna acción
  { key = 'z', mods = 'CTRL', action = wezterm.action.DisableDefaultAssignment },
}

table.insert(config.keys, {
  key = 'v',
  mods = 'ALT',
  action = wezterm.action_callback(function(window, pane)
    local ps_script = [[
      Add-Type -AssemblyName System.Windows.Forms;
      $img = [System.Windows.Forms.Clipboard]::GetImage();
      if ($img) {
          $dir = "$env:USERPROFILE\Screenshots";
          if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
          $filename = "screenshot_$(Get-Date -Format 'yyyyMMdd_HHmmss').png";
          $winPath = Join-Path $dir $filename;
          $img.Save($winPath);
          $drive = $winPath.Substring(0,1).ToLower();
          $rest = $winPath.Substring(3).Replace('\', '/');
          Write-Output "/mnt/$drive/$rest";
      } else {
          exit 1
      }
    ]]
    local success, stdout, stderr = wezterm.run_child_process({
      "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script
    })
    if success then
      local wsl_path = stdout:gsub("[\r\n]", "")
      pane:send_text(wsl_path .. " ")
    else
      window:toast_notification("WezTerm", "No hay imagen en el portapapeles", nil, 4000)
    end
  end),
})

-- AI Helper: asistente de terminal vía LiteLLM (Alt+I)
local ai_helper = wezterm.plugin.require("https://github.com/Michal1993r/ai-helper.wezterm")
ai_helper.apply_to_config(config, {
  type = "http",
  api_url = "http://127.0.0.1:4000/v1/chat/completions",
  api_key = os.getenv("LITELLM_MASTER_KEY") or "sk-test-key-12345",
  model = "qwen-3.6-27b",
  keybinding = { key = "i", mods = "ALT" },
  system_prompt = "You are a CLI assistant. Be brief. Print commands in copy-pasteable format. Chain with && when useful.",
  timeout = 30,
  show_loading = true,
})

return config