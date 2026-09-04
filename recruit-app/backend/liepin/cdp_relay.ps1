# Recruit-app 运行资产：WSL2 NAT → Windows loopback 的 TCP 双向桥（Edge CDP 用）。
# 背景：Edge/Chromium 新版本忽略 --remote-debugging-address，DevTools 只绑
# Windows loopback（127.0.0.1 或 [::1]）；WSL2 NAT 下 WSL 内进程到不了该监听。
# adapter（backend/liepin/adapter.py）在启动外部浏览器后拉起本脚本，把
# 0.0.0.0:LISTEN → localhost:CDP 双向转发，playwright connect_over_cdp 从
# WSL 侧经网关 IP 接入。
# 用法：powershell.exe -NoProfile -ExecutionPolicy Bypass -File cdp_relay.ps1 <listen> <host> <cdp>
param(
    [int]$ListenPort = 9338,
    [string]$TargetHost = "localhost",
    [int]$TargetPort = 9337
)
$ErrorActionPreference = "Stop"
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $ListenPort)
$listener.Start()
while ($true) {
    $client = $null
    $upstream = $null
    try {
        $client = $listener.AcceptTcpClient()
        $upstream = [System.Net.Sockets.TcpClient]::new($TargetHost, $TargetPort)
        $cs = $client.GetStream()
        $us = $upstream.GetStream()
        $t1 = $cs.CopyToAsync($us)
        $t2 = $us.CopyToAsync($cs)
        [System.Threading.Tasks.Task]::WaitAny($t1, $t2)
    } catch {
        # 单条连接失败（上游未就绪等）不退出，继续服务
    } finally {
        if ($client) { $client.Close() }
        if ($upstream) { $upstream.Close() }
    }
}
