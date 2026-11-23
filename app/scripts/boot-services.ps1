# OpenVideo boot launcher (ASCII only: PS 5.1 reads BOM-less files as ANSI,
# so non-ASCII comments here can break parsing).
#
#   .\scripts\boot-services.ps1          start services, wait for health
#   .\scripts\boot-services.ps1 -Status  show state only

param([switch]$Status)

$ErrorActionPreference = 'SilentlyContinue'
$distro = 'Ubuntu'
$services = @('comfyui-h3-045.service', 'studio.service', 'openvideo-health.timer')
$log = Join-Path $env:LOCALAPPDATA 'openvideo-boot.log'
$health = 'http://127.0.0.1:8801/api/health'

function Write-Log($msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Value $line
}
function Wsl($cmd) { & wsl.exe -d $distro -u root -- bash -lc $cmd }
function Test-Health {
    $code = (Wsl "curl -s -o /dev/null -w '%{http_code}' --max-time 5 $health").Trim()
    return ($code -eq '200')
}

if ($Status) {
    foreach ($s in $services) {
        Write-Host ("{0,-26} {1}" -f $s, (Wsl "systemctl is-active $s").Trim())
    }
    Write-Host ("studio health: " + (Wsl "curl -s -o /dev/null -w '%{http_code}' --max-time 5 $health").Trim())
    exit 0
}

Write-Log 'boot: starting'

for ($i = 1; $i -le 20; $i++) {
    $ok = (Wsl 'echo up').Trim()
    if ($ok -eq 'up') { break }
    Start-Sleep -Seconds 3
}
Write-Log 'wsl up'

for ($attempt = 1; $attempt -le 2; $attempt++) {
    Wsl ("systemctl start " + ($services -join ' ')) | Out-Null
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 4
        if (Test-Health) {
            Write-Log ("studio ready (attempt " + $attempt + ")")
            exit 0
        }
    }
    Write-Log ("attempt " + $attempt + " timed out")
}

$keepalive = Join-Path $env:LOCALAPPDATA 'OpenVideo\wsl-keepalive.vbs'
if (Test-Path $keepalive) {
    Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $keepalive + '"') -WindowStyle Hidden
}
Write-Log 'boot: TIMEOUT'
exit 1
