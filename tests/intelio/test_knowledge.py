"""Shipped knowledge packs stay inside one client and can be overlaid."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from intelio.clients import CLIENTS, DESK, SECTIONS, pack_token
from intelio.knowledge import excerpt_for_client, foreign_markers, load_pack, shipped_root


def _seeds():
    path = Path(__file__).resolve().parents[2] / "evals" / "intelio_grader" / "seeds.py"
    spec = importlib.util.spec_from_file_location("intelio_grader_seeds_for_packs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pack_desks_match_the_grader_and_sections_are_complete(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    seeds = _seeds()
    assert DESK == seeds.DESK
    assert CLIENTS == seeds.CLIENTS
    for client in CLIENTS:
        pack = load_pack(client)
        assert pack["version"] == 1
        assert pack["sections"] == list(SECTIONS)
        assert pack["facts"]["desk"] == DESK[client]
        assert pack["facts"]["token"] == pack_token(client)
        assert seeds.secret(client) not in pack["text"]
        excerpt = excerpt_for_client(client)
        assert excerpt.startswith(f"Knowledge pack {client} v1:")
        assert pack_token(client) in excerpt
        for marker in foreign_markers(client):
            assert marker not in excerpt
        assert str(shipped_root() / client) == pack["path"]


def test_unknown_client_excerpt_is_empty():
    assert excerpt_for_client("other") == ""


def test_a_pack_that_quotes_another_client_is_refused(tmp_path):
    folder = tmp_path / "HHP"
    shutil.copytree(shipped_root() / "HHP", folder)
    (folder / "policies.md").write_text("PACKTOKEN_PRC\n", encoding="utf-8")
    with pytest.raises(ValueError, match="PRC"):
        load_pack("HHP", root=tmp_path)


def test_overlay_wins_only_when_pack_json_already_exists(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert "HHP" in load_pack("HHP")["path"]
    assert str(shipped_root()) in load_pack("HHP")["path"]

    overlay = home / "knowledge" / "HHP"
    shutil.copytree(shipped_root() / "HHP", overlay)
    manifest = json.loads((overlay / "pack.json").read_text(encoding="utf-8"))
    manifest["facts"]["decision"] = "Overlay decision for HHP only."
    (overlay / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    (overlay / "decisions.md").write_text(
        "HHP desk 4175. PACKTOKEN_HHP\nOverlay decision for HHP only.\n",
        encoding="utf-8",
    )

    packed = load_pack("HHP")
    assert packed["facts"]["decision"] == "Overlay decision for HHP only."
    assert packed["path"] == str(overlay)
    assert str(shipped_root()) in load_pack("PRC")["path"]
