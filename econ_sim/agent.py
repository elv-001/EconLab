from __future__ import annotations

import random
from typing import TYPE_CHECKING

from econ_sim.config import (
    GOAL_CHAIN_RECIPES, RECIPES, SimConfig, RECIPE_BY_NAME, ENABLED_GOODS,
    TRADEABLE_GOODS, ARCHETYPE_MIX
)

from econ_sim.sim_types import (
    AgentState, CounterpartyMemory, Good, Order, Recipe, SkillDomain, 
    AgentArchetype, ARCHETYPES,
)

if TYPE_CHECKING:
    from econ_sim.events import EventLog

class Agent:
    def __init__(
        self,
        agent_id: int,
        config: SimConfig,
        rng: random.Random,
        skill_affinities: dict[SkillDomain, float],
        archetype: AgentArchetype | None
    ) -> None:
        self.state = AgentState(agent_id=agent_id, money=config.initial_money, self_reliance=0.5)
        self.config = config
        self.rng = rng

        self.archetype = archetype or ARCHETYPES["generalist"]
        self.skill_affinities = skill_affinities
        self.state.self_reliance = rng.uniform(*self.archetype.self_reliance_range)

        # Jitter starting money
        self.state.money += rng.uniform(-config.money_jitter, config.money_jitter)
        self.state.money = max(1.0, self.state.money)

        self.state.has_shelter = False

        self.state.current_goal = None
        self.state.alive = True

        """
        if self.agent_id % 10 == 0:
            self.state.skills[SkillDomain.CARPENTRY] = 4.0
            """

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
        initial_tool_count = max(
            0,
            config.initial_tools + rng.randint(0, config.endowment_jitter // 2),
        )
        self.add_tool(initial_tool_count)

    @property
    def agent_id(self) -> int:
        return self.state.agent_id

    @property
    def tools_count(self) -> int:
        return len(self.state.tool_lots)

    def _shortage(self, good: Good) -> int:
        if good == Good.FOOD:
            return max(
                0,
                self.config.targets()[good]
                - self.state.inventory_of(good),
            )

        if good == Good.SHELTER:
            if self.state.has_shelter:
                return 0
            return 1

        # It finds shortages in goods that the current recipe needs
        recipe = self.state.current_recipe
        if recipe is None:
            return 0

        required = recipe.inputs.get(good, 0)

        return max(
            0,
            required - self.state.inventory_of(good),
        )

    def _surplus(self, good: Good) -> int:
        inventory = self.state.inventory_of(good)

        if good == Good.FOOD:
            target = self.config.targets()[Good.FOOD]
            return max(0, inventory - (target + self.config.surplus_buffer))

        # How much of this good does my actual occupation need to keep operating,
        # not just this tick's specific action?
        role_recipe_name = self.primary_activity()  # stable, not tick-volatile
        if role_recipe_name is None:
            return 0
        role_recipe = RECIPE_BY_NAME.get(role_recipe_name)
        reserve_needed = role_recipe.inputs.get(good, 0) if role_recipe else 0

        # Also reserve for whatever's actually queued this tick, so we don't
        # sell something out from under our own immediate plan.
        current_need = self.state.current_recipe.inputs.get(good, 0) if self.state.current_recipe else 0

        reserve = max(reserve_needed, current_need)
        return max(0, inventory - reserve)

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

        skill_factor = 1.0 + self.config.skill_effect * (skill - 1.0)
        affinity_factor = 1.0 + self.config.affinity_effect * (affinity - 1.0)

        cap = self.config.skill_productivity_cap

        raw = skill_factor * affinity_factor

        if skill < recipe.min_skill:
            proficiency = skill / recipe.min_skill
            raw *= proficiency ** self.archetype.novice_penalty_exponent

        if self.config.shelter_required and not self.state.has_shelter and recipe_name != "shelter":
            raw *= (1 - self.config.shelter_productivity_loss)

        return min(cap, raw)

    def _shelter_penalty_cost(self) -> float:
        """Economic cost of NOT having shelter, expressed on the same scale
        as other goal-urgency scores (shortage * urgency_weight)."""
        if self.state.has_shelter or not self.config.shelter_required:
            return 0.0

        feasible = [r for r in RECIPES if r.can_afford(self.state.inventory)]
        if not feasible:
            return 0.0

        best_value = max(
            self._score_recipe_for_goal(r, None, {}) for r in feasible
        )
        # best_value can be negative (a bad recipe); a negative "loss" doesn't
        # make sense as an urgency signal, so floor at 0.
        return max(0.0, best_value) * (1 - self.config.shelter_productivity_loss)

    def _food_critical(self, current_tick: int) -> bool:
        total = self.state.inventory_of(Good.FOOD)
        shelf = self.config.food_shelf_life_ticks
        # How much is about to expire in the next couple ticks?
        near_expiry = sum(
            qty for qty, produced_tick in self.state.food_lots
            if current_tick - produced_tick >= shelf - 1
        )
        safe_food = total - near_expiry
        return safe_food <= self.config.food_consumption_per_tick

    def choose_goal(self, current_tick: int) -> Good | None:
        """Choose the most urgent good the agent wants to produce. Shortages only"""
        if self._food_critical(current_tick):
            self.state.current_goal = Good.FOOD
            return Good.FOOD
        
        shortages = [
            (good, self._shortage(good) * self._urgency_weight(good))
            for good in ENABLED_GOODS
            if self._shortage(good) > 0 and good != Good.SHELTER
        ]

        shelter_cost = self._shelter_penalty_cost()
        if shelter_cost > 0:
            shortages.append((Good.SHELTER, shelter_cost))

        if shortages:
            candidates = shortages
        else:
            self.state.current_goal = None
            self.state.current_recipe = None
            return None

        best_good, best_score = max(candidates, key=lambda x: x[1])

        # Stay on the current goal unless something else has become
        # meaningfully more urgent. Avoids oscillation between two
        # shortages that are close in magnitude.
        if self.state.current_goal is not None:
            current_score = dict(candidates).get(self.state.current_goal)
            if current_score is not None and current_score >= best_score * self.archetype.goal_stickiness:
                return self.state.current_goal

        self.state.current_goal = best_good
        return best_good

    def _score_recipe_for_goal(self, recipe: Recipe, goal: Good | None, market_prices: dict[Good, float]) -> float:
        score = 0.0

        # If there are shortages, this increases score of recipe
        if goal is not None:
            depth = self._chain_depth_of(
                recipe,
                goal,
                self.config.chain_depth,
            )

            goal_pressure = (
                self._shortage(goal)
                * self._urgency_weight(goal)
            )

            # Increases score based on urgency while discounting based on hops
            score += (
                goal_pressure
                * self.config.chain_discount ** depth
            )
    
        # Factors in economic productivity (skillset)
        productivity = self._productivity(recipe.name)
        revenue = sum(
            qty * productivity * market_prices.get(
                    good,
                    self.config.base_prices()[good],
            )
            for good, qty in recipe.outputs.items()
            if good in TRADEABLE_GOODS
        )

        cost = max(0, sum(
            (qty - self.state.inventory_of(good)) * market_prices.get(
                good,
                self.config.base_prices()[good],
            )
            for good, qty in recipe.inputs.items()
        ))

        score += (revenue - cost) * self.archetype.profit_motivation
        return score

    def choose_production(self, market_prices: dict[Good, float], current_tick: int) -> Recipe | None:
        goal = self.state.current_goal
        if goal == Good.FOOD and self._food_critical(current_tick):
            # Get FOOD ASAP
            candidates = [r for r in RECIPES if r.outputs.get(Good.FOOD, 0) > 0 and r.can_afford(self.state.inventory)]
            if candidates:
                chosen = max(candidates, key=lambda r: r.outputs[Good.FOOD] * self._productivity(r.name))
                self.state.current_recipe = chosen
                return chosen

        chain = (self._recipes_toward_good(goal) if goal is not None else RECIPES)
        scored = []

        # Finds the value for each recipe
        for recipe in chain:
            score = self._score_recipe_for_goal(
                recipe,
                goal,
                market_prices,
            )
            scored.append((score, recipe))

        scored.sort(key=lambda x: x[0], reverse=True)

        top_n = min(self.config.production_top_n, len(scored))
        top = scored[:top_n]
        weights = [max(s, 0.01) for s, _ in top]
        _, chosen = self.rng.choices(top, weights=weights, k=1)[0]

        self.state.current_recipe = chosen
        return chosen

    def get_affordable_recipe(self, market_prices: dict[Good, float]) -> tuple[Recipe | None, bool]:
        """Called post-trade. Uses the SAME recipe chosen pre-trade, if now affordable;
        falls back only if it genuinely isn't."""
        chosen = self.state.current_recipe
        if chosen is not None and chosen.can_afford(self.state.inventory):
            return chosen, True

        """
        if chosen is not None and not chosen.can_afford(self.state.inventory):
            print(
                f"Fallback agent={self.state.agent_id} "
                f"wanted={chosen.name} "
                f"food={self.state.inventory.get(Good.FOOD, 0)} "
                f"wood={self.state.inventory.get(Good.WOOD, 0)} "
                f"tools={self.state.inventory.get(Good.TOOLS, 0)} "
                f"money={self.state.money:.2f}"
        )
        """

        # Fallback: the planned recipe still isn't affordable even after trading.
        # Pick the best currently-affordable option instead, without re-randomizing
        # the whole goal-directed decision.
        feasible = [r for r in RECIPES if r.can_afford(self.state.inventory)]
        if not feasible:
            return None, False
        scored = [(self._score_recipe_for_goal(r, self.state.current_goal, market_prices), r) for r in feasible]
        return max(scored, key=lambda x: x[0])[1], False

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
    ) -> dict[str, int]:
        for good, qty in recipe.inputs.items():
            if good == Good.FOOD:
                self.remove_food(qty)
            elif good == Good.TOOLS:
                for _ in range(qty):
                    success = self.use_tool()
                    if not success:
                        break
            else:
                self.state.remove_good(good, qty)

        productivity = self._productivity(recipe.name)
        outputs: dict[str, int] = {}
        for good, base_qty in recipe.outputs.items():
            qty = 0
            if good == Good.TOOLS:
                if productivity >= 1.0:
                    qty = max(1, int(base_qty * productivity))  # skilled carpenters can exceed base output
                else:
                    qty = 1 if productivity > 0 else 0 
                self.add_tool(int(qty))
            elif good == Good.SHELTER:
                skill = self.state.skills.get(recipe.domain, 1.0)
                if skill >= recipe.min_skill:
                    self.state.has_shelter = True
            else:
                # this makes it round to nearest number instead of 0, so foragers get 2 instead of 1 food
                # probably worth investigating in the future
                qty = max(1, int(round((base_qty * productivity)))) if base_qty > 0 else 0

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

        # There should be no skill to foraging, it is a minimum survival action
        if (recipe.domain == SkillDomain.GATHERING):
            return
        
        current = self.state.skills.get(recipe.domain, 1.0)
        cap = self.config.skill_productivity_cap
        
        progress = (current - 1.0) / (cap - 1.0) # percentage completion compared to max
        effective_gain = self.config.skill_gain_per_use * (1.0 - progress) ** self.config.skill_gain_decay_exponent

        self.state.skills[recipe.domain] = min(cap, current + effective_gain)

    def reservation_bid_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = self.config.base_prices()[good]
        market_ref = (market_prices or {}).get(good, base)
        anchor = 0.7 * base + 0.3 * market_ref
        shortage = self._shortage(good)

        if good == Good.FOOD:
            target = self.config.targets()[good]
            urgency = min(1.0, shortage / max(1, target))
        else:
            shortage = self._shortage(good)
            urgency = 1.0 if shortage > 0 else 0.0

        return anchor * (
            1.0 + self.config.shortage_premium * urgency
        )

    def reservation_ask_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = self.config.base_prices()[good]
        market_ref = (market_prices or {}).get(good, base)
        anchor = 0.7 * base + 0.3 * market_ref # allow some market influence

        surplus = self._surplus(good)
        if surplus <= 0:
            return anchor
        
        return max(0.5, anchor * (1.0 - self.config.surplus_discount * surplus))

    def generate_orders(
        self,
        order_id_start: int,
        market_prices: dict[Good, float] | None = None,
    ) -> tuple[list[Order], int]:
        orders: list[Order] = []
        next_id = order_id_start

        for good in TRADEABLE_GOODS:
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

    def remove_tool(self, qty: int) -> None:
        for _ in range(qty):
            if self.state.tool_lots:
                self.state.tool_lots.pop(0)
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
            #print(f"DEATH REASON: {self.state.current_goal}, recipe: {self.state.current_recipe}")
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

def create_agents(
    config: SimConfig,
    rng: random.Random,
) -> list[Agent]:
    """Create agents, optionally drawn from a mix of personality archetypes.

    archetype_mix: e.g. {"generalist": 0.5, "hoarder": 0.5}. Weights don't need
    to sum to 1 — rng.choices normalizes them. Defaults to 100% generalist,
    which reproduces the existing validated baseline exactly.
    """
    archetype_mix = ARCHETYPE_MIX or {"generalist": 1.0}
    names = list(archetype_mix.keys())
    weights = list(archetype_mix.values())

    invalid_names = [name for name in names if name not in ARCHETYPES]
    if invalid_names:
        raise ValueError(f"Invalid Archetype Name: {invalid_names}")

    agents: list[Agent] = []
    for i in range(config.num_agents):
        archetype = ARCHETYPES[rng.choices(names, weights=weights, k=1)[0]]

        affinities = {
            # may use bell curve in future, more realistic
            domain: rng.uniform(config.skill_affinity_min, config.skill_affinity_max)
            * archetype.affinity_bias.get(domain, 1.0)
            for domain in SkillDomain
        }
        agents.append(Agent(i, config, rng, affinities, archetype))
    return agents
