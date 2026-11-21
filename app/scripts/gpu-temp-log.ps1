param(
    [int]$Seconds = 0,
    [int]$IntervalSec = 15,
    [switch]$Once,
    [int]$WarnC = 80,
    [int]$DangerC = 84
)

# GPU temperature logger (ASCII output on purpose: avoids console codepage garbling)
#   .\scripts\gpu-temp-log.ps1 -Once
#   .\scripts\gpu-temp-log.ps1 -Seconds 3600

$root   = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root '_research\gpu-temp'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ('gpu-' + (Get-Date -Format 'yyyyMMdd') + '.csv')
if (-not (Test-Path $logFile)) {
    Add-Content -Path $logFile -Value 'time,core_c,power_w,power_limit_w,sm_mhz,fan_pct,throttle'
}

$deadline = $null
if ($Seconds -gt 0) { $deadline = (Get-Date).AddSeconds($Seconds) }

while ($true) {
    $raw = & nvidia-smi --query-gpu=temperature.gpu,power.draw,power.limit,clocks.sm,fan.speed,clocks_throttle_reasons.active --format=csv,noheader,nounits 2>$null
    if (-not $raw) { Write-Host 'nvidia-smi unavailable' -ForegroundColor Red; break }
    $p = $raw -split ',\s*'

    $core = [double]$p[0]
    $pw   = [double]$p[1]
    $plim = [double]$p[2]
    $sm   = $p[3]
    $fan  = $p[4]
    $thr  = $p[5]

    $color = 'Green'
    if ($core -ge $DangerC) { $color = 'Red' }
    elseif ($core -ge $WarnC) { $color = 'Yellow' }

    $stamp = Get-Date -Format 'HH:mm:ss'
    $msg = '{0}  core {1:N1}C  power {2:N0}/{3:N0}W  sm {4}MHz  fan {5}%  throttle {6}' -f $stamp, $core, $pw, $plim, $sm, $fan, $thr
    Write-Host $msg -ForegroundColor $color

    if ($core -ge $DangerC) {
        Write-Host '  !! at 84C thermal limit - GPU clocks down; stop a job or lower power limit to 80%' -ForegroundColor Red
    }

    $row = '{0},{1:N2},{2:N0},{3:N0},{4},{5},{6}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $core, $pw, $plim, $sm, $fan, $thr
    Add-Content -Path $logFile -Value $row

    if ($Once) { break }
    if ($deadline -ne $null -and (Get-Date) -ge $deadline) { break }
    Start-Sleep -Seconds $IntervalSec
}
