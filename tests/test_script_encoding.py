from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_powershell_scripts_have_exactly_one_bom():
    # Windows PowerShell 5.1 reads UTF-8 only with a BOM; a doubled BOM makes «param(» unparseable
    # and the service supervisor start silently (2026-09-25).
    for path in SCRIPTS.glob("*.ps1"):
        data = path.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf"), path.name
        assert not data.startswith(b"\xef\xbb\xbf\xef\xbb\xbf"), path.name
