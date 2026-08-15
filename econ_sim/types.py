from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Good(str, Enum):
    FOOD = "food"
    WOOD = "wood"
    TOOLS = "tools"


@dataclass(frozen=True)
class Recipe:
    name: str
    inputs: dict[Good, int]
    outputs: dict[Good, int]

    def can_afford(self, inventory: dict[Good, int]) -> bool:
        return all(inventory.get(g, 0) >= qty for g, qty in self.inputs.items())


@dataclass
class Order:
    agent_id: int
    good: Good
    quantity: int
    price: float
    is_bid: bool
    order_id: int


@dataclass
class TradeRecord:
    tick: int
    buyer_id: int
    seller_id: int
    good: Good
    quantity: int
    price: float


@dataclass
class CounterpartyMemory:
    """Short memory of recent trades with a counterparty."""
    agent_id: int
    trade_count: int = 0
    last_tick: int = 0
    trust: float = 0.5


@dataclass
class AgentState:
    agent_id: int
    money: float
    inventory: dict[Good, int] = field(default_factory=lambda: {g: 0 for g in Good})
    skills: dict[str, float] = field(default_factory=dict)
    memory: dict[int, CounterpartyMemory] = field(default_factory=dict)
    last_recipe: str | None = None
    recipe_counts: dict[str, int] = field(default_factory=dict)

    def inventory_of(self, good: Good) -> int:
        return self.inventory.get(good, 0)

    def add_good(self, good: Good, qty: int) -> None:
        self.inventory[good] = self.inventory.get(good, 0) + qty

    def remove_good(self, good: Good, qty: int) -> None:
        current = self.inventory.get(good, 0)
        if current < qty:
            raise ValueError(
                f"Agent {self.agent_id} insufficient {good.value}: have {current}, need {qty}"
            )
        self.inventory[good] = current - qty
