# Public-side check that needs no sign-in (AGENTS.md「測試與驗收」): this project's tunnel is
# connected and the public hostname is answered by Cloudflare Access. Page content is verified on
# 127.0.0.1:5000, which is the origin the tunnel serves.
$ErrorActionPreference = 'Stop'
$hostname = 'stockresearch.pimi-sunsun.com'

$tunnel = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -like '*stockresearch-pimi-sunsun*' } | Select-Object -First 1
if (-not $tunnel) {
    Write-Output 'FAIL tunnel: no cloudflared process for stockresearch-pimi-sunsun'
    exit 1
}
$ready = $null
foreach ($port in (Get-NetTCPConnection -OwningProcess $tunnel.ProcessId -State Listen -ErrorAction SilentlyContinue).LocalPort) {
    try {
        $ready = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/ready" -TimeoutSec 5).Content | ConvertFrom-Json
        break
    } catch { }
}
if (-not $ready -or $ready.readyConnections -lt 1) {
    Write-Output "FAIL tunnel: PID $($tunnel.ProcessId) has no ready connection"
    exit 1
}
Write-Output "OK   tunnel: PID $($tunnel.ProcessId), $($ready.readyConnections) connections"

$answer = & curl.exe -sS -o NUL -w '%{http_code} %{redirect_url}' --max-time 15 "https://$hostname/"
$code, $location = "$answer" -split ' ', 2
if ($code -ne '302' -or $location -notlike 'https://*.cloudflareaccess.com/*') {
    Write-Output "FAIL public: https://$hostname/ answered $answer"
    exit 1
}
Write-Output "OK   public: https://$hostname/ is behind Cloudflare Access ($code)"
