' ClipScribe - abre o servidor sem janela de terminal.
' Uma instancia so: se o app ja estiver no ar na porta 5000, apenas abre o navegador.

Dim shell, fso, python, scriptDir, http, jaNoAr

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
python = scriptDir & "\.venv\Scripts\python.exe"

Set shell = CreateObject("WScript.Shell")

If Not fso.FileExists(python) Then
  MsgBox "O ClipScribe ainda nao foi instalado." & vbCrLf & "Rode o INSTALAR.bat que esta na pasta:" & vbCrLf & scriptDir, 48, "ClipScribe"
  WScript.Quit
End If

' 1. A porta 5000 ja responde como ClipScribe?
jaNoAr = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1000, 1000, 2000, 2000
http.open "GET", "http://localhost:5000/__ping", False
http.send
If Err.Number = 0 And http.Status = 200 Then
  If InStr(http.responseText, "clipscribe") > 0 Then jaNoAr = True
End If
Err.Clear
On Error GoTo 0

If jaNoAr Then
  shell.Run "http://localhost:5000", 1, False
  WScript.Quit
End If

' 2. Porta presa por processo travado: mata antes de subir
shell.Run "cmd /c for /f ""tokens=5"" %p in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill /f /pid %p", 0, True
WScript.Sleep 1500

' 3. Sobe o servidor
shell.Run "cmd /c cd /d """ & scriptDir & """ && """ & python & """ app.py", 0, False

WScript.Sleep 5000
shell.Run "http://localhost:5000", 1, False
