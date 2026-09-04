# Windows 原生直启验收清单（阶段 2，spec §6 items 3/4）

日期：2026-09-04 · 机器：开发机 Windows 侧（.venv-win 直启，不打包）

## 准备
- [ ] `.venv-win` 就绪（Task 8 Step 1 记录的命令完成）；npm 构建过 `frontend\dist`（Task 7）
- [ ] 数据隔离：本次全部落点 = 临时目录（不碰仓库 data/）：
      PowerShell: `$env:RECRUIT_DATA_DIR = "D:\AI应用\hr\hr\recruit-app\data-win-check"`

## 启动与静态托管
- [ ] 后端起（PowerShell，仓库根）：
      `.venv-win\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
- [ ] 浏览器开 `http://127.0.0.1:8000` → 页面正常（同源 dist，无跨域报错）
- [ ] 直接开深层路由 `http://127.0.0.1:8000/settings` → 仍出页面（history 兜底生效）

## 设置保存 / 平台态
- [ ] `GET /settings` 里 `platform_native = true`（浏览器 F12 Network 或后端冒烟）
- [ ] 原生按钮组出现：「打开浏览器登录 / 立即导入 / 测试连接」；WSL 专属「liepin login」文案不出现

## 登录导入主路径（§4.5）—— spec §6 第 3 项
- [ ] 设置 http/https 代理 = 本机科学上网端口（如 127.0.0.1:7890）并保存
- [ ] 点「打开浏览器登录」→ 弹出**独立窗口**（非日常浏览器窗口），地址为猎聘登录页
- [ ] 核对弹窗命令行含 `--user-data-dir=...\data-win-check\login-profile` 与
      `--remote-debugging-port=53471`（任务管理器 → 命令行）
- [ ] 在弹出窗口**手动**登录企业版账号（输账号密码/扫码；系统无任何自动代填）
- [ ] 约 15-25 秒后 toast「Cookie 已自动导入并保存」（或点「立即导入」立即生效）
- [ ] 再点「立即导入」→ 指纹相同返回「会话与库内一致」（不重复写库）
- [ ] 点「测试连接」（attach 开关保持关闭）→ 成功（默认 chromium 静默引擎）
- [ ] 粘贴 Cookie 兜底路径照旧可用（清掉已导入值 → 粘贴 → 测试连接成功）

## attach 复用登录浏览器（§4.4/§4.5）
- [ ] 登录浏览器窗口保持打开 → 设置开「在登录的浏览器里开着页面操作」→ 测试连接
      → 页面以标签页开进该窗口、成功
- [ ] 手动关掉登录浏览器窗口 → 测试连接 → 明确报错提示「先在设置页点『打开浏览器登录』」
- [ ] 重新点「打开浏览器登录」→ 新窗口正常、可再登录导入
- [ ] 登录浏览器窗口打开期间，任务管理器无任何被强杀的 Chrome/msedge 记录；日常浏览器不受影响

## 拉取岗位与 WSL 结果一致（spec §6 第 4 项）
- [ ] 拉取岗位 1 页，关键字段与 WSL 版同账号结果一致（job 标题/公司/薪资区间）
- [ ] 失败路径（断网/端口占用等）报错为中文可读信息，不裸抛堆栈

## 收尾
- [ ] 停后端 → 确认 login-profile 浏览器进程被回收（lifespan close_login_browser；仅精确 PID）
- [ ] 删 `data-win-check` 临时目录（验证完不留残余）
