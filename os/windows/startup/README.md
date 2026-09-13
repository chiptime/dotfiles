# Windows startup scripts

Scripts are born here (dotfiles source of truth) and deployed as **copies** to
Windows paths — DrvFs (`/mnt/c`) does not support WSL symlinks. Keep CRLF line
endings in `.bat` files.

## startup-workspaces-wezterm.bat

Launches WezTerm with `STARTUP_WORKSPACES=1`; the `gui-startup` handler in
`../.wezterm.lua` then builds the 2x2/2x1 layouts via mux + `WSL:Ubuntu`.

| Target | Path |
| --- | --- |
| Deployed copy | `C:\Users\Bruno\Desktop\startup-workspaces-wezterm.bat` |
| Login autostart | `startup-workspaces-wezterm.lnk` in `shell:startup` (minimized, targets the Desktop copy) |
| Pinnable launcher | `C:\Users\Bruno\Desktop\Start Work WezTerm.lnk` (cmd.exe wrapper + wezterm-gui icon, minimized; same pattern as the tmux `Start Work.lnk`) |

After editing here, re-deploy the Desktop copy (CRLF):

```sh
sed -i 's/$/\r/' startup-workspaces-wezterm.bat
cp startup-workspaces-wezterm.bat /mnt/c/Users/Bruno/Desktop/
```

Recreate the login shortcut (the `.lnk` itself is binary, not versioned):

```powershell
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\startup-workspaces-wezterm.lnk")
$lnk.TargetPath = "$env:USERPROFILE\Desktop\startup-workspaces-wezterm.bat"
$lnk.WorkingDirectory = "$env:USERPROFILE\Desktop"
$lnk.WindowStyle = 7
$lnk.Save()
```
