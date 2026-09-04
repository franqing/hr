// liepin_export_cookie.mjs —— Windows 侧零依赖助手（recruit-app 后端直接 spawn node.exe 拉起）
//
// 作用：从 `liepin login` 的常驻 Chrome（@viyzhu/liepin-cli 固定调试端口）经 CDP
// 导出 liepin.com 域 cookie，拼成 Cookie 头写入 ~/.liepin-cli/cookie_header.txt。
//
// 边界：仅本机回环操作；绝不打印 cookie 明文；不驱动浏览器（绝不自动登录）。
// 退出码：0=成功；1=内部错误；2=调试端口无响应（liepin 浏览器没在跑）；3=浏览器在但无 liepin cookie。
//
// Windows 退出协议（崩溃坑各踩两轮，二分验证后勿改回）：
// 1) 断言根因（实测二分定位）：本机 node v24 上「≥2 个 undici fetch keep-alive 连接
//    + process.exit() 强退」必然触发 Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)
//    src\win\async.c:76 —— 单 fetch 结构 + process.exit 干净；双 fetch 结构换 exitCode
//    自然退出同样干净（0.18s，无挂活）。→ 一律 exitCode 自然退出，绝不 process.exit。
// 2) WS 必须关并等 close 事件落地：不关则 WebSocket 挂活事件循环，自然退出永不发生。
// 3) 关键输出行用 fs.writeSync 同步写管道（abort 或提前退出时不等异步 flush）。
// 要求：Node >= 22（内置 fetch / WebSocket / AbortSignal.timeout）。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// CLI 默认 53471；用户曾 setx LIEPIN_BROWSER_REMOTE_DEBUGGING_PORT=9222 → 两者都探测。
const PORTS = [9222, 53471]
const DOMAIN_FILTER = 'liepin.com'

const out = (s) => fs.writeSync(1, s + '\n')
const err = (s) => fs.writeSync(2, s + '\n')

async function httpJson(url, timeoutMs) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    return r.ok ? await r.json() : null
  } catch {
    return null
  }
}

/** 经 CDP 取全部 cookie。承诺：resolve/reject 前 WS 必已彻底关闭（否则自然退出永不发生）。 */
function getAllCookies(wsUrl, method) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl)
    let settled = false
    const timer = setTimeout(() => settle(reject, new Error('CDP 连接超时')), 15000)
    function settle(fn, arg) {
      if (settled) return
      settled = true
      clearTimeout(timer)
      // 关 WS 并等 close 事件落地；服务器不应答关闭帧时 2s 后 terminate 强制断开。
      // 任一收尾路径结束后才 fn(arg)，外层流程才能安全退出。
      const closeTimer = setTimeout(() => {
        try { ws.terminate() } catch { /* 已关 */ }
      }, 2000)
      ws.onclose = () => { clearTimeout(closeTimer); fn(arg) }
      try { ws.close() } catch { clearTimeout(closeTimer); fn(arg) } // 已处于关闭态 → 直接收尾
    }
    ws.onopen = () => ws.send(JSON.stringify({ id: 1, method }))
    ws.onerror = () => settle(reject, new Error('CDP WebSocket 连接失败'))
    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.id !== 1) return
      if (msg.error || !msg.result) {
        return settle(reject, new Error(`CDP ${method} 失败: ${msg.error?.message || '未知'}`))
      }
      if (!Array.isArray(msg.result.cookies)) {
        return settle(reject, new Error('CDP 返回异常（无 cookies 数组）'))
      }
      settle(resolve, msg.result.cookies)
    }
  })
}

async function main() {
  for (const port of PORTS) {
    const version = await httpJson(`http://127.0.0.1:${port}/json/version`, 3000)
    if (!version) continue // 该端口无浏览器
    // 1) 页面 target：Network.getAllCookies（与 puppeteer/playwright 同路径）
    const list = await httpJson(`http://127.0.0.1:${port}/json/list`, 3000)
    let target = null
    if (Array.isArray(list)) {
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) target = { wsUrl: page.webSocketDebuggerUrl, method: 'Network.getAllCookies' }
    }
    // 2) 浏览器 target：Storage.getCookies
    if (!target && version.webSocketDebuggerUrl) target = { wsUrl: version.webSocketDebuggerUrl, method: 'Storage.getCookies' }
    if (!target) {
      err(`端口 ${port} 在线但无可用 CDP target`)
      return 2
    }

    const all = await getAllCookies(target.wsUrl, target.method)
    const nowMs = Date.now()
    const wanted = all
      .filter((c) => (c.domain || '').includes(DOMAIN_FILTER))
      .filter((c) => !(c.expires && c.expires !== -1 && c.expires * 1000 < nowMs)) // 丢弃已过期
      .sort((a, b) => `${a.domain}|${b.name}`.localeCompare(`${b.domain}|${b.name}`))
    if (!wanted.length) {
      err('浏览器在线但未找到 liepin.com cookie —— 请先在该浏览器完成 liepin login 登录')
      return 3
    }
    const header = wanted.map((c) => `${c.name}=${c.value}`).join('; ')
    // 写系统临时目录而非 liepin-cli 的 cookie_header.txt：该文件归 liepin login 所有，
    // 登录中途导出部分会话会把它写坏。本文件只是 Python 侧的传输管道（轮询盯的是
    // Chrome Cookie 库 mtime，不依赖此文件）。
    const outFile = path.join(os.tmpdir(), 'recruit-liepin-cookie-export.txt')
    fs.writeFileSync(outFile, header, 'utf8')
    out(`OK port=${port} cookies=${wanted.length}`)
    out(`OUT=${outFile}`)
    return 0
  }
  err('未找到 liepin 登录浏览器（调试端口 9222 / 53471 均无响应）。请先在终端运行：liepin login')
  return 2
}

main()
  .then((code) => { process.exitCode = code }) // 自然退出：WS 已关完、无 process.exit 强退，node 自管收尾
  .catch((e) => { err(String((e && e.message) || e)); process.exitCode = 1 })
