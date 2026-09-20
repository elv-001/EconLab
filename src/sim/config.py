from __future__ import annotations

import typing
from dataclasses import dataclass, field

from sim.sim_types import Good, Recipe, SkillDomain


@dataclass
class SimConfig:
    """All economic rules and coefficients live here."""

    seed: int = 42
    num_agents: int = 80
    num_ticks: int = 400

    max_trades_per_tick: int = 2

    # Starting endowments
    initial_money: float = 40.0
    initial_food: int = 3
    initial_wood: int = 1
    initial_tools: int = 1
    initial_fiber: int = 3
    money_jitter: float = 25.0
    endowment_jitter: int = 3  # random +/- on starting goods

    input_cost_weight: float = 0.15  # penalty for using inputs in production

    # Survival: agents consume food each tick
    food_consumption_per_tick: int = 1
    gathering_congestion_k: float = 6.0  # tune: higher = crowding bites harder
    gathering_sustainable_share: float = 0.05 # what % of population should forage
    gathering_productivity_floor: float = 0.65
    congestion_ema_alpha: float = 0.3

    # Food perishes after this many ticks in inventory
    food_shelf_life_enabled: bool = True

    GOODS_SHELF_LIFE: typing.ClassVar = {
        Good.FOOD: 5,
        Good.WOOD: 50,
        Good.CLOTHES: 40,
        Good.FIBER: 30,
    }

    _reasonable_stock_cache: dict[Good, int] = field(default_factory=dict)

    # food consumption should increase without shelter
    shelter_required: bool = True
    shelter_productivity_loss = 0.25  # how much producitivity is lost without shelter

    # Inventory targets (agents try to maintain these levels)
    target_food: int = 7
    target_shelter: int = 1
    surplus_buffer: int = 1

    # Clothing
    clothing_enabled: bool = True
    target_clothes: int = 5
    comfort_consumption_per_tick: float = 1 # slower "wear rate" than food's per-tick eating
    comfort_urgency_weight: float = 1.6        # below food/tools, above pure luxury
    comfort_productivity_loss: float = 0.25

    # Base reservation prices (used when no market history)
    base_price_food: float = 3.0
    base_price_wood: float = 2.0
    base_price_tools: float = 7.0
    base_price_fiber: float = 1.5
    base_price_clothes: float = 8.0

    # SimConfig
    tool_max_uses: int = 5   # a tool survives this many farm cycles
    tool_break_chance: float = 0.1  # optional: random breakage instead of/alongside fixed uses
    tool_max_lifespan: int = 50

    # Bounded rationality: pick randomly among top-N scored recipes
    production_top_n: int = 3

    # Learning-by-doing: skill gain per use of a recipe
    skill_gain_per_use: float = 0.03
    skill_productivity_cap: float = 1.6
    skill_productivity_base: float = 1.0
    skill_gain_decay_exponent: float = 3
    skill_decay_per_tick: float = 0.05
    skill_decay_grace_period: int = 5

    carrying_cost_rate: float = 0.03
    carrying_cost_horizon: int = 10

    # Market
    use_midpoint_pricing: bool = True

    # Random skill affinity at spawn (multiplier range)
    skill_affinity_min: float = 0.75
    skill_affinity_max: float = 1.35

    # impact of skill to productivity; higher value should magnify comparative advantage
    skill_effect: float = 1
    affinity_effect: float = 0.7

    # Price discovery smoothing (EMA alpha; clamp as fraction of base price)
    price_ema_alpha: float = 0.1
    price_clamp_min_factor: float = 0.1
    price_clamp_max_factor: float = 10

    # Invariant checks
    check_invariants: bool = True

    def base_prices(self) -> dict[Good, float]:
        return {
            Good.FOOD: self.base_price_food,
            Good.WOOD: self.base_price_wood,
            Good.TOOLS: self.base_price_tools,
            Good.FIBER: self.base_price_fiber,
            Good.CLOTHES: self.base_price_clothes,
            Good.SHELTER: 8.0,
        }

    def targets(self) -> dict[Good, int]:
        result =  {
            Good.FOOD: self.target_food, Good.CLOTHES: self.target_clothes
        }
        if self.shelter_required:
            result[Good.SHELTER] = self.target_shelter
        return result

    def reasonable_stock(self, good: Good) -> int:
        if good not in self._reasonable_stock_cache:
            targets = self.targets()
            base = targets.get(good, 0)
            max_input = max((r.inputs.get(good, 0) for r in RECIPES), default=0)
            self._reasonable_stock_cache[good] = max(base, max_input * 10)
        return self._reasonable_stock_cache[good]

# ARCHETYPE MIX
ARCHETYPE_MIX: dict[str, float] = {
    "generalist": 1.0,
    #"trader": 0.5,
}

ALL_RECIPES: list[Recipe] = [
    Recipe(name="forage", inputs={}, outputs={Good.FOOD: 3},
           domain=SkillDomain.GATHERING, min_skill=1.0),
    Recipe(name="chop_wood", inputs={}, outputs={Good.WOOD: 2},
           domain=SkillDomain.LUMBERJACK, min_skill=1.0),
    Recipe(
        name="craft_tools", 
           inputs={Good.WOOD: 3}, 
           outputs={Good.TOOLS: 1},
           domain=SkillDomain.CARPENTRY,
           min_skill=1.3,
        ),
    Recipe(
        name="farm",
        inputs={Good.TOOLS: 1},
        outputs={Good.FOOD: 6},
        domain=SkillDomain.FARMING,
        min_skill=1.3,
    ),
    Recipe(
        name="shelter",
        inputs={Good.WOOD: 3, Good.TOOLS: 1},
        outputs={Good.SHELTER: 1},
        domain=SkillDomain.CONSTRUCTION,
        min_skill=1.0,
    ),
    Recipe(name="gather_fiber", 
           inputs={}, 
           outputs={Good.FIBER: 2},
           domain=SkillDomain.HARVESTING, 
           min_skill=1.0, 
    ),
    Recipe(name="weave_cloth", 
           inputs={Good.FIBER: 3, Good.TOOLS: 1}, 
           outputs={Good.CLOTHES: 6},
           domain=SkillDomain.WEAVING, 
           min_skill=1.4,
    ),
]

RECIPE_BY_NAME: dict[str, Recipe] = {r.name: r for r in ALL_RECIPES}

def _filter_recipes_by_config() -> list[Recipe]:
    """Filter recipes based on SimConfig settings."""
    filtered: list[Recipe] = []
    for r in ALL_RECIPES:
        if r.name == "shelter" and not SimConfig.shelter_required:
            continue
        if (r.name == "gather_fiber" or r.name == "weave_cloth") and not SimConfig.clothing_enabled:
            continue

        filtered.append(r)
    return filtered

def _build_producers_by_good(filtered: list[Recipe]) -> dict[Good, list[Recipe]]:
    index: dict[Good, list[Recipe]] = {}
    for r in filtered:
        for good in r.outputs:
            index.setdefault(good, []).append(r)
    return index

RECIPES = _filter_recipes_by_config()
_PRODUCERS_BY_GOOD = _build_producers_by_good(RECIPES)

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

ENABLED_GOODS = [
    Good.FOOD,
    Good.WOOD,
    Good.TOOLS,
]

TRADEABLE_GOODS = [
    Good.FOOD,
    Good.WOOD,
    Good.TOOLS,
]

if SimConfig.shelter_required:
    ENABLED_GOODS.append(Good.SHELTER)
if SimConfig.clothing_enabled:
    ENABLED_GOODS.append(Good.FIBER)
    ENABLED_GOODS.append(Good.CLOTHES)
    TRADEABLE_GOODS.append(Good.FIBER)
    TRADEABLE_GOODS.append(Good.CLOTHES)

GOAL_CHAIN_RECIPES: dict[Good, list[Recipe]] = {
    good: _chain_for_good(good) for good in ENABLED_GOODS
}

DEFAULT_CONFIG = SimConfig()
