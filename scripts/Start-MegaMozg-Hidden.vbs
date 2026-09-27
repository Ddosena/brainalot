Option Explicit

Dim shell, files, scriptFolder, projectFolder, launcher, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
scriptFolder = files.GetParentFolderName(WScript.ScriptFullName)
projectFolder = files.GetParentFolderName(scriptFolder)
launcher = files.BuildPath(scriptFolder, "Launch-MegaMozg-Hidden.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & launcher & """"
shell.Run command, 0, False
