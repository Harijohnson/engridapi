"""Committed raw-data snapshots (D10): gzipped JSON exactly as Elexon returned it.

File shape: {"dataset", "endpoint", "request_params", "fetched_at", "row_count", "data": [...]}.
The loader feeds `data` through the same parser and validator as the live API path.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.market_data.models import Dataset

SNAPSHOT_FILES: dict[Dataset, str] = {
    Dataset.WINDFOR: "windfor.json.gz",
    Dataset.FUELHH: "fuelhh_wind.json.gz",
}


class SnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    dataset: Dataset
    endpoint: str
    request_params: dict[str, Any]
    fetched_at: str
    rows: list[dict[str, Any]]


def write_snapshot(
    path: Path,
    *,
    dataset: Dataset,
    endpoint: str,
    request_params: dict[str, Any],
    fetched_at: datetime,
    rows: list[dict[str, Any]],
) -> None:
    payload = {
        "dataset": dataset.value,
        "endpoint": endpoint,
        "request_params": request_params,
        "fetched_at": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "row_count": len(rows),
        "data": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the gzip bytes reproducible for the same content.
    with gzip.GzipFile(path, mode="wb", mtime=0) as fh:
        fh.write(json.dumps(payload, separators=(",", ":")).encode())


def read_snapshot(path: Path) -> Snapshot:
    with gzip.open(path, mode="rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SnapshotError(f"{path} is not a snapshot file")
    try:
        dataset = Dataset(payload["dataset"])
    except (KeyError, ValueError) as exc:
        raise SnapshotError(f"{path} has an unknown dataset") from exc
    if payload.get("row_count") != len(payload["data"]):
        raise SnapshotError(f"{path} row_count does not match its data")
    return Snapshot(
        dataset=dataset,
        endpoint=str(payload.get("endpoint", "")),
        request_params=dict(payload.get("request_params", {})),
        fetched_at=str(payload.get("fetched_at", "")),
        rows=payload["data"],
    )
