from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class Good(str, Enum):
    FOOD = "food"
    WOOD = "wood"
    TOOLS = "tools"
    SHELTER = "shelter"
    FIBER = "fiber"
    CLOTHES = "clothes"

class Goal(str, Enum):
    PROFIT = "profit"
    EXPLORE = "explore"

class ActionType(str, Enum):
    PRODUCE = "produce"
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

@dataclass
class Action:
    action_type: ActionType
    good: Good | None = None
    recipe: Recipe | None = None
    quantity: int = 0

@dataclass
class Recipe:
    name: str
    inputs: dict[Good, int]
    outputs: dict[Good, int]
    domain: SkillDomain
    min_skill: float

    def can_afford(self, inventory: dict[Good, int]) -> bool:
        return all(inventory.get(g, 0) >= qty for g, qty in self.inputs.items())

class SkillDomain(Enum):
    GATHERING = "gathering"      # forage
    HARVESTING = "harvesting"    # harvesting resources like fiber
    LUMBERJACK = "lumber"        # chop wood
    CARPENTRY = "carpentry"      # craft tools
    FARMING = "farming"          # farming
    CONSTRUCTION = "construction" # build shelter
    WEAVING = "weaving"          # fiber to weave clothes

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
    ask_order_id: int
    bid_order_id: int


@dataclass
class InventoryLot:
    good: Good
    quantity: int
    created_tick: int

    # None = does not naturally decay
    shelf_life: int | None = None

    # For durable goods such as tools.
    # None = not a use-based good.
    uses_remaining: int | None = None


@dataclass
class AgentState:
    agent_id: int
    money: float
    self_reliance: float
    inventory: dict[Good, int] = field(default_factory=lambda: {g: 0 for g in Good})
    skills: dict[SkillDomain, float] = field(default_factory=dict)
    last_recipe: str | None = None
    recipe_counts: dict[str, int] = field(default_factory=dict)
    lots: list[InventoryLot] = field(default_factory=list)
    
    alive: bool = True
    has_shelter: bool = False

    current_production_action: Action | None = None
    current_trade_actions: list[Action] = field(default_factory=list)

    comfort_debt: float = 0.0

    recent_recipes: deque[str] = field(default_factory=deque)

    def inventory_of(self, good: Good) -> int:
        return self.inventory.get(good, 0)

@dataclass
class AgentArchetype:
    # should add risk_tolerance and time_preference
    name: str
    affinity_bias: dict[SkillDomain, float] = field(default_factory=lambda: {d: 1.0 for d in SkillDomain})
    self_reliance_range: tuple[float, float] = (0.7, 1.2)   # defaults match current global range
    novice_penalty_exponent: float = 1.3
    profit_motivation: float = 0.8

ARCHETYPES: dict[str, AgentArchetype] = {
    "generalist": AgentArchetype(name="generalist"),

    "trader": AgentArchetype(
        name="trader",
        affinity_bias={d: 0.9 for d in SkillDomain},
        self_reliance_range=(0.5, 0.7),
        profit_motivation=1.2,
    ),
}
