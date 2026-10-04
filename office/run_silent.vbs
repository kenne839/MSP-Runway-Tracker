' Silent Windows background launcher for KMSP Runway Tracker Office Bridge
' Runs pythonw.exe completely hidden (no console window).

Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(currentDir)

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = projectRoot

cmd = "pythonw.exe -m office.bridge"
WshShell.Run cmd, 0, False
