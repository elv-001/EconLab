from __future__ import annotations

import random
import math
from collections import deque, Counter
from typing import TYPE_CHECKING

from econ_sim.config import (
    GOAL_CHAIN_RECIPES, RECIPES, SimConfig, RECIPE_BY_NAME,
    TRADEABLE_GOODS, ARCHETYPE_MIX
)
from econ_sim.sim_types import (
    AgentState, Good, Order, Recipe, SkillDomain,
    AgentArchetype, ARCHETYPES, InventoryLot,
    Action, ActionType
)

class Agent:
    def __init__(
        self,
        agent_id: int,
        config: SimConfig,
        rng: random.Random,
        skill_affinities: dict[SkillDomain, float],
        archetype: AgentArchetype | None,
    ) -> None:
        self.state = AgentState(
            agent_id=agent_id,
            money=config.initial_money,
            self_reliance=0.5,
        )
        self.config = config
        self.rng = rng
        self.archetype = archetype or ARCHETYPES["generalist"]
        self.skill_affinities = skill_affinities
        self.state.self_reliance = rng.uniform(*self.archetype.self_reliance_range)

        # Starting money jitter
        self.state.money += rng.uniform(-config.money_jitter, config.money_jitter)
        self.state.money = max(1.0, self.state.money)

        self.state.lots = []
        self.state.has_shelter = False
        self.state.comfort_debt = 0.0
        self.state.alive = True
        self.state.recent_recipes = deque(maxlen=100)
        self.state.current_production_action = Action(ActionType.HOLD)
        self.state.current_trade_actions = []

        self._congestion: dict[str, float] = {}

        self.expected_sell_rate: dict[Good, float] = {
            g: 1.0 for g in TRADEABLE_GOODS
        }

        # Initial endowments
        self.add_good(Good.FOOD, max(
            config.food_consumption_per_tick + 1,
            config.initial_food + rng.randint(-config.endowment_jitter, config.endowment_jitter)
        ), tick=0)
        self.add_good(Good.WOOD, max(0, config.initial_wood + rng.randint(-config.endowment_jitter, config.endowment_jitter)), tick=0)
        self.add_good(Good.FIBER, max(0, config.initial_fiber + rng.randint(-config.endowment_jitter, config.endowment_jitter)), tick=0)
        self.add_good(Good.TOOLS, max(0, config.initial_tools + rng.randint(0, config.endowment_jitter // 2)), tick=0)

        for skill in SkillDomain:
            self.state.skills[skill] = 1.0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def agent_id(self) -> int:
        return self.state.agent_id

    # ------------------------------------------------------------------
    # Main planning entry point
    # ------------------------------------------------------------------
    def plan(self, tick: int, market_prices: dict[Good, float], congestion=None) -> None:
        self._congestion = congestion or {}
        shadow = self.compute_shadow_values(market_prices)

        # 1. Generate all feasible actions
        actions = self._generate_actions(market_prices, tick)

        # 2. Score them
        scored = [(a, self._value_action(a, tick, market_prices, shadow)) for a in actions]

        skill = self.state.skills[SkillDomain.WEAVING]
        if skill >= 1.2:
            value = self._value_produce(
                RECIPE_BY_NAME["weave_cloth"],
                tick,
                market_prices,
                self.compute_shadow_values(market_prices),
            )

        if self.state.skills.get(SkillDomain.WEAVING, 0) >= 1.2:
            vals = []

            for r in RECIPES:
                if self._can_attempt(r, market_prices):
                    v = self._value_produce(
                        r, tick, market_prices, self.compute_shadow_values(market_prices)
                    )
                    vals.append((r.name, v))

            if vals:
                vals.sort(key=lambda x: x[1], reverse=True)

                weave_value = next(
                    (v for n, v in vals if n == "weave_cloth"),
                    None
                )

                weave_rank = next(
                    (
                        i + 1
                        for i, (name, _) in enumerate(vals)
                        if name == "weave_cloth"
                    ),
                    None
                )
                """
                print(
                    self.agent_id,
                    "money=", round(self.state.money, 2),
                    "fiber=", round(self.state.inventory_of(Good.FIBER), 2),
                    "tools=", self.tools_count,
                    "weave_rank=", weave_rank,
                    "weave_value=", weave_value,
                    "top=", vals[:4],
                )
                """

        # 3. Select best production action (with opportunity cost already inside the score)
        prod_actions = [(a, v) for a, v in scored if a.action_type == ActionType.PRODUCE]
        if prod_actions:
            prod_actions.sort(key=lambda x: x[1], reverse=True)
            if self._survival_penalty(tick) > 0 or self.config.production_top_n <= 1:
                best_prod, best_prod_val = prod_actions[0]
            else:
                top_n = prod_actions[:max(1, self.config.production_top_n)]
                # weight by score so better options are still favored, not just tied
                weights = [max(0.01, v - min(v2 for _, v2 in top_n) + 0.01) for _, v in top_n]
                best_prod, best_prod_val = self.rng.choices(top_n, weights=weights, k=1)[0]

            if best_prod_val > getattr(self.config, "produce_value_threshold",-1):
                self.state.current_production_action = best_prod
            else:
                self.state.current_production_action = Action(ActionType.HOLD)
        else:
            self.state.current_production_action = Action(ActionType.HOLD)

        # 4. Select trade actions (BUY / SELL) that clear a modest threshold
        #    and do not conflict with each other
        trade_actions = [
            (a, v) for a, v in scored
            if a.action_type in (ActionType.BUY, ActionType.SELL) and v > getattr(self.config, "trade_value_threshold", 0.1)
        ]
        trade_actions.sort(key=lambda x: x[1], reverse=True)

        chosen_trades: list[Action] = []
        used_goods: set[Good] = set()
        for action, _ in trade_actions:
            if action.good in used_goods or not action.good:
                continue
            chosen_trades.append(action)
            used_goods.add(action.good)
            #if len(chosen_trades) >= getattr(self.config, "max_trades_per_tick", 4):
            #    break

        # 5. If the chosen production needs missing inputs, force the corresponding BUYs
        #    to the front so the agent actually tries to acquire them this tick
        prod = self.state.current_production_action

        if prod and prod.action_type == ActionType.PRODUCE and prod.recipe:
            for good, qty in prod.recipe.inputs.items():
                if good not in TRADEABLE_GOODS:
                    continue
                missing = self._missing(good, qty)
                if missing > 0 and good not in used_goods:
                    buy = Action(ActionType.BUY, good=good, quantity=missing)
                    chosen_trades.insert(0, buy)
                    used_goods.add(good)
        self.state.current_trade_actions = chosen_trades

    # ------------------------------------------------------------------
    # Action generation
    # ------------------------------------------------------------------
    def _generate_actions(self, market_prices: dict[Good, float], tick: int) -> list[Action]:
        actions = [Action(ActionType.HOLD)]

        # Production – include any recipe that is affordable OR can be made affordable by buying
        for recipe in RECIPES:
            if self._can_attempt(recipe, market_prices):
                # Do not produce shelter again if it has already been made
                if Good.SHELTER in recipe.outputs and self.config.shelter_required and self.state.has_shelter:
                    continue
                actions.append(Action(ActionType.PRODUCE, recipe=recipe))

        # Buys
        for good in TRADEABLE_GOODS:
            if self.state.money > 0:
                qty = self._desired_buy_quantity(good, market_prices)
                if qty > 0:
                    actions.append(Action(ActionType.BUY, good=good, quantity=qty))

        # Sells
        for good in TRADEABLE_GOODS:
            qty = self._desired_sell_quantity(good)
            if qty > 0:
                actions.append(Action(ActionType.SELL, good=good, quantity=qty))
        return actions

    def _can_attempt(self, recipe: Recipe, market_prices: dict[Good, float]) -> bool:
        """True if the agent already has the inputs OR can afford to buy the missing tradeables."""
        missing_cost = 0.0
        for good, qty in recipe.inputs.items():
            miss = self._missing(good, qty)
            if miss <= 0:
                continue
            if good not in TRADEABLE_GOODS:
                return False
            missing_cost += self._buy_price(good, market_prices) * miss
        return missing_cost <= self.state.money + 1e-6

    # ------------------------------------------------------------------
    # Value function (unified)
    # ------------------------------------------------------------------
    def _value_action(
        self,
        action: Action,
        tick: int,
        market_prices: dict[Good, float],
        shadow: dict[Good, float],
    ) -> float:
        if action.action_type == ActionType.HOLD:
            return 0.0

        if action.action_type == ActionType.PRODUCE and action.recipe:
            return self._value_produce(action.recipe, tick, market_prices, shadow)

        if action.action_type == ActionType.BUY and action.good:
            return self._value_buy(action.good, action.quantity, tick, market_prices, shadow)

        if action.action_type == ActionType.SELL and action.good:
            return self._value_sell(action.good, action.quantity, tick, market_prices, shadow)

        return -math.inf

    def _value_produce(
        self,
        recipe: Recipe,
        tick: int,
        market_prices: dict[Good, float],
        shadow: dict[Good, float],
    ) -> float:
        prod = self._productivity(recipe)

        # Output value
        out_val = 0.0
        for good, base_qty in recipe.outputs.items():
            expected = max(0, int(round(base_qty * prod)))
            if expected <= 0:
                continue
            out_val += self._marginal_value(good, expected, tick, market_prices, shadow)

            if good != Good.SHELTER:
                projected = self.state.inventory_of(good) + expected
                excess = max(0, projected - self.config.reasonable_stock(good))
                if excess > 0:
                    # don't penalize stock that's already going to market this tick
                    """
                    pending_sale = next(
                        (a.quantity for a in self.state.current_trade_actions
                        if a.action_type == ActionType.SELL and a.good == good),
                        0
                    )
                    
                    taxable_excess = max(0, excess - pending_sale)
                    if taxable_excess > 0:
                        price = self._expected_price(good, market_prices)
                        out_val -= (self.config.carrying_cost_rate * taxable_excess * price
                                    * self.config.carrying_cost_horizon)
                                    """

        # Input cost (owned + to-be-bought)
        in_cost = 0.0
        for good, qty in recipe.inputs.items():
            have = self.state.inventory_of(good)
            owned = min(have, qty)
            missing = qty - owned
            in_cost += self._sell_price(good, market_prices) * owned    # opportunity cost of using owned
            if missing > 0:
                in_cost += self._buy_price(good, market_prices) * missing   # cash cost of buying

        net = out_val - in_cost

        # Trait modifiers
        net *= (0.6 + 0.8 * self.archetype.profit_motivation)

        # Mild self-reliance penalty if the recipe required buying
        if any(self._missing(g, q) > 0 for g, q in recipe.inputs.items()):
            net *= (1.0 - 0.15 * self.state.self_reliance)

        # Penalize for switching
        primary = self.primary_activity()
        if primary and recipe.domain != RECIPE_BY_NAME[primary].domain:
            net *= 1

        if Good.FOOD not in recipe.outputs:
            net -= self._survival_penalty(tick)

        for good in recipe.outputs:
            rate = self.expected_sell_rate.get(good, 1.0)
            if rate < 0.7:
                net *= (0.6 + 0.4 * rate)   # scales down when the agent cannot sell

        skill = self.state.skills[recipe.domain]
        if skill > 1.3:
            net *= 1.0 + 0.35 * (skill - 1.0)

        # in _value_produce, after computing net:
        recent = list(self.state.recent_recipes)[-5:]
        if recent:
            current_domain_streak = Counter(RECIPE_BY_NAME[r].domain for r in recent)
            if recipe.domain in current_domain_streak:
                # small continuity bonus for the domain you've actually been doing —
                # represents real switching friction (tooling up, momentum, local knowledge)
                # without telling the agent what to specialize in
                net *= 1.0 + 0.1 * current_domain_streak[recipe.domain]

        return net

    def _value_buy(
        self,
        good: Good,
        qty: int,
        tick: int,
        market_prices: dict[Good, float],
        shadow: dict[Good, float],
    ) -> float:
        if qty <= 0:
            return -math.inf
        price = self._buy_price(good, market_prices)
        cost = price * qty
        if cost > self.state.money:
            return -math.inf
        
        direct_need = self._need_value(good, qty, tick)
        benefit = self._marginal_value(good, qty, tick, market_prices, shadow)
        # Self-reliance makes pure market acquisition less attractive
        benefit *= (1.0 - 0.2 * self.state.self_reliance)
        return benefit - cost

    def _value_sell(
        self,
        good: Good,
        qty: int,
        tick: int,
        market_prices: dict[Good, float],
        shadow: dict[Good, float],
    ) -> float:
        if qty <= 0:
            return -math.inf

        price = self._sell_price(good, market_prices)
        expected_proceeds = price * qty * self.expected_sell_rate.get(good, 1.0)

        # Opportunity cost of selling = what we give up by not holding the good.
        # We use a *conservative* hold value: only the direct need (if any)
        # plus a discounted shadow/resale term. This prevents the agent from
        # refusing to sell simply because the good *could* be useful later.
        direct_need = self._need_value(good, qty, tick)

        currently_needed = 0
        prod = self.state.current_production_action
        if prod and (prod.action_type == ActionType.PRODUCE and prod.recipe
                and good in prod.recipe.inputs):
            currently_needed = prod.recipe.inputs[good]

        idle = max(0, self.state.inventory_of(good) - currently_needed)
        idle_factor = 1.0 / (1.0 + 2 * idle)

        # 2. Conservative hold value for sale decisions.
        #    We largely ignore the optimistic multi-hop shadow.
        #    Instead we use a modest fraction of the current market price
        #    as the “I might want this later” term.
        #    the more extra we have, the less we value keeping another unit.
        market = self._expected_price(good, market_prices)
        speculative = 0.20 * qty * market * idle_factor

        hold_value = direct_need + speculative

        return expected_proceeds - hold_value

    def _survival_penalty(self, tick: int) -> float:
        """Expected utility loss from starvation risk if I do a non-food action this tick."""
        current = self.state.inventory_of(Good.FOOD)
        consumption = self.config.food_consumption_per_tick
        # How many ticks of food I have left
        ticks_left = current / max(1, consumption)
        if ticks_left > 3:
            return 0.0
        # Steeply rising penalty as death approaches
        return 100.0 / max(1.0, ticks_left)**2

    def inventory_holding_cost(
        self,
        market_prices: dict[Good, float],
    ) -> float:
        """
        Cost of storing excess inventory.
        Applies every tick, not only when producing.
        """

        cost = 0.0

        for good in Good:
            excess = self._excess_inventory(good)

            if excess <= 0:
                continue

            price = self._expected_price(good, market_prices)

            cost += (
                excess
                * price
                * self.config.carrying_cost_rate
            )

        return cost

    # ------------------------------------------------------------------
    # Marginal value of goods (need + resale + shadow)
    # ------------------------------------------------------------------
    def _marginal_value(self, good, qty, tick, market_prices, shadow):
        if qty <= 0:
            return 0.0
        need = self._need_value(good, qty, tick)
        resale = qty * self._expected_price(good, market_prices) * self.expected_sell_rate.get(good, 1.0)

        base = self.config.base_prices().get(good, 1.0)
        price_ratio = min(1.0, self._expected_price(good, market_prices) / base)
        shad = qty * shadow.get(good, self._expected_price(good, market_prices))

        if price_ratio < 0.6:
            discount = 0.55 + 0.45 * (price_ratio / 0.6)
            resale *= discount
            shad  *= discount
        return need + max(resale, shad)

        # Personal glut: if I'm already holding far more of this than I can
        # realistically sell or use, each additional unit is worth less to me —
        # regardless of what the population-average market price says.

        """
        reasonable = self.config.reasonable_stock(good)

        if reasonable > 0:
            owned = self.state.inventory_of(good)
            glut = owned / reasonable

            surplus = max(0.0, glut - 1.0)

            personal_discount = 1.0 / (
                1.0 + 0.1 * surplus ** 2
            )

            resale *= personal_discount
            shad *= personal_discount
        """
            
        return need + max(resale, shad)

    def _need_value(self, good: Good, qty: int, tick: int) -> float:
        if good == Good.FOOD:
            current = self.state.inventory_of(Good.FOOD)
            # Steeply rising marginal utility as we approach starvation
            critical = self.config.food_consumption_per_tick
            target = self.config.target_food
            if current < critical:
                return qty * 25.0          # extremely high
            if current < target:
                deficit = target - current
                return qty * (7.0 + 2.0 * deficit)
            return qty * 0.5               # small comfort value

        if good == Good.CLOTHES and self.config.clothing_enabled:
            current = self.state.inventory_of(Good.CLOTHES)
            wear_rate = self.config.comfort_consumption_per_tick
            ticks_left = current / max(0.01, wear_rate)

            if ticks_left < 3:
                return qty * 15.0
            if current < self.config.target_clothes:
                return qty * (4.0 + 1.5 * (self.config.target_clothes - current))
            return qty * 0.4

        if good == Good.SHELTER and not self.state.has_shelter and self.config.shelter_required:
            return qty * 30.0

        return 0.0

    # ------------------------------------------------------------------
    # Prices & quantities
    # ------------------------------------------------------------------
    def _expected_price(self, good: Good, market_prices: dict[Good, float] | None) -> float:
        if market_prices and good in market_prices:
            return market_prices[good]
        return self.config.base_prices().get(good, 1.0)

    def _buy_price(self, good, market_prices):
        base = self._expected_price(good, market_prices)
        urgency = 0.0
        if good != Good.FOOD:  # food's urgency is already reflected in _need_value + market backlog pricing
            prod = self.state.current_production_action
            if (prod and prod.action_type == ActionType.PRODUCE and prod.recipe
                    and good in prod.recipe.inputs and self._missing(good, prod.recipe.inputs[good]) > 0):
                urgency = 0.6
        return max(0.05, base * (1.0 + urgency))

    def _sell_price(self, good, market_prices):
        base = self._expected_price(good, market_prices)
        surplus = self._surplus(good)
        reasonable = max(1, self.config.reasonable_stock(good))
        glut_ratio = surplus / reasonable
        discount = min(0.9, 0.04 * surplus + 0.05 * max(0, glut_ratio - 1))
        return max(0.1 * base, base * (1.0 - discount))

    def _input_cost(self, good: Good, qty: int, market_prices: dict[Good, float]) -> float:
        if qty <= 0:
            return 0.0
        price = self._expected_price(good, market_prices)
        if good == Good.TOOLS:
            price /= max(1, self.config.tool_max_uses)
        return price * qty

    def _desired_buy_quantity(self, good: Good, market_prices: dict[Good, float]) -> int:
        if good == Good.FOOD:
            target = self.config.target_food + self.config.food_consumption_per_tick
            return max(1, target - self.state.inventory_of(Good.FOOD))
        if good == Good.CLOTHES:
            return max(1, self.config.target_clothes - self.state.inventory_of(Good.CLOTHES))
        # Capital/intermediate goods: buy more when cheap relative to base
        base = self.config.base_prices().get(good, 1.0)
        price = self._expected_price(good, market_prices)
        # simplest version: just scale desired qty inversely with price ratio
        target = self.config.reasonable_stock(good)
        have = self.state.inventory_of(good)
        return max(1, target - have) if have < target else 1

    def _desired_sell_quantity(self, good: Good) -> int:
        return max(0, self._surplus(good))

    def _surplus(self, good: Good) -> int:
        inv = self.state.inventory_of(good)
        if good == Good.FOOD:
            reserve = self.config.target_food + self.config.food_consumption_per_tick
            return max(0, inv - reserve)
        if good == Good.CLOTHES:
            return max(0, inv - self.config.target_clothes)
        # Tools & intermediates: keep almost nothing in reserve once we have a couple

        reserve = 0
        prod = self.state.current_production_action
        if (prod and prod.action_type == ActionType.PRODUCE and prod.recipe
                and good in prod.recipe.inputs):
            reserve = prod.recipe.inputs[good]
        return max(0, inv - reserve)

    def _missing(self, good: Good, qty: int) -> int:
        return max(0, qty - self.state.inventory_of(good))

    # ------------------------------------------------------------------
    # Shadow values (multi-hop)
    # ------------------------------------------------------------------
    def compute_shadow_values(
        self,
        market_prices: dict[Good, float] | None = None,
        iterations: int = 3,
        discount: float = 0.80,
    ) -> dict[Good, float]:
        prices = market_prices or self.config.base_prices()
        values = {g: prices.get(g, 1.0) for g in Good}

        for _ in range(iterations):
            updated = dict(values)
            for recipe in RECIPES:
                prod = self._productivity(recipe)
                if prod <= 0:
                    continue
                out_val = sum(
                    values.get(g, 1.0) * max(1, int(q * prod))
                    for g, q in recipe.outputs.items()
                )
                for good, qty in recipe.inputs.items():
                    if qty <= 0:
                        continue
                    other = sum(
                        values.get(g2, 1.0) * q2
                        for g2, q2 in recipe.inputs.items() if g2 != good
                    )
                    marginal = discount * (out_val - other) / qty
                    if marginal > updated.get(good, 0.0):
                        updated[good] = marginal
            values = updated

        for good in values:
            inv = self.state.inventory_of(good)
            # Simple scarcity factor: more scarce → higher multiplier
            # Cap so it doesn't explode
            scarcity = 1.0 / (1.0 + inv)**1.2          # 1.0 when inv=0, falls toward 0
            downstream = values[good]
            values[good] = downstream * (1.0 + 1.8 * scarcity)

        return values

    # STORAGE CARRYING COST
    def _excess_inventory(self, good: Good) -> int:
        owned = self.state.inventory_of(good)
        return max(0, owned - self.config.reasonable_stock(good))

    # ------------------------------------------------------------------
    # Order generation (called by Simulation)
    # ------------------------------------------------------------------
    def generate_orders(
        self,
        order_id_start: int,
        market_prices: dict[Good, float] | None = None,
    ) -> tuple[list[Order], int]:
        orders: list[Order] = []
        next_id = order_id_start
        prices = market_prices or self.config.base_prices()

        for action in self.state.current_trade_actions:
            if action.action_type == ActionType.BUY and action.good:
                price = self._buy_price(action.good, prices)
                # Allow bidding even if cash is tight – matching will clamp
                max_qty = max(1, int(self.state.money / max(0.01, price)))
                qty = min(action.quantity, max_qty)

                if qty >= 1:
                    orders.append(Order(
                        agent_id=self.agent_id,
                        good=action.good,
                        quantity=qty,
                        price=price,
                        is_bid=True,
                        order_id=next_id,
                    ))
                    next_id += 1

            elif action.action_type == ActionType.SELL and action.good:
                price = self._sell_price(action.good, prices)
                qty = min(action.quantity, self.state.inventory_of(action.good))
                if qty >= 1:
                    orders.append(Order(
                        agent_id=self.agent_id,
                        good=action.good,
                        quantity=qty,
                        price=price,
                        is_bid=False,
                        order_id=next_id,
                    ))
                    next_id += 1

        return orders, next_id

    # ------------------------------------------------------------------
    # Execution (called after matching)
    # ------------------------------------------------------------------
    def execute_action(self, tick: int, market_prices: dict[Good, float]) -> dict[str, int] | None:
        action = self.state.current_production_action
        if not action or action.action_type != ActionType.PRODUCE or action.recipe is None:
            return None

        recipe = action.recipe
        if not self.can_afford_recipe(recipe):
            shadow = self.compute_shadow_values(market_prices)
            candidates = [
                r for r in RECIPES
                if self.can_afford_recipe(r)
            ]
            if candidates:
                recipe = max(candidates, key=lambda r: self._value_produce(r, tick, market_prices, shadow))
            else:
                return None

        return self.execute_production(recipe, tick)

    def can_afford_recipe(self, recipe: Recipe) -> bool:
        for good, qty in recipe.inputs.items():
            if good == Good.TOOLS:
                if self._get_tool_uses_left() < qty:
                    return False
            else:
                if self.state.inventory_of(good) < qty:
                    return False
        return True

    def execute_production(self, recipe: Recipe, tick: int) -> dict[str, int]:
        for good, qty in recipe.inputs.items():
            self.use_good(good, qty)

        productivity = self._productivity(recipe)
        outputs: dict[str, int] = {}

        for good, base_qty in recipe.outputs.items():
            if good == Good.SHELTER:
                self.state.has_shelter = True
                outputs[good.value] = 1
                continue
            expected = base_qty * productivity
            whole = int(expected)
            frac = expected - whole
            qty = whole + (1 if self.rng.random() < frac else 0)
            if qty > 0:
                self.add_good(good, qty, tick)
            outputs[good.value] = qty

        self.state.last_recipe = recipe.name
        self.state.recipe_counts[recipe.name] = self.state.recipe_counts.get(recipe.name, 0) + 1
        self._apply_learning(recipe)
        self.state.recent_recipes.append(recipe.name)
        return outputs

    # ------------------------------------------------------------------
    # Inventory & tools (unchanged core logic)
    # ------------------------------------------------------------------
    def add_good(self, good: Good, qty: int, tick: int = -1) -> None:
        if qty <= 0:
            return
        if tick < 0:
            raise ValueError("tick required")
        shelf = getattr(self.config, "GOODS_SHELF_LIFE", {}).get(good)

        if good == Good.TOOLS:
            for _ in range(qty):
                self.state.lots.append(InventoryLot(
                    good=good, quantity=1, created_tick=tick,
                    shelf_life=shelf, uses_remaining=self.config.tool_max_uses
                ))
        else:
            self.state.lots.append(InventoryLot(
                good=good, quantity=qty, created_tick=tick,
                shelf_life=shelf, uses_remaining=None
            ))
        self._sync_inventory()

    def use_good(self, good: Good, qty: int) -> None:
        if qty <= 0:
            return
        if good == Good.TOOLS:
            for _ in range(qty):
                if not self._use_tool():
                    raise ValueError(f"Agent {self.agent_id} insufficient tools")
            return
        if self.state.inventory_of(good) < qty:
            raise ValueError(f"Agent {self.agent_id} insufficient {good.value}")
        remaining = qty
        new_lots = []
        for lot in sorted(self.state.lots, key=lambda x: x.created_tick):
            if lot.good != good or remaining <= 0:
                new_lots.append(lot)
                continue
            take = min(lot.quantity, remaining)
            lot.quantity -= take
            remaining -= take
            if lot.quantity > 0:
                new_lots.append(lot)
        self.state.lots = new_lots
        self._sync_inventory()

    def remove_good(self, good: Good, qty: int) -> None:
        if good == Good.TOOLS:
            # Remove whole tools
            remaining = qty
            new_lots = []
            for lot in sorted(self.state.lots, key=lambda x: x.created_tick):
                if lot.good != Good.TOOLS or remaining <= 0:
                    new_lots.append(lot)
                    continue
                take = min(lot.quantity, remaining)
                lot.quantity -= take
                remaining -= take
                if lot.quantity > 0:
                    new_lots.append(lot)
            self.state.lots = new_lots
            self._sync_inventory()
        else:
            self.use_good(good, qty)

    def _use_tool(self) -> bool:
        for i, lot in enumerate(self.state.lots):
            if lot.good == Good.TOOLS and lot.uses_remaining and (lot.uses_remaining or 0) > 0:
                lot.uses_remaining -= 1
                if lot.uses_remaining <= 0:
                    self.state.lots.pop(i)
                self._sync_inventory()
                return True
        return False

    def _sync_inventory(self) -> None:
        counts = {g: 0 for g in Good}
        for lot in self.state.lots:
            counts[lot.good] += lot.quantity
        self.state.inventory = counts

    def decay_inventory(self, current_tick: int) -> dict[Good, int]:
        spoiled: dict[Good, int] = Counter()
        surviving = []
        food_decay = getattr(self.config, "food_shelf_life_enabled", True)
        for lot in self.state.lots:
            if lot.good == Good.TOOLS:
                self._depreciate_tools(lot, current_tick)
                if lot.uses_remaining and lot.uses_remaining > 0:
                    surviving.append(lot)
                continue
            if lot.shelf_life is None:
                surviving.append(lot)
                continue
            if lot.good == Good.FOOD and not food_decay:
                surviving.append(lot)
                continue
            if current_tick - lot.created_tick >= lot.shelf_life:
                spoiled[lot.good] += lot.quantity
            else:
                surviving.append(lot)
        self.state.lots = surviving
        self._sync_inventory()
        return dict(spoiled)

    def _depreciate_tools(self, lot: InventoryLot, current_tick: int):
        if lot.good != Good.TOOLS or lot.uses_remaining is None:
            return

        # how old is this tool
        age = max(0, current_tick - lot.created_tick)

        wear_rate = min(
            1.0,
            age / self.config.tool_max_lifespan,
        )
        expected_uses_remaining = self.config.tool_max_uses * (1 - wear_rate)

        # if tool has been sitting for a long time without usage, wear it down
        if lot.uses_remaining > expected_uses_remaining:
            num_uses = math.floor(
                lot.uses_remaining - expected_uses_remaining
            )

            if num_uses > 0:
                lot.uses_remaining -= num_uses

    def _get_tool_uses_left(self) -> int:
        lots = [lot for lot in self.state.lots if lot.good == Good.TOOLS]
        return sum(lot.uses_remaining for lot in lots if lot.uses_remaining)

    # ------------------------------------------------------------------
    # Learning & productivity
    # ------------------------------------------------------------------
    def _productivity(self, recipe: Recipe) -> float:
        if recipe.domain == SkillDomain.GATHERING:
            share = self._congestion.get(recipe.name, 0.0)
            sustainable = self.config.gathering_sustainable_share
            excess = max(0.0, share - sustainable)
            raw = 1.0 / (1.0 + self.config.gathering_congestion_k * excess)
            return max(self.config.gathering_productivity_floor, raw)
        
        skill = self.state.skills.get(recipe.domain, 1.0)
        affinity = self.skill_affinities.get(recipe.domain, 1.0)
        skill_f = 1.0 + self.config.skill_effect * (skill - 1.0)
        aff_f = 1.0 + self.config.affinity_effect * (affinity - 1.0)
        raw = skill_f * aff_f
        if skill < recipe.min_skill:
            raw *= (skill / recipe.min_skill) ** self.archetype.novice_penalty_exponent
        loss = 0.0
        if self.config.shelter_required and not self.state.has_shelter and recipe.name != "shelter":
            loss += self.config.shelter_productivity_loss
        if self.config.clothing_enabled and self._shortage(Good.CLOTHES) > 0:
            loss += self.config.comfort_productivity_loss
        return min(self.config.skill_productivity_cap, max(0.1, raw * (1.0 - loss)))

    def _apply_learning(self, recipe: Recipe) -> None:
        if recipe.domain == SkillDomain.GATHERING:
            return
        current = self.state.skills.get(recipe.domain, 1.0)
        talent = self.skill_affinities.get(recipe.domain, 1.0)
        cap = 1.0 + (self.config.skill_productivity_cap - 1.0) * (talent ** 1.5)
        progress = max(0.0, (current - 1.0) / (cap - 1.0))
        gain = self.config.skill_gain_per_use * (1.0 - progress) ** 2 * (talent ** 1.5)
        self.state.skills[recipe.domain] = min(cap, current + gain)

        # Mild decay of unused skills
        for domain, skill in list(self.state.skills.items()):
            if domain == recipe.domain or domain == SkillDomain.GATHERING:
                continue
            floor = 1
            last = self._last_used(domain)
            if last > self.config.skill_decay_grace_period:
                self.state.skills[domain] = max(floor, skill - self.config.skill_decay_per_tick)

    def _last_used(self, domain: SkillDomain) -> int:
        for i, name in enumerate(reversed(self.state.recent_recipes)):
            if RECIPE_BY_NAME[name].domain == domain:
                return i + 1
        return len(self.state.recent_recipes) + 1

    def _shortage(self, good: Good) -> int:
        if good == Good.FOOD:
            return max(0, self.config.target_food - self.state.inventory_of(Good.FOOD))
        if good == Good.CLOTHES:
            return max(0, self.config.target_clothes - self.state.inventory_of(Good.CLOTHES))
        if good == Good.SHELTER:
            return 0 if self.state.has_shelter else 1
        return 0

    # ------------------------------------------------------------------
    # Book-keeping helpers used by Simulation
    # ------------------------------------------------------------------
    def record_sale(self, good: Good, offered: int, sold: int, price: float) -> None:
        if offered <= 0:
            return
        realized = max(0.0, min(1.0, sold / offered))
        old = self.expected_sell_rate.get(good, 1.0)
        alpha = self.config.price_ema_alpha
        self.expected_sell_rate[good] = max(0.4, (1 - alpha) * old + alpha * realized)

    def decay_sell_rate_belief(self) -> None:
        """Beliefs about market conditions should get less confident over time
        if untested, so a stale 'nobody's buying this' assumption doesn't persist
        forever once an agent stops trying to sell."""
        for good in TRADEABLE_GOODS:
            old = self.expected_sell_rate.get(good, 1.0)
            self.expected_sell_rate[good] = old + 0.01 * (1.0 - old)

    def consume_food(self, tick: int) -> int:
        needed = self.config.food_consumption_per_tick
        if self.state.inventory_of(Good.FOOD) < needed:
            self.state.alive = False
            #print(f"DEATH agent={self.agent_id} tick={tick} money={self.state.money:.2f} recipe={self.state.current_production_action.recipe.name if ... else 'none'}") # type: ignore
            return 0
        self.use_good(Good.FOOD, needed)
        return needed

    def consume_comfort(self) -> None:
        if not self.config.clothing_enabled:
            return
        available = self.state.inventory_of(Good.CLOTHES)
        if available <= 0:
            return
        self.state.comfort_debt += self.config.comfort_consumption_per_tick
        to_remove = int(self.state.comfort_debt)
        if to_remove > 0:
            actual = min(to_remove, available)
            self.remove_good(Good.CLOTHES, actual)
            self.state.comfort_debt -= actual

    # changing how primary activity is measured/??
    def primary_activity(self, window: int = 25) -> str | None:
        recent = list(self.state.recent_recipes)[-window:]
        if not recent:
            return self.state.last_recipe
        return Counter(recent).most_common(1)[0][0]

    def snapshot(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "money": round(self.state.money, 2),
            "inventory": {g.value: self.state.inventory_of(g) for g in Good},
            "skills": {d.value: round(s, 3) for d, s in self.state.skills.items()},
            "last_recipe": self.state.last_recipe,
            "primary_activity": self.primary_activity(),
            "self_reliance": round(self.state.self_reliance, 3),
        }

    def total_wealth(self, prices: dict[Good, float]) -> float:
        return self.state.money + sum(
            self.state.inventory_of(g) * prices.get(g, 0.0) for g in Good
        )


def create_agents(config: SimConfig, rng: random.Random) -> list[Agent]:
    mix = ARCHETYPE_MIX or {"generalist": 1.0}
    names = list(mix.keys())
    weights = list(mix.values())
    agents = []
    for i in range(config.num_agents):
        arch = ARCHETYPES[rng.choices(names, weights=weights, k=1)[0]]
        affinities = {
            d: rng.uniform(config.skill_affinity_min, config.skill_affinity_max)
               * arch.affinity_bias.get(d, 1.0)
            for d in SkillDomain
        }
        agents.append(Agent(i, config, rng, affinities, arch))
    return agents
