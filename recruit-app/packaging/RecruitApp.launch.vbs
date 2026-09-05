' RecruitApp 桌面启动器（spec §4.6）：后端已在跑(/health 通) → 直接开网页；
' 不在 → 隐藏窗口拉起 run.bat 并轮询最长 ~20s → 再开网页；失败给明确提示，不静默。
Option Explicit
Dim fso, appDir, sh, i
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName) & "\"
sh.CurrentDirectory = appDir
If Not IsHealthUp() Then
    sh.Run """" & appDir & "run.bat""", 0, False
    For i = 1 To 40          ' 200ms × 40 = 最长 20s（uvicorn 冷启动余量）
        WScript.Sleep 200
        If IsHealthUp() Then Exit For
    Next
End If
If IsHealthUp() Then
    sh.Run "http://127.0.0.1:8000", 1, False
Else
    MsgBox "招聘系统启动失败：8000 端口可能被其它程序占用。" & vbCrLf & _
           "请关闭占用 8000 端口的程序后，重新双击桌面的『招聘系统』。", 48, "招聘系统"
End If

Function IsHealthUp()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.open "GET", "http://127.0.0.1:8000/health", False
    http.send
    IsHealthUp = (http.status = 200)
    On Error GoTo 0
End Function
