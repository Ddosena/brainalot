# Windows PowerShell 5.1 supplies .NET Framework WinRT projection (PS7 does not).
# Private JSON-lines protocol. Never select the OS current session or send keys.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:Manager = $null
$script:InitializationError = $false
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $script:ManagerType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
    $script:PropertiesType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime]
    $script:RepeatType = [Windows.Media.MediaPlaybackAutoRepeatMode,Windows.Media,ContentType=WindowsRuntime]
    $script:AsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
} catch { $script:InitializationError = $true }

function Await-WinRT($Operation, [Type]$ResultType) {
    $task = $script:AsTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(1000)) { throw 'Spotify operation timed out' }
    return $task.Result
}

function Get-SpotifySession {
    if ($null -eq $script:Manager) {
        $script:Manager = Await-WinRT ($script:ManagerType::RequestAsync()) $script:ManagerType
    }
    # Exact known desktop / Store AUMIDs only; browser Spotify is intentionally excluded.
    $sessions = @($script:Manager.GetSessions() | Where-Object {
        $_.SourceAppUserModelId -match '^(Spotify\.exe|SpotifyAB\.SpotifyMusic_[a-z0-9]+!Spotify)$'
    } | Sort-Object @{Expression={ if ([string]$_.GetPlaybackInfo().PlaybackStatus -eq 'Playing') {0} else {1} }}, SourceAppUserModelId)
    if ($sessions.Count -gt 0) { return $sessions[0] }
    return $null
}

function New-SpotifyState {
    return @{
        available = (-not $script:InitializationError); connected = $false; source_id = $null
        title = ''; artist = ''; playing = $false; shuffle = $null; repeat = $null
        controls = @{previous=$false; play_pause=$false; next=$false; shuffle=$false; repeat=$false; open=$true}
        error = $null
    }
}

function Read-SpotifyState($Session) {
    $state = New-SpotifyState
    if ($null -eq $Session) { return $state }
    $info = $Session.GetPlaybackInfo()
    $media = Await-WinRT ($Session.TryGetMediaPropertiesAsync()) $script:PropertiesType
    $state.connected = $true
    $state.source_id = $Session.SourceAppUserModelId
    $state.title = [string]$media.Title
    $state.artist = [string]$media.Artist
    $state.playing = [string]$info.PlaybackStatus -eq 'Playing'
    if ($null -ne $info.IsShuffleActive) { $state.shuffle = [bool]$info.IsShuffleActive }
    if ($null -ne $info.AutoRepeatMode) {
        $state.repeat = switch ([string]$info.AutoRepeatMode) { 'None' {'off'} 'Track' {'track'} 'List' {'list'} default {$null} }
    }
    $state.controls.previous = [bool]$info.Controls.IsPreviousEnabled
    $state.controls.next = [bool]$info.Controls.IsNextEnabled
    $state.controls.play_pause = [bool]$info.Controls.IsPlayPauseToggleEnabled
    $state.controls.shuffle = [bool]$info.Controls.IsShuffleEnabled
    $state.controls.repeat = [bool]$info.Controls.IsRepeatEnabled
    return $state
}

while ($null -ne ($line = [Console]::ReadLine())) {
    $request = $null
    try {
        $request = $line | ConvertFrom-Json
        if ($script:InitializationError) { throw 'Windows media control is unavailable' }
        $session = Get-SpotifySession
        $state = Read-SpotifyState $session
        $ok = $true
        $errorMessage = $null
        if ($request.action -ne 'snapshot') {
            if ($request.action -notin @('previous', 'play_pause', 'next', 'shuffle', 'repeat')) {
                $ok = $false; $errorMessage = 'Unknown Spotify command'
            } elseif ($null -eq $session) {
                $ok = $false; $errorMessage = 'Spotify is not active'
            } elseif (-not $state.controls[$request.action]) {
                $ok = $false; $errorMessage = 'Spotify does not support this command'
            } elseif ($request.action -eq 'shuffle' -and $request.value -isnot [bool]) {
                $ok = $false; $errorMessage = 'Shuffle requires a boolean value'
            } elseif ($request.action -eq 'repeat' -and $request.value -notin @('off','track','list')) {
                $ok = $false; $errorMessage = 'Repeat requires off, track or list'
            } else {
                $operation = switch ($request.action) {
                    'previous' { $session.TrySkipPreviousAsync() }
                    'play_pause' { $session.TryTogglePlayPauseAsync() }
                    'next' { $session.TrySkipNextAsync() }
                    'shuffle' { $session.TryChangeShuffleActiveAsync([bool]$request.value) }
                    'repeat' {
                        $mode = switch ($request.value) { 'off' {0} 'track' {1} 'list' {2} }
                        $session.TryChangeAutoRepeatModeAsync([Enum]::ToObject($script:RepeatType, $mode))
                    }
                }
                $ok = [bool](Await-WinRT $operation ([bool]))
                if (-not $ok) { $errorMessage = 'Spotify rejected the command' }
                $state = Read-SpotifyState $session
            }
        }
        $response = @{id=$request.id; ok=$ok; state=$state; error=$errorMessage}
    } catch {
        $script:Manager = $null
        $state = New-SpotifyState
        $state.available = $false
        $state.error = 'Spotify local integration is unavailable'
        $response = @{id=$request.id; ok=$false; state=$state; error='Spotify operation failed; command outcome may be unknown'}
    }
    [Console]::WriteLine(($response | ConvertTo-Json -Depth 5 -Compress))
    [Console]::Out.Flush()
}
