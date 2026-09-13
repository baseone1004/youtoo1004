Option Explicit
Dim shell, fso, folder, part, pythonw, candidate, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = ""
For Each part In Split(shell.ExpandEnvironmentStrings("%PATH%"), ";")
    candidate = fso.BuildPath(part, "pythonw.exe")
    If fso.FileExists(candidate) Then
        pythonw = candidate
        Exit For
    End If
Next
If pythonw = "" Then
    MsgBox "Python was not found. Install Python and enable Add python to PATH.", 16, "YouTube automation"
    WScript.Quit 1
End If
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & fso.BuildPath(folder, "launcher.py") & Chr(34) & " --background"
shell.Run command, 0, False
