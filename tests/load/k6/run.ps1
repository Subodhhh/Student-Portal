# Computer 2: one k6 run, with Computer 2's own CPU, memory and network sampled beside it. Never overwrites a run.
#   .\run.ps1 -Run smoke-10vu-01 -Script browse.js -Vus 10
param(
  [Parameter(Mandatory = $true)][string]$Run,
  [string]$Script = "browse.js",
  [int]$Vus = 10,
  [string]$Ramp = "1m",
  [string]$Hold = "3m",
  [string]$Down = "30s",
  [string]$TargetHost = "192.168.2.103",
  [string]$K6 = "C:\lt2\k6\k6.exe",
  [string]$Results = "C:\lt2\results\stage2-perf"
)
$ErrorActionPreference = "Stop"
if ($Script -ne "health.js" -and -not $env:LT_PASSWORD) { throw "Set `$env:LT_PASSWORD first" }
$dir = Join-Path $Results $Run
if (Test-Path $dir) { throw "$dir already exists: pick a new run name" }
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$env:LT_HOST = $TargetHost; $env:LT_VUS = "$Vus"; $env:LT_RAMP = $Ramp; $env:LT_HOLD = $Hold; $env:LT_DOWN = $Down

@{ run = $Run; script = $Script; vus = $Vus; ramp = $Ramp; hold = $Hold; down = $Down; target = $TargetHost; started_utc = (Get-Date).ToUniversalTime().ToString("o")
   k6 = (& $K6 version | Select-Object -First 1); computer = $env:COMPUTERNAME } | ConvertTo-Json | Set-Content "$dir\meta.json"

$counters = '"\Processor(_Total)\% Processor Time" "\Memory\Available MBytes" "\Network Interface(*)\Bytes Total/sec"'
$sampler = Start-Process typeperf -ArgumentList "$counters -si 5 -f CSV -o `"$dir\computer2.csv`" -y" -WindowStyle Hidden -PassThru
try {
  $ErrorActionPreference = "Continue"     # k6 writes progress to stderr; PowerShell 5.1 would treat it as a fatal error
  & $K6 run $Script --summary-export "$dir\summary.json" --out "json=$dir\raw.json.gz" 2>&1 | Tee-Object -FilePath "$dir\console.txt"
  $code = $LASTEXITCODE
} finally {
  Stop-Process -Id $sampler.Id -ErrorAction SilentlyContinue
}
Write-Host "run $Run finished, k6 exit code $code, files in $dir"
