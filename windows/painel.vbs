' ---------------------------------------------------------------------------
' Abre o painel de controlo do assistente.
'
' Faça um atalho para este ficheiro no Ambiente de Trabalho: passa a ligar,
' desligar e ver a consola do bot com um duplo clique, sem linha de comandos.
' ---------------------------------------------------------------------------

Dim shell, fso, pastaProjeto, pythonw, painel

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

pastaProjeto = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pythonw = pastaProjeto & "\.venv\Scripts\pythonw.exe"
painel = pastaProjeto & "\windows\painel.pyw"

If Not fso.FileExists(pythonw) Then
    MsgBox "Nao encontrei o ambiente virtual em:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Crie-o com:  python -m venv .venv", vbCritical, "Assistente"
    WScript.Quit 1
End If

shell.CurrentDirectory = pastaProjeto

' 1 = janela normal (o painel tem interface); False = nao esperar pelo fim.
shell.Run """" & pythonw & """ """ & painel & """", 1, False
