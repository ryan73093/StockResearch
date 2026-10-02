# Public-side check that needs no sign-in (AGENTS.md): this project's tunnel is
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
if ($code -eq '302' -and $location -like 'https://*.cloudflareaccess.com/*') {
    Write-Output "OK   public: https://$hostname/ is behind Cloudflare Access ($code)"
    exit 0
}
$clientFile = Join-Path $PSScriptRoot '..\.runtime\home-sso.json'
if (Test-Path -LiteralPath $clientFile) {
    $client = Get-Content -LiteralPath $clientFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($client.enabled -eq $true -and $code -eq '302' -and $location.StartsWith($client.home_url.TrimEnd('/') + '/sso/authorize?')) {
        $apiCode = & curl.exe -sS -o NUL -w '%{http_code}' --max-time 15 "https://$hostname/api/status"
        if ($apiCode -eq '401') {
            Write-Output 'OK   public: home-sso native origin rejects anonymous data API.'
            exit 0
        }
    }
}
Write-Output "FAIL public: https://$hostname/ did not show verified login protection."
exit 1
