' ---------------------------------------------------------------------------
' Arranca o assistente sem qualquer janela visivel.
'
' Coloque um atalho para este ficheiro na pasta de arranque do Windows
' (tecla Windows + R, escrever  shell:startup ) para que o bot arranque
' sozinho sempre que iniciar sessao.
'
' Como nao ha janela, os registos vao para o ficheiro assistente.log,
' na pasta do projeto.
' ---------------------------------------------------------------------------

Dim shell, fso, pastaProjeto, pythonw

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Pasta do projeto = pasta acima desta (windows\..)
pastaProjeto = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

' pythonw.exe corre Python sem abrir consola.
pythonw = pastaProjeto & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "Nao encontrei o ambiente virtual em:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Crie-o com:  python -m venv .venv", vbCritical, "Assistente"
    WScript.Quit 1
End If

If Not fso.FileExists(pastaProjeto & "\.env") Then
    MsgBox "Falta o ficheiro .env com as credenciais em:" & vbCrLf & pastaProjeto, _
           vbCritical, "Assistente"
    WScript.Quit 1
End If

' Sem consola, o ficheiro de registo e a unica forma de diagnosticar problemas.
shell.Environment("PROCESS")("LOG_FILE") = pastaProjeto & "\assistente.log"

shell.CurrentDirectory = pastaProjeto

' 0 = janela oculta; False = nao esperar pelo fim do processo.
shell.Run """" & pythonw & """ main.py", 0, False
