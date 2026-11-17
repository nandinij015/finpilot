"""Evidence ledger: every tool result is stored with an ID. Reports may only cite IDs that exist here."""
from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd


def to_jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return to_jsonable(o.reset_index().to_dict("records"))
    if isinstance(o, pd.Series):
        return to_jsonable(o.to_dict())
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return round(float(o), 4) if math.isfinite(o) else None
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return o.strftime("%Y-%m-%d")
    return o


@dataclass
class Evidence:
    id: str
    kind: str  # CALC | NEWS
    tool: str
    args: dict
    result: dict
    created_at: str
    data_source: Optional[str] = None


class EvidenceLedger:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}
        self._n = 0
        self._lock = threading.Lock()
        self.errors: list[dict] = []

    def reset(self) -> None:
        with self._lock:
            self._items, self._n, self.errors = {}, 0, []

    def add(self, kind: str, tool: str, args: dict, result: dict) -> Evidence:
        with self._lock:
            self._n += 1
            ev = Evidence(
                id=f"{kind}-{self._n:03d}", kind=kind, tool=tool, args=to_jsonable(args), result=result,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                data_source=result.get("data_source"),
            )
            self._items[ev.id] = ev
            return ev

    def log_error(self, tool: str, args: dict, error: str) -> None:
        with self._lock:
            self.errors.append({"tool": tool, "args": to_jsonable(args), "error": error})

    def get(self, eid: str) -> Optional[Evidence]:
        return self._items.get(eid)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def index(self) -> list[dict]:
        return [
            {"id": e.id, "kind": e.kind, "tool": e.tool, "args": e.args, "result_keys": list(e.result)[:12], "created_at": e.created_at}
            for e in self.all()
        ]

    def dump(self) -> list[dict]:
        return [asdict(e) for e in self.all()]


LEDGER = EvidenceLedger()
