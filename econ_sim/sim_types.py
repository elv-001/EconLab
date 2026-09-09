from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from collections import deque

class Good(str, Enum):
    FOOD = "food"
    WOOD = "wood"
    TOOLS = "tools"
    SHELTER = "shelter"
    FIBER = "fiber"
    CLOTHES = "clothes"

@dataclass(frozen=True)
class Recipe:
    name: str
    inputs: dict[Good, int]
    outputs: dict[Good, int]
    domain: SkillDomain
    min_skill: float

    def can_afford(self, inventory: dict[Good, int]) -> bool:
        return all(inventory.get(g, 0) >= qty for g, qty in self.inputs.items())

class SkillDomain(Enum):
    GATHERING = "gathering"      # forage, chop wood
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
    self_reliance: float
    inventory: dict[Good, int] = field(default_factory=lambda: {g: 0 for g in Good})
    skills: dict[SkillDomain, float] = field(default_factory=dict)
    memory: dict[int, CounterpartyMemory] = field(default_factory=dict)
    last_recipe: str | None = None
    recipe_counts: dict[str, int] = field(default_factory=dict)
    # FIFO food lots: (quantity, acquired_tick)
    food_lots: list[tuple[int, int]] = field(default_factory=list)
    tool_lots: list[int] = field(default_factory=list)
    
    current_goal: Good | None = None
    current_recipe: Recipe | None = None # current recipe being executed
    alive: bool = True
    has_shelter: bool = False

    comfort_debt: float = 0.0

    recent_recipes: deque[str] = field(default_factory=deque)

    def inventory_of(self, good: Good) -> int:
        if good == Good.TOOLS:
            return len(self.tool_lots)
        return self.inventory.get(good, 0)

    def add_good(self, good: Good, qty: int) -> None:
        self.inventory[good] = self.inventory.get(good, 0) + qty

    def remove_good(self, good: Good, qty: int) -> None:
        if good == Good.FOOD:
            raise ValueError("Use Agent food lot methods for food inventory")
        current = self.inventory.get(good, 0)
        if current < qty:
            raise ValueError(
                f"Agent {self.agent_id} insufficient {good.value}: have {current}, need {qty}"
            )
        self.inventory[good] = current - qty

@dataclass
class AgentArchetype:
    # should add risk_tolerance and time_preference
    name: str
    affinity_bias: dict[SkillDomain, float] = field(default_factory=lambda: {d: 1.0 for d in SkillDomain})
    self_reliance_range: tuple[float, float] = (0.7, 1.2)   # defaults match current global range
    goal_stickiness: float = 0.85                            # defaults match current global constant
    novice_penalty_exponent: float = 1.3
    profit_motivation: float = 0.8
    planning_depth_long: int = 3

ARCHETYPES: dict[str, AgentArchetype] = {
    "generalist": AgentArchetype(name="generalist"),

    "trader": AgentArchetype(
        name="trader",
        affinity_bias={d: 0.9 for d in SkillDomain},
        self_reliance_range=(0.5, 0.7),
        goal_stickiness=0.6,
        profit_motivation=1.2,
    ),
}
