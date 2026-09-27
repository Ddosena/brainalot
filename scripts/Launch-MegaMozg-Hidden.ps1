param([ValidateRange(1024, 65535)][int]$Port = 8765)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$workerPath = Join-Path $PSScriptRoot 'Start-MegaMozg.ps1'
$healthUrl = "http://127.0.0.1:$Port/api/health"

try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
    if ($health.status -eq 'ok') { exit 0 }
} catch {
    # The service is not running yet.
}

# Prefer the autostart task of this folder: Task Scheduler starts the service
# outside the process tree of whoever ran Start-Service.cmd (a terminal, Codex,
# Claude), so closing that program no longer stops the service with it.
$autostartTask = Get-ScheduledTask -TaskName 'MegaMozg Local Service' -ErrorAction SilentlyContinue |
    Where-Object {
        $_.State -ne 'Disabled' -and @($_.Actions | Where-Object {
            $_.Arguments -and $_.Arguments.IndexOf($workerPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $_.Arguments -match "-Port $Port(\s|$)"
        }).Count -gt 0
    } | Select-Object -First 1
if ($autostartTask) {
    # A running task already has a supervisor that restarts the service by itself.
    Start-ScheduledTask -TaskName $autostartTask.TaskName
} else {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Port {1}' -f $workerPath, $Port
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WorkingDirectory $projectPath -WindowStyle Hidden | Out-Null
}

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        if ($health.status -eq 'ok') { exit 0 }
    } catch {
        # Give the local service a little more time to bind the port.
    }
}

throw "Brainalot не запустился. Проверьте data\state\logs\service-error.log"
