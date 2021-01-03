:: ####################################################################################################################################
   @echo off
   color 04
   title Add Open With WSL VS Code to Context Window Options

:: ####################################################################################################################################
   set "scriptLocation=D:\Bruno\wsl\dotfiles\.dotfiles\os\windows\vscode\open with wsl vscode.vbs"
   set "VscoIcon=C:/Program Files/Microsoft VS Code/Code.exe"
   set "registryKeyName=WSL VSCode"
   set "ContextMenuCommandName=Open with WSL VSCode"

:: ####################################################################################################################################
   set "OpenSpecifiedFolderKey=HKCR\Directory\shell\%registryKeyName%"
   set "OpenSpecifiedFolderKeyValue= "\"%windir%\system32\wscript.exe\" \"%scriptLocation%\" "\"%%1\""

   REG ADD "%OpenSpecifiedFolderKey%" /v "Icon" /t REG_EXPAND_SZ /d "%VscoIcon%"
   REG ADD "%OpenSpecifiedFolderKey%" /ve /t REG_EXPAND_SZ /d "%ContextMenuCommandName%"
   REG ADD "%OpenSpecifiedFolderKey%\command" /ve /t REG_EXPAND_SZ /d %OpenSpecifiedFolderKeyValue%

:: ####################################################################################################################################
   set "OpenCurrentFolderKey=HKCR\Directory\Background\shell\%registryKeyName%"
   set "OpenCurrentFolderKeyValue= "\"%windir%\system32\wscript.exe\" \"%scriptLocation%\" "\"%%V\""

   REG ADD "%OpenCurrentFolderKey%" /v "Icon" /t REG_EXPAND_SZ /d "%VscoIcon%"
   REG ADD "%OpenCurrentFolderKey%" /ve /t REG_EXPAND_SZ /d "%ContextMenuCommandName%"
   REG ADD "%OpenCurrentFolderKey%\command" /ve /t REG_EXPAND_SZ /d %OpenCurrentFolderKeyValue%

:: ####################################################################################################################################
   pause>nul
   exit /b
   
:: ####################################################################################################################################
