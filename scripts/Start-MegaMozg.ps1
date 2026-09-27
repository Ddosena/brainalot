param(
    [string]$Root,
    [ValidateRange(1024, 65535)][int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Не найден Python проекта. Выполните установку из README.md.'
}
if (-not $Root) { $Root = Join-Path $projectPath 'data' }
$Root = [System.IO.Path]::GetFullPath($Root)
$logDirectory = Join-Path $Root 'state\logs'
$outputLog = Join-Path $logDirectory 'service.log'
$errorLog = Join-Path $logDirectory 'service-error.log'
$supervisorLog = Join-Path $logDirectory 'supervisor.log'
$healthUrl = "http://127.0.0.1:$Port/api/health"
$serviceArguments = '-m megamozg --root "{0}" serve --port {1}' -f $Root, $Port
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-SupervisorLog([string]$Message) {
    if ((Test-Path -LiteralPath $supervisorLog) -and (Get-Item -LiteralPath $supervisorLog).Length -gt 1MB) {
        Move-Item -LiteralPath $supervisorLog -Destination ($supervisorLog + '.old') -Force
    }
    $line = '{0:yyyy-MM-dd HH:mm:ss} [{1}] {2}{3}' -f (Get-Date), $PID, $Message, [Environment]::NewLine
    [System.IO.File]::AppendAllText($supervisorLog, $line, (New-Object System.Text.UTF8Encoding $false))
}

function Test-ServiceHealth {
    # A refused connection to localhost costs about 2 s on Windows: look for a listener first.
    if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { return $false }
    try { return (Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2).status -eq 'ok' } catch { return $false }
}

# One supervisor per port: a second copy (for example, from Start-Service.cmd
# while the autostart task is running) leaves the work to the first one.
$mutex = New-Object System.Threading.Mutex($false, "Local\BrainalotServiceSupervisor-$Port")
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
if (-not $owned) {
    Write-SupervisorLog "another supervisor already watches port $Port"
    exit 0
}

# The service is restarted whenever its process ends: a crash, a restart by an
# agent, or a program that took its whole process tree down when it closed
# (2026-09-24: the service started from Codex died together with Codex).
# While another live copy of Brainalot answers on the port, the supervisor only watches it.
Write-SupervisorLog "supervisor started for port $Port"
$quickExits = 0
while ($true) {
    try {
        if (Test-ServiceHealth) {
            Start-Sleep -Seconds 5
            continue
        }
        foreach ($logPath in @($outputLog, $errorLog)) {
            if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 0) {
                # The previous run's output stays next to the new one.
                Move-Item -LiteralPath $logPath -Destination ($logPath + '.old') -Force
            }
        }
        $startedAt = Get-Date
        $process = Start-Process -FilePath $pythonPath -ArgumentList $serviceArguments `
            -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog
        $null = $process.Handle  # keeps ExitCode readable after the process ends
        Write-SupervisorLog "service started, PID $($process.Id)"
        $process.WaitForExit()
        $seconds = [int]((Get-Date) - $startedAt).TotalSeconds
        Write-SupervisorLog "service PID $($process.Id) exited with code $($process.ExitCode) after $seconds s"
        if ($seconds -lt 30) { $quickExits++ } else { $quickExits = 0 }
    } catch {
        Write-SupervisorLog "supervisor error: $($_.Exception.Message)"
        $quickExits++
    }
    # 1 s after a normal run; 2, 4 ... 60 s while the service keeps failing at start.
    Start-Sleep -Seconds ([Math]::Min(60, [Math]::Pow(2, [Math]::Min($quickExits, 6))))
}
