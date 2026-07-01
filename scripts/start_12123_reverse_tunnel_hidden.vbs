Set shell = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\Project\scripts\start_12123_reverse_tunnel.ps1"" -Server ""47.242.208.64"" -User ""root"" -RemotePort 18789 -LocalPort 8789"
shell.Run cmd, 0, True
