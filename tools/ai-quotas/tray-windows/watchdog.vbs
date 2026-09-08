' Runs the watchdog batch with a hidden window (0) so no cmd.exe flashes.
CreateObject("WScript.Shell").Run """C:\Users\Bruno\ai-quotas-tray\watchdog.cmd""", 0, False
