"""Portable, hash-verified CSV snapshots and explicit per-module configuration."""

from __future__ import annotations

import gzip
import base64
import hashlib
import json
from pathlib import Path
import tomllib

from vkd.sources.catalogue import parse_catalogue
from vkd.sources.registry import utc
from .screening import ScreeningConfig


def load_config(path=None):
    path = (
        Path(path)
        if path
        else Path(__file__).resolve().parents[2] / "config/conjunctions.toml"
    )
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if set(data) != {"screening"}:
        raise ValueError("Expected only [screening] in conjunction configuration")
    return ScreeningConfig(**data["screening"])


def load_snapshot(folder):
    """Return (catalogues, manifest). Saved raw bytes must travel with the report."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    catalogues = []
    for record in manifest["records"]:
        name = record["file"]
        if Path(name).name != name:
            raise ValueError("Snapshot raw file must be in the manifest directory")
        with gzip.open(folder / name, "rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if (
            len(raw) > 4 * 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError(f"Catalogue snapshot digest/size mismatch: {name}")
        catalogue = parse_catalogue(
            raw,
            available_utc=utc(record["available_utc"]),
            url=record["url"],
            scope=record["scope"],
            source_id=record["source_id"],
        )
        catalogue.metadata.update(
            raw_path=str(folder / name),
            compression="gzip",
            availability_proof=record["availability_proof"],
        )
        catalogues.append(catalogue)
    return tuple(catalogues), manifest


def source_records(catalogues):
    """A4-compatible self-contained raw records for B's existing ZIP export.

    Export the uncompressed original CSV entity, not .gz as if it were CSV.
    A hash check prevents a mutable cache path changing a saved calculation.
    """
    records = {}
    for catalogue in catalogues:
        metadata = dict(catalogue.metadata)
        path = Path(metadata["raw_path"])
        if metadata.get("compression") == "gzip":
            with gzip.open(path, "rb") as handle:
                raw = handle.read(4 * 1024 * 1024 + 1)
        else:
            with path.open("rb") as handle:
                raw = handle.read(4 * 1024 * 1024 + 1)
        if (
            len(raw) > 4 * 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != metadata["sha256"]
        ):
            raise ValueError("Raw catalogue bytes changed since calculation")
        records[metadata["raw_record_id"]] = {
            "metadata": metadata,
            "encoding": "base64",
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }
    return records
