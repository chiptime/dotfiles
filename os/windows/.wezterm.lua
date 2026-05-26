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

wezterm.on('gui-startup', function(cmd)
  -- Si lanzamos wezterm con esta variable de entorno, abrimos el layout completo
  if os.getenv("STARTUP_WORKSPACES") == "1" then
    local tab, pane, window = wezterm.mux.spawn_window({
      args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx compare-prices'}
    })
    window:spawn_tab { args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx compare-prices-2'} }
    window:spawn_tab { args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx compare-prices-3'} }
    window:spawn_tab { args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx voice'} }
    window:spawn_tab { args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx sis'} }
    window:spawn_tab { args = {'wsl.exe', '-d', 'Ubuntu', '-e', 'zsh', '-ic', 'tx tools'} }
    return
  end

  -- Comportamiento por defecto (cuando lo abrís normal)
  local tab, pane, window = wezterm.mux.spawn_window(cmd or {})
end)

-- Crear el objeto de configuración (esto te da autocompletado si usas un buen editor)
local config = wezterm.config_builder()
---------------------------------------------------------------
-- 1. SISTEMA OPERATIVO Y SHELL
---------------------------------------------------------------
-- Forzamos a WezTerm a que arranque directamente dentro de WSL usando ZSH
-- El flag -i asegura modo interactivo (zshrc siempre se carga)
config.default_prog = { 'wsl.exe', '~', '-e', 'zsh', '-i' }
---------------------------------------------------------------
-- 2. APARIENCIA BÁSICA (Grado Corporativo)
---------------------------------------------------------------
-- Esquema de colores integrado (tiene cientos, este es un clásico)
config.color_scheme = 'Tokyo Night'
-- Tipografía: Asegurate de tener una fuente Nerd Font instalada en Windows
-- Si no tenés JetBrains, cambiala por 'Consolas' o 'Cascadia Code'
config.font = wezterm.font('Hack NF')
config.font_size = 13.5
-- Cuántas líneas guardar de scrollback (build logs largos, etc.)
config.scrollback_lines = 10000

--------------------------------------------------------------
-- 3. INTERFAZ MINIMALISTA
--------------------------------------------------------------
-- Ocultar barra de pestañas si hay solo una (ideal si usas Zellij/Tmux)
config.hide_tab_bar_if_only_one_tab = true
-- Cursor barra parpadeante, más fácil de ubicar en paredes de texto
config.default_cursor_style = 'BlinkingBar'
config.cursor_blink_rate = 500
-- Campana visual en vez de pitido molesto
config.audible_bell = "Disabled"
-- Nota: El bracketed_paste_mode se maneja desde el shell (Bash/Zsh), no desde wezterm.
config.visual_bell = {
  fade_in_duration_ms = 75,
  fade_out_duration_ms = 75,
  target = "BackgroundColor",
}
-- Quitar los bordes feos de Windows 11 para que se vea nativo
config.window_decorations = "RESIZE"
-- Opacidad ligera para ver el fondo (opcional, ponelo en 1.0 para sólido)
config.window_background_opacity = 0.95
---------------------------------------------------------------
-- 4. ATAJOS DE TECLADO BÁSICOS Y LÓGICA CUSTOM
---------------------------------------------------------------
config.disable_default_key_bindings = false
config.keys = {
  -- Copiar al portapapeles
  { key = 'c',     mods = 'CTRL|SHIFT', action = wezterm.action.CopyTo 'Clipboard' },

  -- Pegar directo usando la acción nativa de WezTerm
  { key = 'v',     mods = 'CTRL|SHIFT', action = wezterm.action.PasteFrom 'Clipboard' },

  -- Zoom de fuente (útil para presentar, compartir pantalla, o vista cansada)
  { key = '=',     mods = 'CTRL',       action = wezterm.action.IncreaseFontSize },
  { key = '-',     mods = 'CTRL',       action = wezterm.action.DecreaseFontSize },
  { key = '0',     mods = 'CTRL',       action = wezterm.action.ResetFontSize },

  -- Cerrar panel/tab (muscle memory de navegador)
  { key = 'w',     mods = 'CTRL',       action = wezterm.action.CloseCurrentPane { confirm = true } },

  -- Nueva pestaña WSL
  { key = 't',     mods = 'CTRL|SHIFT', action = wezterm.action.SpawnTab 'CurrentPaneDomain' },

  -- Navegación entre pestañas
  { key = 'Tab',   mods = 'CTRL',       action = wezterm.action.ActivateTabRelative(1) },
  { key = 'Tab',   mods = 'CTRL|SHIFT', action = wezterm.action.ActivateTabRelative(-1) },

  -- Split pane horizontal (hereda CWD vía --cd de wsl.exe)
  { key = 'd',     mods = 'CTRL|SHIFT', action = wezterm.action_callback(function(window, pane)
    local cwd_url = pane:get_current_working_dir()
    local cwd = cwd_url and cwd_url.file_path or nil
    if cwd then
      wezterm.log_info("split-cwd", cwd)
      window:perform_action(
        wezterm.action.SplitHorizontal {
          args = {'wsl.exe', '--cd', cwd, '-e', 'zsh', '-i'},
        },
        pane
      )
    else
      window:perform_action(wezterm.action.SplitHorizontal { domain = 'CurrentPaneDomain' }, pane)
    end
  end)},
  -- Split pane vertical (hereda CWD vía --cd de wsl.exe)
  { key = 'Enter', mods = 'CTRL|SHIFT', action = wezterm.action_callback(function(window, pane)
    local cwd_url = pane:get_current_working_dir()
    local cwd = cwd_url and cwd_url.file_path or nil
    if cwd then
      wezterm.log_info("split-cwd", cwd)
      window:perform_action(
        wezterm.action.SplitVertical {
          args = {'wsl.exe', '--cd', cwd, '-e', 'zsh', '-i'},
        },
        pane
      )
    else
      window:perform_action(wezterm.action.SplitVertical { domain = 'CurrentPaneDomain' }, pane)
    end
  end)},

  -- Moverse entre panes con Ctrl+Shift + flechas
  { key = 'LeftArrow',  mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Left' },
  { key = 'RightArrow', mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Right' },
  { key = 'UpArrow',    mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Up' },
  { key = 'DownArrow',  mods = 'CTRL|SHIFT', action = wezterm.action.ActivatePaneDirection 'Down' },

  -- Zoom pane actual (fullscreen temporal), mismo atajo que Tmux
  { key = 'z',     mods = 'CTRL|SHIFT', action = wezterm.action.TogglePaneZoomState },

  -- Quick Select: etiqueta cada URL/path/git-SHA con una letra. Apretás la letra y se copia.
  { key = 'Space', mods = 'CTRL|SHIFT', action = wezterm.action.QuickSelect },

  -- Buscar en todo el scrollback con regex (sin mouse, sin seleccionar)
  { key = 'f',     mods = 'CTRL|SHIFT', action = wezterm.action.Search 'CurrentSelectionOrEmptyString' },

  -- Copy Mode: entrás al scrollback como si fuera Vim (/ buscar, v seleccionar, y copiar)
  { key = 'x',     mods = 'CTRL|SHIFT', action = wezterm.action.ActivateCopyMode },

  -- Scroll to Prompt (requiere Shell Integration)
  { key = 'PageUp',   mods = 'CTRL|SHIFT', action = wezterm.action.ScrollToPrompt(-1) },
  { key = 'PageDown', mods = 'CTRL|SHIFT', action = wezterm.action.ScrollToPrompt(1) },
}
-- INYECCIÓN: Lógica para pegar imágenes con Alt + V
table.insert(config.keys, {
  key = 'v',
  mods = 'ALT',
  action = wezterm.action_callback(function(window, pane)
    -- Script de PowerShell que lee el portapapeles, guarda el PNG y devuelve la ruta WSL
    local ps_script = [[
      Add-Type -AssemblyName System.Windows.Forms;
      $img = [System.Windows.Forms.Clipboard]::GetImage();
      if ($img) {
          $dir = "$env:USERPROFILE\Screenshots";
          if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
          $filename = "screenshot_$(Get-Date -Format 'yyyyMMdd_HHmmss').png";
          $winPath = Join-Path $dir $filename;
          $img.Save($winPath);

          # Convertir ruta Windows (C:\...) a ruta WSL (/mnt/c/...) rapidísimo
          $drive = $winPath.Substring(0,1).ToLower();
          $rest = $winPath.Substring(3).Replace('\', '/');
          Write-Output "/mnt/$drive/$rest";
      } else {
          exit 1
      }
    ]]
    -- Ejecutamos PowerShell directo desde WezTerm (Host)
    local success, stdout, stderr = wezterm.run_child_process({
      "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script
    })
    if success then
      -- Si hubo imagen, limpiamos el salto de línea que escupe PowerShell
      local wsl_path = stdout:gsub("[\r\n]", "")

      -- Inyectamos el texto simulando que lo tipeaste en el teclado
      pane:send_text(wsl_path .. " ")
    else
      -- Si no hay imagen, te lo avisamos con una notificación nativa de WezTerm
      window:toast_notification("WezTerm", "No hay imagen en el portapapeles de Windows", nil, 4000)
    end
  end),
})
return config