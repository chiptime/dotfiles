@echo off
setlocal

:: Levanta WezTerm con STARTUP_WORKSPACES=1: el handler gui-startup de
:: .wezterm.lua construye los layouts (mux + WSL:Ubuntu). La logica de los
:: layouts vive en Lua, no aqui.
::
:: Source of truth: ~/.dotfiles/os/windows/startup/ (dotfiles).
:: Despliegue: copia CRLF en C:\Users\<user>\Desktop\ y acceso directo
:: startup-workspaces-wezterm.lnk en shell:startup (arranque al login).

set "WEZTERM_EXE=%ProgramFiles%\WezTerm\wezterm-gui.exe"
if not exist "%WEZTERM_EXE%" set "WEZTERM_EXE=wezterm-gui"

set STARTUP_WORKSPACES=1
start "" "%WEZTERM_EXE%"
