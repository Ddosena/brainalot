"""Offline playback tests. No test issues a command to a live media session."""

import json
from pathlib import Path
import subprocess
import sys
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
                                  "previous": False, "repeat": False, "shuffle": False}


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell WinRT helper")
def test_real_powershell_helper_routes_fake_session_commands(tmp_path):
    # Execute the shipping routing loop with an entirely fake session. The
    # initialization loads WinRT types but Get-SpotifySession is replaced before
    # the loop executes, so no live manager or media command can be reached.
    helper = Path(__file__).parents[1] / "megamozg" / "spotify_bridge.ps1"
    harness = tmp_path / "spotify-fake.ps1"
    harness.write_text(r'''
$source = [IO.File]::ReadAllText($args[0])
$split = $source.IndexOf('while ($null -ne ($line = [Console]::ReadLine()))')
Invoke-Expression $source.Substring(0, $split)
$script:Calls = New-Object 'System.Collections.Generic.List[string]'
$script:Fake = [PSCustomObject]@{SourceAppUserModelId='Spotify.exe'}
$script:Controls = [PSCustomObject]@{IsPreviousEnabled=$true;IsNextEnabled=$true;IsPlayPauseToggleEnabled=$true;IsShuffleEnabled=$true;IsRepeatEnabled=$true}
$script:Fake | Add-Member ScriptMethod GetPlaybackInfo { return [PSCustomObject]@{PlaybackStatus='Paused';IsShuffleActive=$false;AutoRepeatMode='None';Controls=$script:Controls} }
$script:Fake | Add-Member ScriptMethod TryGetMediaPropertiesAsync { return [PSCustomObject]@{Title='Fake';Artist='Offline'} }
$script:Fake | Add-Member ScriptMethod TrySkipPreviousAsync { $script:Calls.Add('previous'); return $true }
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
function Get-SpotifySession {
    $script:Controls.IsRepeatEnabled = $request.id -ne 9
    return $script:Fake
}
function Await-WinRT($Operation, [Type]$ResultType) { return $Operation }
Invoke-Expression $source.Substring($split)
[Console]::WriteLine((@{calls=@($script:Calls)} | ConvertTo-Json -Compress))
''', encoding="utf-8-sig")
    requests = [("snapshot", None), ("previous", None), ("play_pause", None), ("next", None),
                ("shuffle", True), ("shuffle", False), ("repeat", "off"), ("repeat", "track"), ("repeat", "list"),
                ("repeat", "track"), ("shuffle", "invalid"), ("unknown", None)]
    payload = "".join(json.dumps({"id": i, "action": action, "value": value}) + "\n"
                      for i, (action, value) in enumerate(requests))
    result = subprocess.run(
        [str(Path(__import__('os').environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
         '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness), str(helper)],
        input=payload, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert all(line["ok"] is True for line in lines[:9]), lines
    assert all(line["ok"] is False for line in lines[9:-1]), lines
    assert lines[9]["error"] == "Spotify does not support this command"
    assert lines[-1]["calls"] == ["previous", "play_pause", "next", "shuffle:True", "shuffle:False",
                                     "repeat:None", "repeat:Track", "repeat:List"]
