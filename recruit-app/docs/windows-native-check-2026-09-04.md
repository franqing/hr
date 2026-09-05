# Windows 原生直启验收清单（阶段 2，spec §6 items 3/4）

日期：2026-09-05 执行 · 机器：开发机 Windows 侧（.venv-win 直启，不打包）
结果：勾选态见各条；执行记录与遗留备注见文末。

## 准备
- [x] `.venv-win` 就绪；npm 已构建 `frontend\dist`（`/` 与 `/search` 返回 dist index.html）
- [x] 数据隔离：全部落点 = `data-win-check`（临时目录，`RECRUIT_DATA_DIR` 注入，不碰仓库 data/）
      （验证后已删，见收尾）

## 启动与静态托管
- [x] 后端起（.venv-win，仓库根，`RECRUIT_DATA_DIR=data-win-check`）：
      `.venv-win\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` → `/health` ok
- [x] 浏览器开 `http://127.0.0.1:8000` → HTTP 200，`/` 重定向 `/search`，页面正常渲染；
      headless 浏览器打开控制台 **0 条 error**（无跨域报错）
- [x] 深层路由兜底（history 兜底生效）：`/search`、任意未知路径（`/no-such-route`）→ 均回 `index.html`
- [!] 清单原项 `/settings` 直接访问返回的是 **API JSON**（`GET /settings` 后端路由优先于 SPA 兜底），
      非"仍出页面"。SPA 内跳转 `/settings` 正常（前端路由）。凡与 GET API 同名的 SPA 路径
      （settings/runs/profiles/candidates/:id/invites）均无法直接深链刷新——**既有问题，非本阶段回归**，
      日常经启动器打开根路径进入不受影响。详见文末备注 3。

## 设置保存 / 平台态
- [x] `GET /settings` 返回 `platform_native = true`（`os.name=='nt'` 判定，多次 GET/PUT 往返确认）
- [x] 原生按钮组出现：「打开浏览器登录 / 立即导入 / 测试连接」；
      WSL 专属「从 liepin login 导入 / 在登录的 Chrome 里」文案不出现（headless DOM 断言 PASS）

## 登录导入主路径（§4.5）—— spec §6 第 3 项
- [x] 代理设置并保存：**按用户决定保持留空**（本机可直连猎聘 BFF，无需代理端口）；
      http/https_proxy PUT/GET 往返均通过
- [x] 点「打开浏览器登录」→ 弹出**独立专用窗口**（独立 profile），地址为猎聘登录/首页；
      本机按用户偏好以 Edge（`browser_executable=msedge.exe`）打开；默认捆绑 chromium 亦验证可用
- [x] 弹窗命令行含 `--user-data-dir=D:\...\data-win-check\login-profile` 与
      `--remote-debugging-port`（本机实际 9222：53471 落在排除段被 bind 探测判忙 → 按设计回退 9222，
      任务管理器命令行核对一致）
- [x] 在弹出窗口**手动**登录企业版账号（本机登录态已在专用 profile 内，重开直接进入；
      全程系统无任何自动代填/自动登录——代码只打开登录页）
- [x] 登录后自动导入生效：Cookie 库 mtime 变化后 15s 轮询触发，`audit settings.import source=auto` 落库成功
      （此前被 `account.checkin`(103160306) 拦截，见备注 1）
- [x] 再点「立即导入」→ `{ok:true, changed:false}` 指纹相同「会话与库内一致」，不重复写库
- [x] 点「测试连接」（attach 关）→ 成功（默认 chromium 静默引擎 / Edge 静默引擎均验证通过）
- [x] 粘贴 Cookie 兜底路径：清空已导入值 → 粘贴（同会话头）→ 测试连接成功

## attach 复用登录浏览器（§4.4/§4.5）
- [x] 登录窗口开着 + attach 开 → 测试连接成功（`门户与 BFF 会话探测均通过`），
      页面以标签页开进该窗口（`lpt.liepin.com/search`）
- [x] 登录浏览器不在运行 → 测试连接 → 明确报错：
      「登录浏览器没在运行。请先在设置页点『打开浏览器登录』并手动完成猎聘登录…」
      （注意：Edge 开"后台继续运行"时关窗口未必退进程，见备注 2）
- [x] 重新点「打开浏览器登录」→ 新窗口正常（Edge），再次导入 `{ok:true, changed:false}` 通过
- [x] 全程无 `taskkill /IM` 强杀记录：仅精确 PID 回收 app 自拉登录实例（重启/收尾各一次）；
      日常浏览器不受影响

## 拉取岗位与 WSL 结果一致（spec §6 第 4 项）
- [x] 拉取岗位 1 页，Windows 原生与 WSL 版同账号结果**逐条一致**：
      `研发负责人 / 85336907 / jobKind=2`、`AI应用工程师 / 85334465 / jobKind=2`
      （方法：把当前会话同步至 WSL repo-data settings 后，由 WSL 后端 :8001 拉取对照，见备注 4）
- [x] 失败路径中文可读、不裸抛堆栈（实测：`浏览器可执行文件不存在: ...`；
      attach 未开登录窗错误、登录态 -1701 提示等均为中文引导文案）

## 收尾
- [x] 停后端 → login-profile 登录浏览器进程被回收（lifespan `close_login_browser` 语义；本次由后端进程退出 +
      精确 PID 回收完成，仅 app 自拉实例）
- [x] 删 `data-win-check` 临时目录（验证完不留残余）

## 执行记录与备注
1. **导入曾持续失败的根因 = 猎聘账号级 IM checkin 门（非代码缺陷）**：`get-job-chat-list`（IM 沟通 BFF）
   返回 `flag=0 code=103160306`，msg 指向 `api-passport account.checkin` 跳转页。连**已登录的官方窗口**用
   标准请求头调同一接口同样返回该码 → 判定为账号需先完成一次平台安全验证。在登录浏览器打开官方 IM 页
   `lpt.liepin.com/chat/im` 一次后解除；随后导出 → 重放 → 门户+BFF 双验证 → 落库全链路通过。
   若同事机器首登后测试连接/导入报 103160306，需在其登录窗口完成该平台验证（或提示打开 IM 页）。
2. **Edge 后台常驻影响错误路径测试**：本机 Edge 设置"关闭窗口后继续运行后台应用"，手动关登录窗口后
   CDP/进程仍在 → attach 仍可复用（表现为仍成功）。真实"没在运行"态需进程退出；本验收以精确 PID 结束
   app 自拉实例验证了该错误分支，UI 提示正确。
3. **深层路由撞名局限**（前文 [!]）：`GET /settings`、`GET /runs`、`GET /profiles`、`GET /candidates/{id}`、
   `GET /invites` 等后端路由会遮蔽同名 SPA 路径的直接访问（返回 JSON）；非 API 深链（/search、未知路径）
   兜底正常。SPA 内导航不受影响。建议后续单独立项（如前端路由改 hash 或后端全量 /api 前缀）解决，非本
   阶段阻塞项——WSL 版行为一致、非 Windows 原生回归。
4. **WSL 对照方法**：原生拉取前 WSL repo-data 会话已被本次原生登录顶掉（-1701，单会话正常行为）；
   将当前有效会话刷新进 repo-data settings（仅改 cookie，未动任何运行数据）后，WSL :8001 拉取结果与原生
   逐条一致。
5. 数据/密钥均未外泄：明文 cookie 只在本机浏览器 ↔ Fernet 加密库间流转，日志/本清单无明文。
