from __future__ import annotations

import random
from typing import TYPE_CHECKING

from econ_sim.config import (
    GOAL_CHAIN_RECIPES, RECIPES, SimConfig, forage_yield_per_agent, RECIPE_BY_NAME, ENABLED_GOODS,
    TRADEABLE_GOODS
)

from econ_sim.sim_types import AgentState, CounterpartyMemory, Good, Order, Recipe, SkillDomain

if TYPE_CHECKING:
    from econ_sim.events import EventLog

class Agent:
    def __init__(
        self,
        agent_id: int,
        config: SimConfig,
        rng: random.Random,
        skill_affinities: dict[SkillDomain, float] | None = None,
    ) -> None:
        self.state = AgentState(agent_id=agent_id, money=config.initial_money, self_reliance=0.5)
        self.config = config
        self.rng = rng
        self.skill_affinities = skill_affinities or {}

        # Jitter starting money
        self.state.money += rng.uniform(-config.money_jitter, config.money_jitter)
        self.state.money = max(1.0, self.state.money)

        self.state.has_shelter = False

        self.state.current_goal = None
        self.state.alive = True
        self.state.self_reliance = rng.uniform(config.self_reliance_min, config.self_reliance_max)

        self.state.inventory[Good.FOOD] = max(
            0,
            config.initial_food
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
        if self.state.inventory[Good.FOOD] > 0:
            self.state.food_lots.append((self.state.inventory[Good.FOOD], 0))
        self.state.inventory[Good.WOOD] = max(
            0,
            config.initial_wood
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
        self.state.inventory[Good.TOOLS] = max(
            0,
            config.initial_tools
            + rng.randint(0, config.endowment_jitter // 2),
        )

    @property
    def agent_id(self) -> int:
        return self.state.agent_id

    def _shortage(self, good: Good) -> int:
        if good in self.config.targets():
            target = self.config.targets()[good]
            return max(0, target - self.state.inventory_of(good))
        return 0

    def _surplus(self, good: Good) -> int:
        if good in self.config.targets():
            target = self.config.targets()[good]
            threshold = target + self.config.surplus_buffer
            return max(0, self.state.inventory_of(good) - threshold)
        return 0

    def _urgency_weight(self, good: Good) -> float:
        weights = {
            Good.FOOD: self.config.food_urgency_weight,
            Good.WOOD: self.config.wood_urgency_weight,
            Good.TOOLS: self.config.tool_urgency_weight,
            Good.SHELTER: self.config.shelter_urgency_weight,
        }
        return weights[good]
    
    def _recipes_toward_good(self, goal: Good) -> list[Recipe]:
        return GOAL_CHAIN_RECIPES[goal]
    
    def _productivity(self, recipe_name: str) -> float:
        recipe = RECIPE_BY_NAME[recipe_name]
        skill = self.state.skills.get(recipe.domain, 1.0)
        affinity = self.skill_affinities.get(recipe.domain, 1.0)
        cap = self.config.skill_productivity_cap
        base = self.config.skill_productivity_base

        raw = base * skill * affinity
        if skill < recipe.min_skill:
            proficiency = skill / recipe.min_skill
            raw *= proficiency ** self.config.novice_penalty_exponent

        if self.config.shelter_required and not self.state.has_shelter and recipe_name != "shelter":
            raw *= self.config.shelter_productivity_loss
        return min(cap, raw)

    def _self_production_cost(self, recipe: Recipe, good: Good) -> float:
        productivity = self._productivity(recipe.name)
        if productivity <= 0:
            return float("inf")
        base_prices = self.config.base_prices()
        input_cost = sum(qty * base_prices.get(g, 1.0) for g, qty in recipe.inputs.items())
        expected_output = recipe.outputs.get(good, 0) * productivity
        if expected_output <= 0:
            return float("inf")  # will likely produce nothing — not a real option

        # cost per unit actually produced, accounting for waste from low skill
        return input_cost / expected_output

    # Compare to market price with self-reliance
    def _should_make_good(self, recipe: Recipe, good: Good, market_prices: dict[Good, float] | None = None) -> bool:
        # if the good can't be traded, the only choice is to make it
        if good not in TRADEABLE_GOODS:
            return True
        market_price = (market_prices or {}).get(good, self.config.base_prices()[good])
        make_cost = self._self_production_cost(recipe, good)
        skill = self.state.skills.get(recipe.domain, 1.0)

        if make_cost <= market_price * self.state.self_reliance or skill < recipe.min_skill:
            return True
        return False

    def _shelter_penalty_cost(self) -> float:
        """Estimate the economic cost of NOT having shelter, in the agent's own terms."""
        if self.state.has_shelter or self.config.shelter_required is False:
            return 0.0
        # Measures the economic value of the best recipe the agent could make if it had shelter as a score
        best_value = max(
            (self._score_recipe(r) for r in RECIPES if r.can_afford(self.state.inventory)),
            default=0.0,
        )
        # returns the discounted factor
        return best_value * self.config.shelter_productivity_loss

    def _choose_goal(self) -> Good | None:
        """Choose the most urgent good the agent wants to produce. Shortages only"""
        shortages = [
            (good, self._shortage(good) * self._urgency_weight(good))
            for good in ENABLED_GOODS
            if self._shortage(good) > 0
        ]

        shelter_cost = self._shelter_penalty_cost()
        if shelter_cost > 0 and self.config.shelter_required:
            shortages.append((Good.SHELTER, shelter_cost))

        if shortages:
            candidates = shortages
        else:
            self.state.current_goal = None
            return None

        best_good, best_score = max(candidates, key=lambda x: x[1])

        # Stay on the current goal unless something else has become
        # meaningfully more urgent. Avoids oscillation between two
        # shortages that are close in magnitude.
        if self.state.current_goal is not None:
            current_score = dict(candidates).get(self.state.current_goal)
            if current_score is not None and current_score >= best_score * self.config.goal_stickiness:
                return self.state.current_goal

        self.state.current_goal = best_good
        return best_good
    
    def _score_recipe(self, recipe: Recipe) -> float:
        """Heuristic score: how much this recipe closes inventory gaps."""
        score = 0.0 
        base_prices = self.config.base_prices()

        for good, qty in recipe.outputs.items():
            shortage = self._shortage(good)
            if shortage > 0:
                score += qty * self._urgency_weight(good)
            surplus = self._surplus(good)
            if surplus > 0:
                score += qty * self.config.sell_value_weight * base_prices[good] * 0.1

        for good, qty in recipe.inputs.items():
            surplus = self._surplus(good)
            if surplus >= qty:
                # incentivize using surplus inputs to produce something else
                score += 0.5
            elif self.state.inventory_of(good) >= qty:
                score -= 0.3 * self._urgency_weight(good)

        skill = self._productivity(recipe.name)
        score *= skill

        return score

    def _chain_score(self, recipe: Recipe, goal: Good, depth: int) -> float:
        """Score a recipe that is a prerequisite toward `goal` but doesn't
        produce it directly (e.g. chop_wood when the goal is food via tools).
        Discounted by how many hops away it is from the goal."""
        base = self._score_recipe(recipe)
        discount = self.config.chain_discount ** depth
        return base * discount * self._urgency_weight(goal)

    def choose_production(self, market_prices: dict[Good, float]) -> Recipe | None:
        feasible = [r for r in RECIPES if r.can_afford(self.state.inventory)]
        if not feasible:
            return None

        goal = self._choose_goal()

        if goal is not None:
            chain = self._recipes_toward_good(goal)
            chain_candidates = [r for r in feasible if r in chain] or feasible

            # looks at whether each good is worth producing
            should_produce = [r for r in chain_candidates 
                          if self._should_make_good(r, next(iter(r.outputs)), market_prices)]
            candidates = should_produce or chain_candidates
            scored = []
            for r in candidates:
                if r.outputs.get(goal, 0) > 0:
                    scored.append((self._score_recipe(r), r))
                else:
                    # figure out how many hops this recipe is from the goal
                    depth = self._chain_depth_of(r, goal, self.config.chain_depth)
                    scored.append((self._chain_score(r, goal, depth), r))
        else:
            # No urgent shortages: pick whatever is most efficient in general.
            scored = [(self._score_recipe(r), r) for r in feasible]

        scored.sort(key=lambda x: x[0], reverse=True)

        top_n = min(self.config.production_top_n, len(scored))
        top = scored[:top_n]
        weights = [max(s, 0.01) for s, _ in top]
        _, chosen = self.rng.choices(top, weights=weights, k=1)[0]
        return chosen

    def _chain_depth_of(self, recipe: Recipe, goal: Good, max_depth: int) -> int:
        """How many hops `recipe` sits from directly producing `goal`."""
        frontier = {goal}
        visited: set[Good] = set()
        for depth in range(max_depth):
            next_frontier: set[Good] = set()
            for good in frontier:
                if good in visited:
                    continue
                visited.add(good)
                for r in RECIPES:
                    if r.outputs.get(good, 0) > 0:
                        if r.name == recipe.name:
                            return depth
                        next_frontier.update(r.inputs.keys())
            frontier = next_frontier
        return max_depth

    def execute_production(
        self,
        recipe: Recipe,
        tick: int,
        num_forgers: int = 1,
    ) -> dict[str, int]:
        for good, qty in recipe.inputs.items():
            if good == Good.FOOD:
                self.remove_food(qty)
            elif good == Good.TOOLS:
                for _ in range(qty):
                    if not self.use_tool():
                        break
            else:
                self.state.remove_good(good, qty)

        productivity = self._productivity(recipe.name)
        outputs: dict[str, int] = {}
        for good, base_qty in recipe.outputs.items():
            qty = 0
            if recipe.name == "forage" and good == Good.FOOD:
                qty = forage_yield_per_agent(
                    self.config, num_forgers, productivity, base_qty
                )
            elif good == Good.TOOLS:
                self.add_tool(base_qty)
            elif good == Good.SHELTER:
                self.state.has_shelter = True
            else:
                qty = max(1, int(base_qty * productivity)) if base_qty > 0 else 0

            if good == Good.FOOD:
                self.add_food(qty, tick)
            else:
                self.state.add_good(good, qty)
            outputs[good.value] = qty

        self.state.last_recipe = recipe.name
        self.state.recipe_counts[recipe.name] = (
            self.state.recipe_counts.get(recipe.name, 0) + 1
        )
        self._apply_learning(recipe.name)
        return outputs

    def _apply_learning(self, recipe_name: str) -> None:
        recipe = RECIPE_BY_NAME[recipe_name]
        current = self.state.skills.get(recipe.domain, 1.0)
        gain = self.config.skill_gain_per_use
        cap = self.config.skill_productivity_cap
        self.state.skills[recipe.domain] = min(cap, current + gain)

    def reservation_bid_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = self.config.base_prices()[good]
        market_ref = (market_prices or {}).get(good, base)
        anchor = 0.7 * base + 0.3 * market_ref # allow some market influence

        shortage = self._shortage(good)
        target = self.config.targets()[good]
        if target == 0:
            return base
        urgency = min(1.0, shortage / target)
        return anchor * (1.0 + self.config.shortage_premium * urgency)

    def reservation_ask_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = self.config.base_prices()[good]
        market_ref = (market_prices or {}).get(good, base)
        anchor = 0.7 * base + 0.3 * market_ref # allow some market influence

        surplus = self._surplus(good)
        target = self.config.targets()[good]
        if target == 0:
            return base
        excess = min(1.0, surplus / max(1, target))
        return max(0.5, anchor * (1.0 - self.config.surplus_discount * excess))

    def generate_orders(
        self,
        order_id_start: int,
        market_prices: dict[Good, float] | None = None,
    ) -> tuple[list[Order], int]:
        orders: list[Order] = []
        next_id = order_id_start

        for good in TRADEABLE_GOODS:
            # Shelters aren't tradeable
            if good == Good.SHELTER:
                continue
            surplus = self._surplus(good)
            if surplus >= self.config.min_order_quantity:
                qty = max(
                    self.config.min_order_quantity,
                    int(surplus * self.config.max_order_fraction),
                )
                qty = min(qty, surplus)
                price = self.reservation_ask_price(good, market_prices)
                orders.append(
                    Order(
                        agent_id=self.agent_id,
                        good=good,
                        quantity=qty,
                        price=price,
                        is_bid=False,
                        order_id=next_id,
                    )
                )
                next_id += 1

            shortage = self._shortage(good)
            if shortage >= self.config.min_order_quantity:
                affordable_qty = int(
                    self.state.money
                    / max(0.01, self.reservation_bid_price(good, market_prices))
                )
                qty = min(shortage, max(self.config.min_order_quantity, affordable_qty))
                if qty >= self.config.min_order_quantity and self.state.money > 0:
                    price = self.reservation_bid_price(good, market_prices)
                    orders.append(
                        Order(
                            agent_id=self.agent_id,
                            good=good,
                            quantity=qty,
                            price=price,
                            is_bid=True,
                            order_id=next_id,
                        )
                    )
                    next_id += 1

        return orders, next_id

    def record_trade(self, counterparty_id: int, tick: int) -> None:
        mem = self.state.memory.get(counterparty_id)
        if mem is None:
            mem = CounterpartyMemory(
                agent_id=counterparty_id,
                trust=self.config.trust_initial,
            )
            self.state.memory[counterparty_id] = mem
        mem.trade_count += 1
        mem.last_tick = tick
        mem.trust = min(1.0, mem.trust + self.config.trust_gain_per_trade)

    def decay_memory(self, tick: int) -> None:
        stale = [
            aid
            for aid, mem in self.state.memory.items()
            if tick - mem.last_tick > self.config.memory_decay_ticks
        ]
        for aid in stale:
            del self.state.memory[aid]

    def total_wealth(self, prices: dict[Good, float]) -> float:
        goods_value = sum(
            self.state.inventory_of(g) * prices.get(g, 0) for g in Good
        )
        return self.state.money + goods_value

    def primary_activity(self) -> str | None:
        if not self.state.recipe_counts:
            return self.state.last_recipe
        return max(self.state.recipe_counts, key=self.state.recipe_counts.get)  # type: ignore[arg-type]

    def add_food(self, qty: int, tick: int) -> None:
        if qty <= 0:
            return
        self.state.food_lots.append((qty, tick))
        self._sync_food_inventory()

    def add_tool(self, qty: int) -> None:
        for _ in range(qty):
            self.state.tool_lots.append(self.config.tool_max_uses)
        self._sync_tool_inventory()

    def use_tool(self) -> bool:
        """Consume one use from an existing tool. Returns False if no tool available."""
        if not self.state.tool_lots:
            return False
        self.state.tool_lots[0] -= 1
        if self.state.tool_lots[0] <= 0:
            self.state.tool_lots.pop(0)
        self._sync_tool_inventory()
        return True

    def _sync_tool_inventory(self) -> None:
        self.state.inventory[Good.TOOLS] = len(self.state.tool_lots)

    def remove_food(self, qty: int) -> None:
        if qty <= 0:
            return
        remaining = qty
        kept: list[tuple[int, int]] = []
        for lot_qty, lot_tick in self.state.food_lots:
            if remaining <= 0:
                kept.append((lot_qty, lot_tick))
                continue
            take = min(lot_qty, remaining)
            lot_qty -= take
            remaining -= take
            if lot_qty > 0:
                kept.append((lot_qty, lot_tick))
        if remaining > 0:
            raise ValueError(
                f"Agent {self.agent_id} insufficient food: need {qty} more"
            )
        self.state.food_lots = kept
        self._sync_food_inventory()

    def decay_food(self, current_tick: int) -> int:
        """Remove food older than shelf life. Returns spoiled quantity."""
        if self.config.food_shelf_life_enabled is False:
            return 0
        shelf = self.config.food_shelf_life_ticks
        spoiled = 0
        fresh: list[tuple[int, int]] = []
        for lot_qty, lot_tick in self.state.food_lots:
            if current_tick - lot_tick >= shelf:
                spoiled += lot_qty
            else:
                fresh.append((lot_qty, lot_tick))
        self.state.food_lots = fresh
        self._sync_food_inventory()
        return spoiled

    def _sync_food_inventory(self) -> None:
        self.state.inventory[Good.FOOD] = sum(q for q, _ in self.state.food_lots)

    def consume_food(self) -> int:
        """Eat food for survival (oldest lots first). Returns amount consumed."""
        needed = self.config.food_consumption_per_tick
        available = self.state.inventory_of(Good.FOOD)
        
        if needed > available:
            self.state.alive = False
            return 0
        else:
            self.remove_food(needed)
        return needed

    def snapshot(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "money": round(self.state.money, 2),
            "inventory": {g.value: self.state.inventory_of(g) for g in Good},
            # ensures enum stuff gets outputted correctly
            "skills": {getattr(domain, "value", domain): round(skill, 3) for domain, skill in self.state.skills.items()},
            "last_recipe": self.state.last_recipe,
            "primary_activity": self.primary_activity(),
            "self_reliance": round(self.state.self_reliance, 3),
            "memory_size": len(self.state.memory),
        }


def create_agents(config: SimConfig, rng: random.Random) -> list[Agent]:
    agents: list[Agent] = []
    for i in range(config.num_agents):
        affinities = {
            # may use bell curve in future, more realistic
            domain: rng.uniform(config.skill_affinity_min, config.skill_affinity_max)
            for domain in SkillDomain
        }
        agents.append(Agent(i, config, rng, affinities))
    return agents 
