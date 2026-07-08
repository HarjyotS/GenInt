"""Asset manifest data structures + JSON I/O for asset_plan.json / asset_manifest.json."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssetEntry:
    type: str
    source: str  # "local" | "generated" | "none"
    path: str | None
    note: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "source": self.source, "path": self.path, "note": self.note}


def build_asset_plan(types: list[str]) -> dict:
    return {"requested_types": types}


def build_asset_manifest(entries: dict[str, AssetEntry], notes: list[str]) -> dict:
    return {
        "assets": {t: e.to_dict() for t, e in entries.items()},
        "notes": notes,
    }
