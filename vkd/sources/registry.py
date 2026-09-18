"""Offline, hash-checked access to the A1 archive. No application-type dependency."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


class RegistryError(ValueError):
    """The archive cannot substantiate its declared content or metadata."""


def utc(value: str | datetime) -> datetime:
    """Parse an aware timestamp and normalize it to UTC; never assume a timezone."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a timezone-aware timestamp: {value!r}") from exc


def iso_utc(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


class SourceRegistry:
    """A private archive reader, not a replacement for the shared Manifest type.

    All indexed releases are verified when loaded. Returned metadata is copied;
    a caller cannot mutate another session's archive. Raw bytes are rechecked on
    access, so changing a file after loading never silently changes the snapshot.
    """

    def __init__(self, repo_root: str | Path, registry_path: str = "data/source_registry_2024/registry.json"):
        self._root = Path(repo_root).resolve()
        self._records: dict[str, dict[str, Any]] = {}
        self._source_ids: set[str] = set()
        index = self._json(self._path(registry_path).read_bytes(), registry_path)
        if index.get("schema_version") != 1 or not isinstance(index.get("sources"), dict):
            raise RegistryError("Unsupported A1 registry schema")
        documents: dict[str, dict] = {}
        try:
            for source_id, source in index["sources"].items():
                self._source_ids.add(source_id)
                path = source["records_path"]
                content = self._checked_bytes(path, source["records_sha256"])
                document = documents.setdefault(path, self._json(content, path))
                if document.get("schema_version") != 1:
                    raise RegistryError(f"Unsupported records schema: {path}")
                refs = source["records"]
                if source["record_count"] != len(refs):
                    raise RegistryError(f"Record count mismatch: {source_id}")
                for raw_id, ref in refs.items():
                    position = ref["record_index"]
                    if type(position) is not int or position < 0:
                        raise RegistryError(f"Invalid record index: {raw_id}")
                    record = document["records"][position]
                    if (record["source_id"] != source_id or record["raw_record_id"] != raw_id
                            or record["sha256"] != ref["sha256"]
                            or record["release_id"] != ref["release_id"] or raw_id in self._records):
                        raise RegistryError(f"Record identity mismatch: {raw_id}")
                    self._validate_record(record)
                    self._verify_evidence(record)
                    for response in record.get("response_refs", []):
                        self._checked_bytes(response["raw_path"], response["sha256"])
                    self._records[raw_id] = deepcopy(record)
            if index["record_count"] != len(self._records):
                raise RegistryError("Total record count mismatch")
        except (KeyError, TypeError, IndexError) as exc:
            raise RegistryError(f"Malformed registry metadata: {exc}") from exc

    def _path(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute():
            raise RegistryError("Archive paths must be repository-relative")
        path = (self._root / candidate).resolve()
        if not path.is_relative_to(self._root):
            raise RegistryError("Archive path escapes the repository")
        return path

    @staticmethod
    def _json(content: bytes, label: str) -> dict:
        try:
            value = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RegistryError(f"Invalid JSON: {label}") from exc
        if not isinstance(value, dict):
            raise RegistryError(f"Expected a JSON object: {label}")
        return value

    def _checked_bytes(self, path: str, sha256: str) -> bytes:
        content = self._path(path).read_bytes()
        if hashlib.sha256(content).hexdigest() != sha256:
            raise RegistryError(f"SHA-256 mismatch: {path}")
        return content

    def _verify_evidence(self, record: dict) -> None:
        content = self._checked_bytes(record["raw_path"], record["sha256"])
        if type(record["bytes"]) is not int or len(content) != record["bytes"]:
            raise RegistryError(f"Byte length mismatch: {record['raw_record_id']}")

    @staticmethod
    def _validate_record(record: dict) -> None:
        if record["version"] != "sha256:" + record["sha256"]:
            raise RegistryError("Content version does not match SHA-256")
        if not record.get("strict_replay_eligibility"):
            raise RegistryError("Missing historical eligibility status")
        try:
            for name in ("fetched_utc", "published_utc", "available_utc", "created_utc", "valid_from_utc", "valid_to_utc"):
                if name not in record:
                    raise RegistryError(f"Missing timestamp field: {name}")
                if record[name] is not None:
                    utc(record[name])
            if record["fetched_utc"] is None:
                raise RegistryError("Missing retrieval time")
            bounds = (record["valid_from_utc"], record["valid_to_utc"])
            if all(bounds) and utc(bounds[0]) >= utc(bounds[1]):
                raise RegistryError("Reversed validity interval")
            channel_ids = set()
            for channel in record["channels"]:
                if not channel.get("unit") or channel["channel_id"] in channel_ids:
                    raise RegistryError("Missing channel unit or duplicate channel")
                channel_ids.add(channel["channel_id"])
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._source_ids))

    def records(self, source_id: str) -> list[dict[str, Any]]:
        if source_id not in self._source_ids:
            raise KeyError(f"Unknown source: {source_id}")
        return [deepcopy(r) for r in self._records.values() if r["source_id"] == source_id]

    def record(self, raw_record_id: str) -> dict[str, Any]:
        return deepcopy(self._records[raw_record_id])

    def raw_bytes(self, raw_record_id: str) -> bytes:
        record = self._records[raw_record_id]
        return self._checked_bytes(record["raw_path"], record["sha256"])
