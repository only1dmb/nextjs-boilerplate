"""Append-only audit log.

Every signal, every risk decision, and every order attempt lands here as one
JSON object per line. This is the record of what the agent did with real
money, so it is only ever appended to -- never rewritten, never truncated.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=_encode)
        # Append + flush + fsync: a crash mid-session must not lose the record
        # of an order that already reached the broker.
        with self.path.open("a") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def read(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def entries_on(self, day: date) -> list[dict[str, Any]]:
        """All records whose UTC timestamp falls on the given date."""
        out = []
        prefix = day.isoformat()
        for record in self.read():
            if str(record.get("ts", "")).startswith(prefix):
                out.append(record)
        return out

    def filled_orders_on(self, day: date) -> list[dict[str, Any]]:
        return [r for r in self.entries_on(day) if r.get("event") == "order_submitted"]

    def last_trade_time(self, symbol: str) -> datetime | None:
        """When the agent last submitted an order for this symbol."""
        latest: datetime | None = None
        for record in self.read():
            if record.get("event") != "order_submitted":
                continue
            if record.get("symbol") != symbol:
                continue
            try:
                stamp = datetime.fromisoformat(record["ts"])
            except (KeyError, ValueError):
                continue
            if latest is None or stamp > latest:
                latest = stamp
        return latest

    def day_open_equity(self, day: date) -> float | None:
        """Portfolio equity recorded by the first snapshot of the day."""
        for record in self.entries_on(day):
            if record.get("event") == "snapshot" and record.get("equity") is not None:
                return float(record["equity"])
        return None
