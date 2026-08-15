from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    PRODUCTION = "production"
    OFFER = "offer"
    BID = "bid"
    TRADE = "trade"
    TICK = "tick"
    INVARIANT_VIOLATION = "invariant_violation"


@dataclass
class Event:
    tick: int
    event_type: EventType
    agent_id: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "event_type": self.event_type.value,
            "agent_id": self.agent_id,
            "data": self.data,
        }


class EventLog:
    """Central structured log of every simulation action."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def record(
        self,
        tick: int,
        event_type: EventType,
        agent_id: int | None = None,
        **data: Any,
    ) -> None:
        self._events.append(
            Event(tick=tick, event_type=event_type, agent_id=agent_id, data=data)
        )

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def agent_history(self, agent_id: int) -> list[Event]:
        """Full history for a single agent (as actor or counterparty)."""
        result: list[Event] = []
        for e in self._events:
            if e.agent_id == agent_id:
                result.append(e)
            elif e.event_type == EventType.TRADE:
                if e.data.get("buyer_id") == agent_id or e.data.get("seller_id") == agent_id:
                    result.append(e)
        return result

    def events_of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def to_json(self) -> str:
        return json.dumps([e.to_dict() for e in self._events], indent=2)

    def clear(self) -> None:
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)
