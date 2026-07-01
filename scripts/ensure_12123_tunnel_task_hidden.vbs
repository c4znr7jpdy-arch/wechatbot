Set shell = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\Project\scripts\ensure_12123_tunnel_task.ps1"" -TaskName ""Jiang12123ReverseTunnel"" -HealthUrl ""https://xiuxianjyj.xin/health"""
shell.Run cmd, 0, True
