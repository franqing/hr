# 华联招聘 · 猎聘候选人智能搜寻

用**华联半导体**自己的**猎聘企业版账号**（`lpt.liepin.com`），按人才画像自动搜索匹配简历：AI 解析岗位要求 PDF → 结构化画像 → 自动多关键词搜索 → 规则/AI 打分 → 简历详情解析 → Excel 导出 → 批量站内邀请投递。

> ⚠️ 合规红线：仅使用本公司猎聘企业版账号、低量限速、不抓取手机/邮箱、通过平台站内功能触达候选人。请遵守猎聘平台规则与招聘数据使用规范。

## 快速启动

```bash
cd Desktop/hr/recruit-app

# 后端
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev        # http://localhost:5173 （/api 自动代理到 8000）
```

浏览器打开 `http://localhost:5173` → **设置**页 → 用自己的企业账号在 Chrome 登录 `lpt.liepin.com` 后，把 Cookie 粘贴进系统（DevTools → Application → Cookies）→ 测试连接。

> 📖 **完整操作手册见 [`docs/操作手册.md`](docs/操作手册.md)** —— 覆盖代理、浏览器、Cookie、画像、搜索、AI 评分、批量邀请、定时搜索与已知风险。新人从「第 2 节 设置页配置」开始即可。

## 网络要求

猎聘（`liepin.com` / `lpt.liepin.com`）需要能直连访问。本项目在 WSL2 验证时发现 Windows 与 WSL 直连均被重置，**推荐用设置页填代理**（改完即生效，无需重启）：

- **设置页 → 代理**：填 HTTP/HTTPS 代理 `http://127.0.0.1:7890`（换成你的 Clash/FlClash 实际端口）→ 保存 → 设置页「测试连接」。
- 或 Windows 代理开启 **TUN 模式**，让 WSL2 流量自动走代理（不填也能通，但建议填上更稳）。
- 或启动后端前导出代理环境变量：
  ```bash
  export http_proxy=http://127.0.0.1:7890
  export https_proxy=http://127.0.0.1:7890
  export no_proxy=localhost,127.0.0.1
  ```

浏览器自检：`chrome` 通道在 Linux/WSL 会自动探测本机 Windows Chrome/Edge 并调用；找不到时回退 Playwright Chromium（`playwright install chromium`）。也可在设置页「浏览器可执行文件」手动指定。

## 端口冲突提示

本机若同时运行 **BOM-AI**（`bom-converter`），它占用 **8000** 与 **5173**。二选一：
- 停掉 BOM-AI 后再启动本应用；或
- 本应用后端换端口启动：`uvicorn backend.main:app --port 8002`，并把 `frontend/vite.config.js` 的 proxy target 改成对应端口，前端 `npm run dev -- --port 5175`。

## 目录

```
backend/
  main.py           # FastAPI 路由 + lifespan
  db.py             # SQLite（内联 schema + _migrate）
  security.py       # Fernet 加密（cookie / LLM key）
  models.py         # dataclass
  liepin/           # 猎聘自动化（cookie 注入→搜索→简历→邀请）
  matcher.py        # quick_gate / hard_gate / rule_score
  scorer.py         # LLM 五维评分（降级规则兜底）
  profile_parser.py # 岗位要求 PDF → 结构化画像
  search_planner.py # 画像 → 搜索关键词/筛选组
  export.py         # Excel 导出
  invite_service.py # M6 批量邀请（限速/停止/重试）
  scheduler.py      # M7 定时搜索
frontend/           # Vue3 + Element Plus + Vite
data/               # app.db / secret.key / browser_profile/ / exports/（gitignore）
tests/              # pytest（mock 猎聘，不触网）
```

## 里程碑

M1 骨架+设置 ✅ → M2 搜索落地 ✅ → M3 画像上传+规则打分+导出 ✅ → M4 简历详情 ✅ → M5 LLM 评分 ✅ → M6 批量邀请 ✅ → M7 定时搜索 ✅ → M8 加固+测试 ✅

## 测试

```bash
.venv/bin/pytest tests/ -q
```
