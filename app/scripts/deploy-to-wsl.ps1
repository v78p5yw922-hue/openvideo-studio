# Sync D: sources into WSL and verify.
#
#   .\scripts\deploy-to-wsl.ps1            sync + tests + restart studio
#   .\scripts\deploy-to-wsl.ps1 -NoRestart sync + tests, no restart
#   .\scripts\deploy-to-wsl.ps1 -SkipTest  sync + restart, no tests
#
# The real work is scripts/deploy-sync.sh (runs inside WSL).
# Python changes need a studio.service restart; HTML under studio/web does not.

param(
    [switch]$NoRestart,
    [switch]$SkipTest
)

$ErrorActionPreference = 'Stop'
$distro = 'Ubuntu'
$script = '/mnt/d/AI/DeepSeekProjects/OpenVideo/scripts/deploy-sync.sh'

$args = @()
if ($NoRestart) { $args += '--no-restart' }
if ($SkipTest)  { $args += '--skip-test' }

$cmd = "bash '$script' $($args -join ' ')"
& wsl.exe -d $distro -u root -- bash -lc $cmd
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
