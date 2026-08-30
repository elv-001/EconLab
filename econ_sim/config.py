from __future__ import annotations

from dataclasses import dataclass, field

from econ_sim.types import Good, Recipe, SkillDomain


@dataclass
class SimConfig:
    """All economic rules and coefficients live here."""

    seed: int = 42
    num_agents: int = 80
    num_ticks: int = 400

    # Starting endowments
    initial_money: float = 40.0
    initial_food: int = 3
    initial_wood: int = 1
    initial_tools: int = 0
    money_jitter: float = 25.0
    endowment_jitter: int = 3  # random +/- on starting goods

    input_cost_weight: float = 0.15  # penalty for using inputs in production
    goal_stickiness = 0.85

    # Survival: agents consume food each tick
    food_consumption_per_tick: int = 2

    # Food perishes after this many ticks in inventory
    food_shelf_life_ticks: int = 6
    food_shelf_life_enabled: bool = True

    # Inventory targets (agents try to maintain these levels)
    target_food: int = 6
    target_wood: int = 3
    target_tools: int = 1
    surplus_buffer: int = 1

    # Base reservation prices (used when no market history)
    base_price_food: float = 3.0
    base_price_wood: float = 2.0
    base_price_tools: float = 4.0

    # Price adjustment from shortage/surplus (fraction of base price)
    shortage_premium: float = 0.5
    surplus_discount: float = 0.3

    # Production scoring weights
    food_urgency_weight: float = 2.0
    wood_urgency_weight: float = 1.0
    tool_urgency_weight: float = 1.5
    sell_value_weight: float = 0.4

    # Bounded rationality: pick randomly among top-N scored recipes
    production_top_n: int = 2

    # Learning-by-doing: skill gain per use of a recipe
    skill_gain_per_use: float = 0.03
    skill_productivity_cap: float = 2.0
    skill_productivity_base: float = 1.0

    self_reliance_min: float = 0.7
    self_reliance_max: float = 1.6

    # punish not having the skill but mimicking it
    novice_penalty_exponent: float = 1.3

    # Trade memory
    memory_decay_ticks: int = 50
    trust_gain_per_trade: float = 0.05
    trust_initial: float = 0.5

    # Market
    min_order_quantity: int = 1
    max_order_fraction: float = 0.9  # max fraction of surplus to offer
    use_midpoint_pricing: bool = True

    # Random skill affinity at spawn (multiplier range)
    skill_affinity_min: float = 0.75
    skill_affinity_max: float = 1.35

    # Price discovery smoothing (EMA alpha; clamp as fraction of base price)
    price_ema_alpha: float = 0.15
    price_clamp_min_factor: float = 0.4
    price_clamp_max_factor: float = 10

    # Planning depth
    planning_depth_urgent: int = 1
    planning_depth_long: int = 3
    chain_depth: int = 4
    chain_discount: float = 0.8

    # Invariant checks
    check_invariants: bool = True

    def base_prices(self) -> dict[Good, float]:
        return {
            Good.FOOD: self.base_price_food,
            Good.WOOD: self.base_price_wood,
            Good.TOOLS: self.base_price_tools,
        }

    def targets(self) -> dict[Good, int]:
        return {
            Good.FOOD: self.target_food,
            Good.WOOD: self.target_wood,
            Good.TOOLS: self.target_tools,
        }


RECIPES: list[Recipe] = [
    Recipe(name="forage", inputs={}, outputs={Good.FOOD: 3},
           domain=SkillDomain.GATHERING, min_skill=1.0),
    Recipe(name="chop_wood", inputs={}, outputs={Good.WOOD: 2},
           domain=SkillDomain.GATHERING, min_skill=1.0),
    Recipe(
        name="craft_tools", 
           inputs={Good.WOOD: 3}, 
           outputs={Good.TOOLS: 1},
           domain=SkillDomain.CARPENTRY, 
           min_skill=1.2
        ),
    Recipe(
        name="hunt_farm",
        inputs={Good.TOOLS: 1},
        outputs={Good.FOOD: 7},
        domain=SkillDomain.FARMING,
        min_skill=1.3
    ), 
]

RECIPE_BY_NAME: dict[str, Recipe] = {r.name: r for r in RECIPES}

def _build_producers_by_good() -> dict[Good, list[Recipe]]:
    index: dict[Good, list[Recipe]] = {}
    for r in RECIPES:
        for good in r.outputs:
            index.setdefault(good, []).append(r)
    return index

_PRODUCERS_BY_GOOD = _build_producers_by_good()

def _chain_for_good(goal: Good) -> list[Recipe]:
    """All recipes relevant to reaching `goal`, direct or via prerequisites."""
    relevant: set[str] = set()
    frontier = {goal}
    visited: set[Good] = set()

    while frontier:                      # bounded by number of distinct goods
        good = frontier.pop()
        if good in visited:
            continue
        visited.add(good)
        for r in _PRODUCERS_BY_GOOD.get(good, []):
            relevant.add(r.name)
            frontier.update(r.inputs.keys())

    return [r for r in RECIPES if r.name in relevant]

GOAL_CHAIN_RECIPES: dict[Good, list[Recipe]] = {
    good: _chain_for_good(good) for good in Good
}

DEFAULT_CONFIG = SimConfig()

# ignore for now
def forage_yield_per_agent(
    config: SimConfig, num_forgers: int, productivity: float, base_yield: int
) -> int:
    """Shared foraging pool: more foragers means less food per person."""
    if num_forgers <= 0:
        return 0
    return RECIPES[0].outputs[Good.FOOD]
