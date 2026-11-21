# Boot OpenVideo services. ASCII only: PS 5.1 reads BOM-less .ps1 as ANSI,
# so any UTF-8 Chinese here would corrupt parsing.
#
#   .\scripts\boot-services.ps1          start everything and wait for health
#   .\scripts\boot-services.ps1 -Status  show current state only

param([switch]$Status)

$ErrorActionPreference = 'SilentlyContinue'
$distro = 'Ubuntu'
$services = @('comfyui-8188.service', 'studio.service', 'openvideo-health.timer')

function Wsl($cmd) { & wsl.exe -d $distro -u root -- bash -lc $cmd }

if ($Status) {
    $vmmem = Get-Process -Name 'vmmemWSL' -ErrorAction SilentlyContinue
    if (-not $vmmem) {
        Write-Host 'WSL: not running'
        exit 0
    }
    Write-Host 'WSL: running'
    foreach ($s in $services) {
        $act = (Wsl "systemctl is-active $s").Trim()
        if (-not $act) { $act = 'unknown' }
        Write-Host ("  {0,-26} {1}" -f $s, $act)
    }
    $code = (Wsl "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8801/api/health").Trim()
    Write-Host "  studio health: $code"
    exit 0
}

Write-Host '== start WSL =='
& wsl.exe -d $distro -u root -- echo boot | Out-Null
Start-Sleep -Seconds 3

Write-Host '== start services =='
Wsl ("systemctl start " + ($services -join ' ')) | Out-Null
Start-Sleep -Seconds 6

Write-Host '== wait for studio =='
for ($i = 1; $i -le 20; $i++) {
    $code = (Wsl "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8801/api/health").Trim()
    if ($code -eq '200') {
        Write-Host '  ready: http://127.0.0.1:8801'
        exit 0
    }
    Start-Sleep -Seconds 3
}

Write-Host '  TIMEOUT - check: systemctl status studio' -ForegroundColor Yellow
exit 1
