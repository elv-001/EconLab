from __future__ import annotations

import random
from typing import TYPE_CHECKING

from econ_sim.config import RECIPES, SimConfig
from econ_sim.types import AgentState, CounterpartyMemory, Good, Order, Recipe

if TYPE_CHECKING:
    from econ_sim.events import EventLog


class Agent:
    def __init__(
        self,
        agent_id: int,
        config: SimConfig,
        rng: random.Random,
        skill_affinities: dict[str, float] | None = None,
    ) -> None:
        self.state = AgentState(agent_id=agent_id, money=config.initial_money)
        self.config = config
        self.rng = rng
        self.skill_affinities = skill_affinities or {}

        # Jitter starting money
        self.state.money += rng.uniform(-config.money_jitter, config.money_jitter)
        self.state.money = max(1.0, self.state.money)

        self.state.inventory[Good.FOOD] = max(
            0,
            config.initial_food
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
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

        for recipe in RECIPES:
            affinity = rng.uniform(config.skill_affinity_min, config.skill_affinity_max)
            self.state.skills[recipe.name] = affinity

    @property
    def agent_id(self) -> int:
        return self.state.agent_id

    def _shortage(self, good: Good) -> int:
        target = self.config.targets()[good]
        return max(0, target - self.state.inventory_of(good))

    def _surplus(self, good: Good) -> int:
        target = self.config.targets()[good]
        threshold = target + self.config.surplus_buffer
        return max(0, self.state.inventory_of(good) - threshold)

    def _urgency_weight(self, good: Good) -> float:
        weights = {
            Good.FOOD: self.config.food_urgency_weight,
            Good.WOOD: self.config.wood_urgency_weight,
            Good.TOOLS: self.config.tool_urgency_weight,
        }
        return weights[good]

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
                score += 0.5
            elif self.state.inventory_of(good) >= qty:
                score -= 0.3 * self._urgency_weight(good)

        skill = self._productivity(recipe.name)
        score *= skill

        if recipe.name == self.state.last_recipe:
            score *= 1.1

        if recipe.name == "hunt_farm" and self.state.inventory_of(Good.TOOLS) >= 1:
            score *= 1.8
        if recipe.name == "forage":
            score *= 0.7

        return score

    def _productivity(self, recipe_name: str) -> float:
        skill = self.state.skills.get(recipe_name, 1.0)
        affinity = self.skill_affinities.get(recipe_name, 1.0)
        cap = self.config.skill_productivity_cap
        base = self.config.skill_productivity_base
        return min(cap, base * skill * affinity)

    def choose_production(self) -> Recipe | None:
        feasible = [r for r in RECIPES if r.can_afford(self.state.inventory)]
        if not feasible:
            return None

        scored = [(self._score_recipe(r), r) for r in feasible]
        scored.sort(key=lambda x: x[0], reverse=True)

        top_n = min(self.config.production_top_n, len(scored))
        top = scored[:top_n]
        weights = [max(s, 0.01) for s, _ in top]
        _, chosen = self.rng.choices(top, weights=weights, k=1)[0]
        return chosen

    def execute_production(self, recipe: Recipe) -> dict[str, int]:
        for good, qty in recipe.inputs.items():
            self.state.remove_good(good, qty)

        productivity = self._productivity(recipe.name)
        outputs: dict[str, int] = {}
        for good, base_qty in recipe.outputs.items():
            qty = max(1, int(base_qty * productivity)) if base_qty > 0 else 0
            self.state.add_good(good, qty)
            outputs[good.value] = qty

        self.state.last_recipe = recipe.name
        self.state.recipe_counts[recipe.name] = (
            self.state.recipe_counts.get(recipe.name, 0) + 1
        )
        self._apply_learning(recipe.name)
        return outputs

    def _apply_learning(self, recipe_name: str) -> None:
        current = self.state.skills.get(recipe_name, 1.0)
        gain = self.config.skill_gain_per_use
        cap = self.config.skill_productivity_cap
        self.state.skills[recipe_name] = min(cap, current + gain)

    def reservation_bid_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = (market_prices or self.config.base_prices()).get(
            good, self.config.base_prices()[good]
        )
        shortage = self._shortage(good)
        target = self.config.targets()[good]
        if target == 0:
            return base
        urgency = min(1.0, shortage / target)
        return base * (1.0 + self.config.shortage_premium * urgency)

    def reservation_ask_price(
        self, good: Good, market_prices: dict[Good, float] | None = None
    ) -> float:
        base = (market_prices or self.config.base_prices()).get(
            good, self.config.base_prices()[good]
        )
        surplus = self._surplus(good)
        target = self.config.targets()[good]
        if target == 0:
            return base
        excess = min(1.0, surplus / max(1, target))
        return max(0.5, base * (1.0 - self.config.surplus_discount * excess))

    def generate_orders(
        self,
        order_id_start: int,
        market_prices: dict[Good, float] | None = None,
    ) -> tuple[list[Order], int]:
        orders: list[Order] = []
        next_id = order_id_start

        for good in Good:
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

    def consume_food(self) -> int:
        """Eat food for survival. Returns amount consumed."""
        needed = self.config.food_consumption_per_tick
        available = self.state.inventory_of(Good.FOOD)
        consumed = min(needed, available)
        if consumed > 0:
            self.state.remove_good(Good.FOOD, consumed)
        return consumed

    def snapshot(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "money": round(self.state.money, 2),
            "inventory": {g.value: self.state.inventory_of(g) for g in Good},
            "skills": {k: round(v, 3) for k, v in self.state.skills.items()},
            "last_recipe": self.state.last_recipe,
            "memory_size": len(self.state.memory),
        }


def create_agents(config: SimConfig, rng: random.Random) -> list[Agent]:
    agents: list[Agent] = []
    for i in range(config.num_agents):
        affinities = {
            r.name: rng.uniform(config.skill_affinity_min, config.skill_affinity_max)
            for r in RECIPES
        }
        agents.append(Agent(i, config, rng, affinities))
    return agents
