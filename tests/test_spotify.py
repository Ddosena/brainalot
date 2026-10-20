"""Offline playback tests. No test issues a command to a live media session."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from megamozg.spotify import SpotifyController, _PowerShellBridge


def active_state(**changes):
    state = {
        "available": True, "connected": True, "source_id": "Spotify.exe",
        "title": "Test track", "artist": "Test artist", "playing": False,
        "shuffle": False, "repeat": "off", "error": None,
        "controls": {key: True for key in ("previous", "play_pause", "next", "shuffle", "repeat", "open")},
    }
    state.update(changes)
    return state


class Adapter:
    def __init__(self, state=None):
        self.state = state or active_state()
        self.calls = []
        self.closed = False
        self.failure = None
        self.accepted = True

    def request(self, action, value=None):
        self.calls.append((action, value))
        if self.failure:
            raise self.failure
        return {"ok": self.accepted, "state": self.state,
                "error": None if self.accepted else "Spotify does not support this command"}

    def close(self):
        self.closed = True


def test_shared_cached_snapshot_is_copied_and_concurrent_reads_are_serialized():
    adapter = Adapter()
    clock = [10.0]
    controller = SpotifyController(adapter=adapter, platform="win32", clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=8) as executor:
        states = list(executor.map(lambda _: controller.snapshot(), range(12)))
    assert adapter.calls == [("snapshot", None)]
    states[0]["controls"]["next"] = False
    assert controller.snapshot()["controls"]["next"] is True
    clock[0] += 1.6
    controller.snapshot()
    assert len(adapter.calls) == 2
    controller.close()
    assert adapter.closed
    assert controller.snapshot()["connected"] is False


@pytest.mark.parametrize("source", ["chrome.exe", "Spotify.exe.evil", "notSpotify.exe", "SpotifyAB.Other_123!Spotify"])
def test_non_spotify_source_is_never_exposed(source):
    adapter = Adapter(active_state(source_id=source))
    state = SpotifyController(adapter=adapter, platform="win32").snapshot()
    assert state["connected"] is False
    assert state["source_id"] is None
    assert state["title"] == ""
    assert state["controls"]["next"] is False


def test_store_spotify_source_and_missing_optional_states():
    adapter = Adapter(active_state(source_id="SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", shuffle=None, repeat=None))
    state = SpotifyController(adapter=adapter, platform="win32").snapshot()
    assert state["connected"] is True
    assert state["shuffle"] is None and state["repeat"] is None


def test_inactive_state_does_not_invent_controls_or_metadata():
    adapter = Adapter(active_state(connected=False))
    state = SpotifyController(adapter=adapter, platform="win32").snapshot()
    assert state["available"] is True
    assert state["title"] == ""
    assert state["controls"] == {"next": False, "open": True, "play_pause": False,
                                  "previous": False, "repeat": False, "shuffle": False, "volume": False}


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_never_starts_adapter_or_opens_uri(platform):
    adapter = Adapter()
    opened = []
    controller = SpotifyController(adapter=adapter, platform=platform, opener=opened.append)
    assert controller.snapshot()["available"] is False
    assert controller.command("open")["ok"] is False
    assert adapter.calls == [] and opened == []


@pytest.mark.parametrize(("action", "value"), [
    ("shell", None), ([], None), ("shuffle", 1), ("shuffle", "true"),
    ("repeat", None), ("repeat", "all"), ("next", "anything"), ("open", "https://example.test"),
])
def test_invalid_commands_have_no_side_effect(action, value):
    adapter = Adapter()
    opened = []
    controller = SpotifyController(adapter=adapter, platform="win32", opener=opened.append)
    assert controller.command(action, value)["ok"] is False
    assert adapter.calls == [] and opened == []


@pytest.mark.parametrize(("action", "value"), [
    ("previous", None), ("play_pause", None), ("next", None),
    ("shuffle", True), ("shuffle", False), ("repeat", "off"), ("repeat", "track"), ("repeat", "list"),
])
def test_commands_are_forwarded_once_and_do_not_optimistically_change_state(action, value):
    adapter = Adapter()
    controller = SpotifyController(adapter=adapter, platform="win32")
    result = controller.command(action, value)
    assert result["ok"] is True
    assert result["state"]["playing"] is False
    assert result["state"]["shuffle"] is False
    assert result["state"]["repeat"] == "off"
    assert adapter.calls == [(action, value)]


def test_rejected_command_is_explicit():
    adapter = Adapter()
    adapter.accepted = False
    result = SpotifyController(adapter=adapter, platform="win32").command("repeat", "track")
    assert result["ok"] is False
    assert result["error"] == "Spotify does not support this command"


def test_snapshot_failure_backoff_and_recovery_without_replaying_command():
    adapter = Adapter()
    clock = [0.0]
    controller = SpotifyController(adapter=adapter, platform="win32", clock=lambda: clock[0])
    adapter.failure = RuntimeError("private details must not leak")
    result = controller.command("next")
    assert result["ok"] is False and "unknown" in result["error"]
    assert "private" not in json.dumps(result)
    controller.snapshot()
    assert adapter.calls == [("next", None)]
    adapter.failure = None
    clock[0] = 11
    assert controller.snapshot()["connected"] is True
    assert adapter.calls == [("next", None), ("snapshot", None)]


def test_helper_unavailable_state_uses_failure_backoff():
    adapter = Adapter(active_state(available=False, connected=False, error="Unavailable"))
    clock = [0.0]
    controller = SpotifyController(adapter=adapter, platform="win32", clock=lambda: clock[0])
    controller.snapshot()
    clock[0] = 3
    controller.snapshot()
    assert adapter.calls == [("snapshot", None)]
    clock[0] = 11
    controller.snapshot()
    assert len(adapter.calls) == 2


def test_open_uses_only_constant_uri_and_no_media_request():
    opened = []
    adapter = Adapter()
    controller = SpotifyController(adapter=adapter, platform="win32", opener=opened.append)
    assert opened == []
    controller.snapshot()
    assert opened == []
    assert controller.command("open")["ok"] is True
    assert opened == ["spotify:"]
    assert adapter.calls == [("snapshot", None)]


def test_open_failure_is_explicit():
    def fail(_):
        raise OSError("no handler")
    assert SpotifyController(platform="win32", opener=fail).command("open")["ok"] is False


@pytest.mark.parametrize("value", [None, True, False, -0.01, 1.01, 10**500, float("nan"), float("inf"), "0.5", []])
def test_invalid_volume_never_reaches_adapter(value):
    adapter = Adapter()
    assert SpotifyController(adapter=adapter, platform="win32").command("volume", value)["ok"] is False
    assert adapter.calls == []


@pytest.mark.parametrize("value", [0, 0.3, 1])
def test_volume_forwarded_once(value):
    adapter = Adapter(active_state(volume=value))
    adapter.state["controls"]["volume"] = True
    result = SpotifyController(adapter=adapter, platform="win32").command("volume", value)
    assert result["ok"] is True
    assert result["state"]["volume"] == value
    assert adapter.calls == [("volume", value)]


@pytest.mark.parametrize("value", [None, -1, 2, 10**500, float("nan"), float("inf"), True, "0.5"])
def test_unknown_volume_does_not_disable_playback(value):
    adapter = Adapter(active_state(volume=value))
    state = SpotifyController(adapter=adapter, platform="win32").snapshot()
    assert state["volume"] is None
    assert state["controls"]["next"] is True
    assert state["controls"]["play_pause"] is True


def test_slow_snapshot_never_blocks_command_or_queues_extra_polls():
    started, release = threading.Event(), threading.Event()
    class SlowAdapter(Adapter):
        def request(self, action, value=None):
            self.calls.append((action, value))
            if action == "snapshot":
                started.set()
                assert release.wait(3)
                return {"ok": True, "state": active_state(playing=True)}
            return {"ok": True, "state": active_state(playing=False)}
    adapter = SlowAdapter()
    controller = SpotifyController(adapter=adapter, platform="win32")
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(controller.snapshot)
        try:
            assert started.wait(1)
            command = executor.submit(controller.command, "play_pause")
            # Snapshot remains held until after command completion: no dependency
            # on its metadata wait, rather than a timing comparison on a busy CI VM.
            assert command.result(timeout=1)["ok"] is True
            for _ in range(10):
                assert controller.snapshot()["playing"] is False
            assert adapter.calls == [("snapshot", None), ("play_pause", None)]
        finally:
            release.set()
        assert pending.result(timeout=1)["playing"] is False  # stale poll discarded
    controller.close()


def test_command_light_reply_preserves_metadata_but_invalidates_poll_cache():
    class LightAdapter(Adapter):
        def request(self, action, value=None):
            self.calls.append((action, value))
            state = active_state(volume=0.4)
            if action != "snapshot":
                for key in ("title", "artist", "volume"):
                    state.pop(key)
            else:
                state["controls"]["volume"] = True
            return {"ok": True, "state": state, "confirmed": False}
    adapter = LightAdapter()
    controller = SpotifyController(adapter=adapter, platform="win32")
    controller.snapshot()
    response = controller.command("play_pause")
    assert response["confirmed"] is False
    assert response["state"]["volume"] == 0.4
    assert response["state"]["title"] == "Test track"
    assert response["state"]["controls"]["volume"] is True
    controller.snapshot()
    assert adapter.calls == [("snapshot", None), ("play_pause", None), ("snapshot", None)]


def test_lost_volume_response_is_not_replayed():
    adapter = Adapter()
    adapter.failure = RuntimeError("response lost after accepted write")
    controller = SpotifyController(adapter=adapter, platform="win32")
    result = controller.command("volume", 0.3)
    assert result["ok"] is False and "unknown" in result["error"]
    controller.snapshot()
    assert adapter.calls == [("volume", 0.3)]


def use_python_helper(monkeypatch, source):
    """Keep the actual transport/process lifecycle, replace only the child code."""
    popen = subprocess.Popen
    children = []

    def launch(args, **kwargs):
        assert args[0].endswith("powershell.exe")
        assert args[-1].endswith("spotify_bridge.ps1")
        child = popen([sys.executable, "-u", "-c", source], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr("megamozg.spotify.subprocess.Popen", launch)
    return children


def test_transport_reuses_child_and_restarts_after_exit(monkeypatch):
    children = use_python_helper(monkeypatch, "import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'id':r['id'],'ok':True}),flush=True)")
    bridge = _PowerShellBridge()
    try:
        assert bridge.request("snapshot")["ok"] is True
        assert bridge.request("snapshot")["ok"] is True
        assert len(children) == 1
        children[0].terminate()
        children[0].wait(timeout=2)
        assert bridge.request("snapshot")["ok"] is True
        assert len(children) == 2
    finally:
        bridge.close()
    assert all(child.poll() is not None for child in children)


def test_transport_timeout_reaps_child_without_retry(monkeypatch):
    children = use_python_helper(monkeypatch, "import sys,time\nfor line in sys.stdin:\n time.sleep(30)")
    bridge = _PowerShellBridge(timeout=0.05)
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            bridge.request("next")
        assert len(children) == 1
        assert children[0].poll() is not None
    finally:
        bridge.close()


def test_transport_rejects_bad_sequence_and_reaps_child(monkeypatch):
    children = use_python_helper(monkeypatch, "import sys\nfor line in sys.stdin:\n print('{\"id\":0}',flush=True)")
    bridge = _PowerShellBridge()
    try:
        with pytest.raises(RuntimeError):
            bridge.request("snapshot")
        assert children[0].poll() is not None
    finally:
        bridge.close()


def run_harness(command, payload, phase_path, *, startup_seconds=45, protocol_seconds=10):
    """Budget cold Windows/WinRT startup separately from the fake RPC loop.

    The child declares readiness after type loading and selector checks, before
    it reads stdin. Phase evidence survives even when startup itself stalls.
    This changes only the test host budget; production RPC deadlines stay fixed.
    """
    started = time.monotonic()
    with subprocess.Popen(command, encoding="utf-8", stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        stage = "startup"
        try:
            while process.poll() is None:
                phases = phase_path.read_text(encoding="utf-8") if phase_path.exists() else ""
                if "ready" in phases.splitlines():
                    break
                if time.monotonic() - started >= startup_seconds:
                    raise subprocess.TimeoutExpired(command, startup_seconds)
                time.sleep(0.02)
            stage = "protocol" if process.poll() is None else "early-exit"
            stdout, stderr = process.communicate(payload, timeout=protocol_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            phases = phase_path.read_text(encoding="utf-8") if phase_path.exists() else "<not started>"
            pytest.fail(
                f"PowerShell fake-session harness {stage} timeout after "
                f"{time.monotonic() - started:.2f}s; phases={phases[-3000:]!r}; "
                f"stdout={stdout[-4000:]!r}; stderr={stderr[-4000:]!r}",
                pytrace=False,
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def test_harness_startup_timeout_reports_phase_and_reaps_process(tmp_path):
    phase = tmp_path / "phase.txt"
    # A child that never declares readiness must exhaust only the startup budget.
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(pytest.fail.Exception, match="startup timeout"):
        run_harness(command, "", phase, startup_seconds=0.05)


def test_harness_protocol_timeout_reports_partial_output(tmp_path):
    phase = tmp_path / "phase.txt"
    source = ("import pathlib,sys,time; "
              "pathlib.Path(sys.argv[1]).write_text('ready\\n', encoding='utf-8'); "
              "print('partial-response',flush=True); "
              "print('protocol-phase',file=sys.stderr,flush=True); time.sleep(30)")
    with pytest.raises(pytest.fail.Exception, match="protocol timeout.*partial-response.*protocol-phase"):
        run_harness([sys.executable, "-u", "-c", source, str(phase)], "", phase,
                    protocol_seconds=0.05)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WinRT helper")
def test_real_powershell_helper_routes_fake_session_commands(tmp_path):
    # Execute the shipping routing loop with an entirely fake session. The
    # initialization loads WinRT types but Get-SpotifySession is replaced before
    # the loop executes, so no live manager or media command can be reached.
    helper = Path(__file__).parents[1] / "megamozg" / "spotify_bridge.ps1"
    harness = tmp_path / "spotify-fake.ps1"
    phase = tmp_path / "spotify-fake.phases"
    harness.write_text(r'''
$script:PhasePath = $args[1]
function Trace-HarnessPhase([string]$Phase) {
    [IO.File]::AppendAllText($script:PhasePath, $Phase + [Environment]::NewLine)
}
Trace-HarnessPhase 'started'
$source = [IO.File]::ReadAllText($args[0])
$split = $source.IndexOf('while ($null -ne ($line = [Console]::ReadLine()))')
if ($split -lt 0) { throw 'Shipping helper routing loop not found' }
Invoke-Expression $source.Substring(0, $split)
Trace-HarnessPhase 'winrt-initialized'
$script:Calls = New-Object 'System.Collections.Generic.List[string]'
$script:MetadataCalls = 0
$script:Dispatch = New-Object 'System.Collections.Generic.List[double]'
$script:Fake = [PSCustomObject]@{SourceAppUserModelId='Spotify.exe'}
$script:Controls = [PSCustomObject]@{IsPreviousEnabled=$true;IsNextEnabled=$true;IsPlayPauseToggleEnabled=$true;IsShuffleEnabled=$true;IsRepeatEnabled=$true}
$script:Fake | Add-Member ScriptMethod GetPlaybackInfo { return [PSCustomObject]@{PlaybackStatus='Paused';IsShuffleActive=$false;AutoRepeatMode='None';Controls=$script:Controls} }
$script:Fake | Add-Member ScriptMethod TryGetMediaPropertiesAsync { $script:MetadataCalls++; Start-Sleep -Milliseconds 200; return [PSCustomObject]@{Title='Fake';Artist='Offline'} }
$script:Fake | Add-Member ScriptMethod TrySkipPreviousAsync { $script:Dispatch.Add($script:DispatchClock.Elapsed.TotalMilliseconds); $script:Calls.Add('previous'); return $true }
$script:Fake | Add-Member ScriptMethod TrySkipNextAsync { $script:Calls.Add('next'); return $true }
$script:Fake | Add-Member ScriptMethod TryTogglePlayPauseAsync { $script:Calls.Add('play_pause'); return $true }
$script:Fake | Add-Member ScriptMethod TryChangeShuffleActiveAsync { param($v) $script:Calls.Add("shuffle:$v"); return $true }
$script:Fake | Add-Member ScriptMethod TryChangeAutoRepeatModeAsync { param($v) $script:Calls.Add("repeat:$v"); return $true }
# Exercise the actual selector against a fake manager; Chrome must be ignored
# even if Windows would otherwise regard it as the current session.
$script:Browser = [PSCustomObject]@{SourceAppUserModelId='chrome.exe'}
$script:Browser | Add-Member ScriptMethod GetPlaybackInfo { return [PSCustomObject]@{PlaybackStatus='Playing'} }
$script:Selection = @($script:Browser, $script:Fake)
$script:Manager = [PSCustomObject]@{}
$script:Manager | Add-Member ScriptMethod GetSessions { return $script:Selection }
if ((Get-SpotifySession).SourceAppUserModelId -ne 'Spotify.exe') { throw 'Wrong media source selected' }
$script:Selection = @($script:Browser)
if ($null -ne (Get-SpotifySession)) { throw 'Browser session was not excluded' }
Trace-HarnessPhase 'selector-checked'
function Get-SpotifySession {
    $script:DispatchClock = [Diagnostics.Stopwatch]::StartNew()
    Trace-HarnessPhase "request:$($request.id)"
    $script:Controls.IsRepeatEnabled = $request.id -ne 9
    return $script:Fake
}
function Await-WinRT($Operation, [Type]$ResultType) { return $Operation }
function Get-SpotifyAudio($Value = $null) {
    if ($null -ne $Value) { $script:Calls.Add("volume:$Value") }
    return @{Available=$true;Success=$true;Volume=0.3;Error=$null}
}
Trace-HarnessPhase 'ready'
Invoke-Expression $source.Substring($split)
Trace-HarnessPhase 'routing-complete'
[Console]::WriteLine((@{calls=@($script:Calls); metadata_calls=$script:MetadataCalls; dispatch_ms=@($script:Dispatch)} | ConvertTo-Json -Compress))
''', encoding="utf-8-sig")
    requests = [("snapshot", None), ("previous", None), ("play_pause", None), ("next", None),
                ("shuffle", True), ("shuffle", False), ("repeat", "off"), ("repeat", "track"), ("repeat", "list"),
                ("repeat", "track"), ("shuffle", "invalid"), ("unknown", None), ("volume", 0.3), ("volume", "invalid")]
    payload = "".join(json.dumps({"id": i, "action": action, "value": value}) + "\n"
                      for i, (action, value) in enumerate(requests))
    result = run_harness(
        [str(Path(__import__('os').environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
         '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness), str(helper), str(phase)],
        payload, phase,
    )
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert all(line["ok"] is True for line in lines[:9]), lines
    assert all(line["ok"] is False for line in lines[9:12]), lines
    assert lines[12]["ok"] is True and lines[12]["state"]["volume"] == 0.3
    assert lines[13]["ok"] is False
    assert lines[9]["error"] == "Spotify does not support this command"
    assert lines[-1]["calls"] == ["previous", "play_pause", "next", "shuffle:True", "shuffle:False",
                                     "repeat:None", "repeat:Track", "repeat:List", "volume:0.3"]
    # Slow metadata was requested once by snapshot, never before/after commands.
    assert lines[-1]["metadata_calls"] == 1
    assert lines[-1]["dispatch_ms"][0] < 200


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .NET Core Audio policy")
def test_core_audio_policy_targets_only_verified_unambiguous_sessions(tmp_path):
    audio = Path(__file__).parents[1] / "megamozg" / "spotify_audio.cs"
    script = tmp_path / "spotify-audio-policy.ps1"
    phase = tmp_path / "audio.phases"
    script.write_text(r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$source = [IO.File]::ReadAllText($args[0])
$fake = @'
namespace Brainalot {
 public sealed class FakeAudioSession : ISpotifyAudioSession {
  public string Path; public bool Shared; public float Value; public int Writes; public int Reads;
  public bool Disappear; public bool LoseResponse;
  public string ProcessPath { get { Reads++; return Disappear && Reads > 3 ? null : Path; } }
  public bool SharedProcess { get { return Shared; } }
  public float Level { get { return Value; } }
  public void SetLevel(float value) { Writes++; Value = value; if (LoseResponse) throw new System.Exception(); }
 }
}
'@
Add-Type -TypeDefinition ($source + $fake)
[IO.File]::WriteAllText($args[1], "ready`n")
function New-Fake([string]$Path, [float]$Value = 0.2) {
 $fake = New-Object Brainalot.FakeAudioSession
 $fake.Path = $Path; $fake.Value = $Value
 return $fake
}
$path = Join-Path $env:APPDATA 'Spotify/Spotify.exe'
$a = New-Fake $path 0.2
$b = New-Fake $path 0.6
$foreign = New-Fake (Join-Path $env:APPDATA 'Chrome/chrome.exe') 0.8
$spoof = New-Fake 'C:/Unverified/Spotify.exe' 0.9
$all = [Brainalot.ISpotifyAudioSession[]]@($a,$b,$foreign,$spoof)
$read = [Brainalot.SpotifyAudio]::Apply($all, $null)
if (-not $read.Available -or $null -ne $read.Volume -or $a.Writes -ne 0) { throw 'Mixed levels/read-only policy failed' }
$write = [Brainalot.SpotifyAudio]::Apply($all, 0.4)
if (-not $write.Success -or $write.Sessions -ne 2 -or $a.Writes -ne 1 -or $b.Writes -ne 1 -or $foreign.Writes -ne 0 -or $spoof.Writes -ne 0) { throw 'Target isolation failed' }
$otherInstall = New-Fake (Join-Path $env:LOCALAPPDATA 'Spotify/Spotify.exe')
$ambiguous = [Brainalot.SpotifyAudio]::Apply([Brainalot.ISpotifyAudioSession[]]@($a,$otherInstall),0.5)
if ($ambiguous.Available -or $a.Writes -ne 1 -or $otherInstall.Writes -ne 0) { throw 'Ambiguous installs were written' }
$shared = New-Fake $path
$shared.Shared = $true
$blocked = [Brainalot.SpotifyAudio]::Apply([Brainalot.ISpotifyAudioSession[]]@($shared),0.5)
if ($blocked.Available -or $shared.Writes -ne 0) { throw 'Shared process session was written' }
$gone = New-Fake $path
$gone.Disappear = $true
$changed = [Brainalot.SpotifyAudio]::Apply([Brainalot.ISpotifyAudioSession[]]@($gone),0.5)
if ($changed.Available -or $gone.Writes -ne 0) { throw 'Disappeared target was written' }
$lost = New-Fake $path
$lost.LoseResponse = $true
$unknown = [Brainalot.SpotifyAudio]::Apply([Brainalot.ISpotifyAudioSession[]]@($lost),0.5)
if ($unknown.Success -or $lost.Writes -ne 1 -or $unknown.Error -notlike '*partial*') { throw 'Lost response was replayed or hidden' }
$none = [Brainalot.SpotifyAudio]::Apply([Brainalot.ISpotifyAudioSession[]]@($foreign,$spoof),$null)
if ($none.Available -or $null -ne $none.Volume) { throw 'Missing Spotify session invented' }
[Console]::WriteLine((@{target_sessions=$write.Sessions;foreign_writes=$foreign.Writes;spoof_writes=$spoof.Writes;lost_response_writes=$lost.Writes} | ConvertTo-Json -Compress))
''', encoding="utf-8-sig")
    result = run_harness(
        [str(Path(__import__('os').environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
         '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), str(audio), str(phase)],
        "", phase,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"target_sessions": 2, "foreign_writes": 0,
                                       "spoof_writes": 0, "lost_response_writes": 1}
