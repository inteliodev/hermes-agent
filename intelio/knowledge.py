"""Versioned per-client knowledge packs.

Shipped packs live in ``intelio/knowledge/<client>/``. When ``HERMES_HOME`` is
set and that home already has ``knowledge/<client>/pack.json``, the home copy
wins. This module never creates a home directory and never reads the default
``~/.hermes`` path unless the process already pointed ``HERMES_HOME`` there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from intelio.clients import CLIENTS, DESK, SECTIONS, pack_token

_SHIPPED = Path(__file__).resolve().parent / "knowledge"
_EXCERPT_LIMIT = 1500


def shipped_root() -> Path:
    return _SHIPPED


def overlay_root() -> Optional[Path]:
    """Profile overlay, only when the process already set ``HERMES_HOME``."""
    raw = os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser() / "knowledge"
    return root if root.is_dir() else None


def pack_dir(client: str, *, root: Optional[Path] = None) -> Path:
    if client not in CLIENTS:
        raise ValueError(f"unknown client {client!r}")
    if root is not None:
        return root / client
    overlay = overlay_root()
    if overlay is not None and (overlay / client / "pack.json").is_file():
        return overlay / client
    return _SHIPPED / client


def load_pack(client: str, *, root: Optional[Path] = None) -> dict:
    """Return the pack manifest plus the concatenated section text."""
    folder = pack_dir(client, root=root)
    manifest_path = folder / "pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("client") != client:
        raise ValueError(f"{manifest_path} is for {manifest.get('client')!r}, not {client}")
    if int(manifest.get("version") or 0) < 1:
        raise ValueError(f"{manifest_path} has no version")
    facts = manifest.get("facts") or {}
    if facts.get("desk") != DESK[client] or facts.get("token") != pack_token(client):
        raise ValueError(f"{client} pack facts do not match the desk code or token")
    parts = [
        f"{client} desk {facts['desk']}. {facts['token']}",
        f"Decision: {facts['decision']}",
        f"SOP: {facts['sop']}",
        f"Template: {facts['template']}",
        f"Contact: {facts['contact']}",
        f"Policy: {facts['policy']}",
    ]
    found: list[str] = []
    for section in SECTIONS:
        path = folder / f"{section}.md"
        if not path.is_file():
            raise ValueError(f"{client} pack is missing {section}.md")
        found.append(section)
        parts.append(path.read_text(encoding="utf-8").strip())
    text = "\n".join(parts)
    for other in CLIENTS:
        if other != client and (pack_token(other) in text or DESK[other] in text):
            raise ValueError(f"{client} pack contains {other} material")
    return {
        "client": client,
        "version": int(manifest["version"]),
        "facts": facts,
        "sections": found,
        "text": text,
        "path": str(folder),
    }


def excerpt_for_client(client: str, *, root: Optional[Path] = None, limit: int = _EXCERPT_LIMIT) -> str:
    """Prompt text for the active client. Empty when the client is unknown."""
    if client not in CLIENTS:
        return ""
    pack = load_pack(client, root=root)
    body = pack["text"]
    if len(body) > limit:
        body = body[:limit]
    return f"Knowledge pack {client} v{pack['version']}:\n{body}"


def foreign_markers(client: str) -> list[str]:
    """Tokens and desk codes that belong to every other client."""
    if client not in CLIENTS:
        return []
    markers: list[str] = []
    for other in CLIENTS:
        if other == client:
            continue
        markers.append(pack_token(other))
        markers.append(DESK[other])
    return markers
