Set shell = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\Project\scripts\start_short_drama_reverse_tunnel.ps1"""
shell.Run cmd, 0, True
