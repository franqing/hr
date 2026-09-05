# 单机版安装包构建（spec §4.8）—— 全程在开发机 **Windows 侧**执行

前置：Task 8 的 `.venv-win` 已就绪（3.12 与开发机同版本 = 最稳路线：便携 Python = 官方
python 安装目录精简 + 从 .venv-win 拷贝 site-packages 覆盖，见 §4.8「首选」路线）。

## 1) 前端产物
```powershell
cd D:\AI应用\hr\hr\recruit-app\frontend
npm run build          # → frontend\dist
```

## 2) 组 stage（PowerShell，仓库根下）
```powershell
$stage = "D:\AI应用\hr\hr\recruit-app\packaging\stage"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$stage\app" | Out-Null

# 便携 Python：官方安装根（python.exe/pythonw.exe/DLLs/Lib 等）全量拷，
# 再把 .venv-win 的 site-packages 覆盖进去（同版本、已含全部离线依赖 + playwright 包）
# 从 .venv-win 反查真实基础 Python 安装根（与运行版同版本同根，最可靠）；
# 别用 (Get-Command py) 推导——py launcher 常解析到 C:\Windows\py.exe，不是安装根
$pyRoot = & "D:\AI应用\hr\hr\recruit-app\.venv-win\Scripts\python.exe" -c "import sys; print(sys.base_prefix)"
robocopy "$pyRoot" "$stage\app\python" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS
robocopy "D:\AI应用\hr\hr\recruit-app\.venv-win\Lib\site-packages" "$stage\app\python\Lib\site-packages" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS

# playwright 浏览器：Windows 侧 playwright install chromium 产物整包搬入
# （chromium-*\chrome-win = 登录可见窗口用；chromium_headless_shell-* = 无头任务引擎）
robocopy "$env:LOCALAPPDATA\ms-playwright" "$stage\app\browsers" /E /NFL /NDL /NJH /NJS /NC /NS

# 后端代码（含 liepin/win/*.mjs —— WSL 回归基线随包保留，原生路径永不调用）
robocopy "D:\AI应用\hr\hr\recruit-app\backend" "$stage\app\backend" /E /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS

# 前端产物 → web\（run.bat 的 RECRUIT_WEB_DIR 指向这里）
robocopy "D:\AI应用\hr\hr\recruit-app\frontend\dist" "$stage\app\web" /E /NFL /NDL /NJH /NJS /NC /NS

# 启动器
Copy-Item "D:\AI应用\hr\hr\recruit-app\packaging\run.bat" "$stage\app\"
Copy-Item "D:\AI应用\hr\hr\recruit-app\packaging\RecruitApp.launch.vbs" "$stage\app\"
```

核对清单（缺一不可）：
- [ ] `stage\app\python\pythonw.exe` 存在；`python\Lib\site-packages\playwright\__init__.py` 存在
- [ ] `stage\app\browsers\chromium-*\chrome-win\chrome.exe` 存在（headed 登录窗口用）
- [ ] `stage\app\web\index.html` 存在；`stage\app\backend\main.py` 存在
- [ ] `stage\app\python\Lib\site-packages` 里无 node.exe / liepin-cli（BOM 不含，§4.8）

## 3) 构建安装包
Inno Setup 6（ISCC.exe 在 PATH 或写全路径）：
```powershell
cd D:\AI应用\hr\hr\recruit-app\packaging
ISCC.exe install.iss          # → packaging\output\recruit-app-setup-0.9.0.exe（约 450MB）
```

版本升级：只改 `install.iss` 顶部 `MyAppVersion` 再跑 ISCC。

## 4) 干净目录安装自测（spec §6 全过才交付试点）
按 `docs/windows-native-check-2026-09-04.md` 的清单在**已装机器**上换 `%LOCALAPPDATA%\RecruitApp`
实路径逐项执行 + Task 9 Step 5 清单，结果回填文档。
