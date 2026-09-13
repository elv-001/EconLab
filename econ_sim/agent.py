from __future__ import annotations

import random, math
from typing import TYPE_CHECKING
from collections import deque, Counter

from econ_sim.config import (
    GOAL_CHAIN_RECIPES, RECIPES, SimConfig, RECIPE_BY_NAME, ENABLED_GOODS,
    TRADEABLE_GOODS, ARCHETYPE_MIX, CAPITAL_GOODS
)

from econ_sim.sim_types import (
    AgentState, CounterpartyMemory, Good, Order, Recipe, SkillDomain, 
    AgentArchetype, ARCHETYPES, Goal, InventoryLot
)

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

        # All physical inventory is stored as timestamped lots.
        # InventoryLot is the single source of truth; self.state.inventory
        # is kept as a synchronized quantity cache for the rest of the sim.
        self.state.lots = []

        # States
        self.state.has_shelter = False
        self.state.comfort_debt = 0

        self.state.current_goal = None
        self.state.alive = True

        self.state.recent_recipes = deque(maxlen=100)

        self.expected_sell_rate: dict[Good, float] = {
            good: 1.0 for good in TRADEABLE_GOODS
        }

        self.expected_prices: dict[Good, float] = {
            good: self.config.base_prices()[good]
            for good in TRADEABLE_GOODS
        }

        initial_food = max(
            self.config.food_consumption_per_tick + 1,
            config.initial_food
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
        self.add_good(Good.FOOD, initial_food, tick=0)

        initial_wood = max(
            0,
            config.initial_wood
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
        self.add_good(Good.WOOD, initial_wood, tick=0)

        initial_fiber = max(
            0,
            config.initial_fiber
            + rng.randint(-config.endowment_jitter, config.endowment_jitter),
        )
        self.add_good(Good.FIBER, initial_fiber, tick=0)

        initial_tool_count = max(
            0,
            config.initial_tools + rng.randint(0, config.endowment_jitter // 2),
        )
        self.add_good(Good.TOOLS, initial_tool_count, tick=0)

        # skills shouldn't decay below 1, or the baseline where u have 0 competence
        for skill in SkillDomain:
            self.state.skills[skill] = 1.0

    @property
    def agent_id(self) -> int:
        return self.state.agent_id
    
    @property
    def tools_count(self) -> int:
        return sum(lot.uses_remaining for lot in self.state.lots 
                   if lot.good == Good.TOOLS and lot.uses_remaining)

    def plan(self, tick: int, market_prices: dict[Good, float]) -> None:
        goal = self.choose_goal(tick)
        self.state.current_goal = goal
        recipe = self.choose_production(goal, market_prices, tick)
        self.state.current_recipe = recipe

    # calculates whether agent's inventory can afford recipe
    def can_afford_recipe(self, recipe: Recipe) -> bool:
        for good, qty in recipe.inputs.items():
            if good == Good.TOOLS:
                if self.tools_count < qty:
                    return False
            else:
                if self.state.inventory_of(good) < qty:
                    return False
        return True

    # Goal deals with hierarchy of needs. Immediate -> Long-Term
    def choose_goal(self, current_tick: int) -> Good | Goal:
        # If agent is about to die, the goal is fixed on food
        if self._food_critical(current_tick):
            return Good.FOOD

        if self.config.shelter_required and not self.state.has_shelter:
            return Good.SHELTER

        necessities = []
        food_short = self._shortage(Good.FOOD)
        if food_short > 0:
            necessities.append((Good.FOOD, food_short * self.config.food_urgency_weight))

        if self.config.clothing_enabled:
            clothes_short = self._shortage(Good.CLOTHES)
            if clothes_short > 0:
                necessities.append((Good.CLOTHES, clothes_short * self.config.comfort_urgency_weight))

        if necessities:
            necessities.sort(key=lambda x: x[1], reverse=True)
            top_good, top_score = necessities[0]
            # only act on it if urgency clears a minimum bar (avoids chasing trivial shortages)
            #if top_score >= self.config.necessity_activation_threshold:
            return top_good

        #    Opportunity mode – personality influences but does not dictate
        #    exploration_drive ∈ [0, 1]  (0 = pure profit seeker, 1 = pure explorer)
        #    We keep a stochastic mix so even high-exploration agents sometimes
        #    maximise profit and vice-versa.
        explore_p = getattr(self.archetype, "exploration_drive", 0.3)
        explore_p = max(0.05, min(0.85, explore_p))   # never 0 % or 100 %

        if self.rng.random() < explore_p:
            return Goal.EXPLORE
        else:
            return Goal.PROFIT

    def choose_production(self, goal: Goal | Good, market_prices: dict[Good, float], tick: int) -> Recipe | None:
        """Chooses the current recipe to execute based on goals."""
        # No active commitment: pick fresh, using the full scoring pass.
        candidates = self._recipes_toward_good(goal) if isinstance(goal, Good) else list(RECIPES)
        if not candidates:
            return None

        scored = sorted(
            ((self._score_recipe_for_goal(r, goal, market_prices), r) for r in candidates),
            key=lambda x: x[0], reverse=True,
        )

        if goal == Good.FOOD and self._food_critical(tick):
            chosen = scored[0][1]
            
            if not self.can_afford_recipe(chosen) or chosen.outputs != Good.FOOD:
                chosen = RECIPE_BY_NAME["forage"]
        else:
            top = scored[:min(2, len(scored))]
            if self.rng.random() < 0.70 or len(top) == 1:
                chosen = top[0][1]
            else:
                weights = [max(s, 0.05) for s, _ in top]
                chosen = self.rng.choices([r for _, r in top], weights=weights, k=1)[0]

        return chosen

    # ------------------------------------------------------------------
    # Inventory lots
    # ------------------------------------------------------------------

    def _shelf_life_for(self, good: Good) -> int | None:
        """Return the shelf life for a good, or None if it does not expire."""
        shelf_lives = getattr(self.config, "GOODS_SHELF_LIFE", {})
        return shelf_lives.get(good)

    def add_good(self, good: Good, qty: int, tick: int = -1) -> None:
        """Add produced/acquired goods as timestamped inventory lots."""
        if qty <= 0:
            return
        if tick < 0:
            raise ValueError("tick is required when adding inventory")

        shelf_life = self._shelf_life_for(good)

        if good == Good.TOOLS:
            # Each tool is its own lot because tools have independent durability.
            for _ in range(qty):
                self.state.lots.append(
                    InventoryLot(
                        good=good,
                        quantity=1,
                        created_tick=tick,
                        shelf_life=shelf_life,
                        uses_remaining=self.config.tool_max_uses,
                    )
                )
        else:
            # Stack ordinary goods into a single lot. A lot represents goods
            # produced/acquired at the same tick with the same decay rule.
            self.state.lots.append(
                InventoryLot(
                    good=good,
                    quantity=qty,
                    created_tick=tick,
                    shelf_life=shelf_life,
                    uses_remaining=None,
                )
            )

        self._sync_inventory()

    def use_good(self, good: Good, qty: int) -> None:
        """Consume ordinary goods FIFO; tools lose one durability use each."""
        if qty <= 0:
            return

        if good == Good.TOOLS:
            for _ in range(qty):
                if not self._use_tool():
                    raise ValueError(
                        f"Agent {self.agent_id} insufficient tools"
                    )
            return

        if self.state.inventory_of(good) < qty:
            raise ValueError(
                f"Agent {self.agent_id} insufficient {good.value}: "
                f"have {self.state.inventory_of(good)}, need {qty}"
            )

        remaining = qty
        new_lots: list[InventoryLot] = []

        # Oldest lots are consumed first (FIFO), which also keeps food usage
        # sensible when lots have different ages.
        for lot in sorted(self.state.lots, key=lambda x: x.created_tick):
            if lot.good != good or remaining <= 0:
                new_lots.append(lot)
                continue

            take = min(lot.quantity, remaining)
            lot.quantity -= take
            remaining -= take

            if lot.quantity > 0:
                new_lots.append(lot)

        if remaining > 0:
            raise ValueError(
                f"Agent {self.agent_id} insufficient {good.value}: "
                f"need {qty}, have {qty - remaining}"
            )

        self.state.lots = new_lots
        self._sync_inventory()

    def remove_good(self, good: Good, qty: int) -> None:
        """Remove goods entirely rather than consuming tool durability."""
        if qty <= 0:
            return

        if good == Good.TOOLS:
            remaining = qty
            new_lots: list[InventoryLot] = []

            # Remove whole tools, oldest first.
            for lot in sorted(self.state.lots, key=lambda x: x.created_tick):
                if lot.good != Good.TOOLS or remaining <= 0:
                    new_lots.append(lot)
                    continue

                take = min(lot.quantity, remaining)
                lot.quantity -= take
                remaining -= take

                if lot.quantity > 0:
                    new_lots.append(lot)

            if remaining > 0:
                raise ValueError(
                    f"Agent {self.agent_id} insufficient tools: need {qty}"
                )

            self.state.lots = new_lots
            self._sync_inventory()
            return

        self.use_good(good, qty)

    def decay_inventory(self, current_tick: int) -> dict[Good, int]:
        """
        Remove expired inventory lots.

        Returns the amount lost by good. Goods with shelf_life=None do not
        decay. Food can still be globally disabled through the existing
        food_shelf_life_enabled setting.
        """
        spoiled: dict[Good, int] = Counter()
        surviving: list[InventoryLot] = []

        food_decay_enabled = getattr(
            self.config, "food_shelf_life_enabled", True
        )

        for lot in self.state.lots:
            if lot.shelf_life is None:
                surviving.append(lot)
                continue

            if lot.good == Good.FOOD and not food_decay_enabled:
                surviving.append(lot)
                continue

            age = current_tick - lot.created_tick
            if age >= lot.shelf_life:
                spoiled[lot.good] += lot.quantity
            else:
                surviving.append(lot)

        self.state.lots = surviving
        self._sync_inventory()
        return dict(spoiled)

    def _sync_inventory(self) -> None:
        """Rebuild the simple inventory quantity cache from lots."""
        counts = {good: 0 for good in Good}
        for lot in self.state.lots:
            counts[lot.good] += lot.quantity
        self.state.inventory = counts

    def _use_tool(self) -> bool:
        """Consume one use from the oldest available tool."""
        for i, lot in enumerate(self.state.lots):
            if lot.good != Good.TOOLS:
                continue

            if lot.uses_remaining is None:
                raise ValueError("Tool lot has no durability")

            lot.uses_remaining -= 1

            if lot.uses_remaining <= 0:
                self.state.lots.pop(i)

            self._sync_inventory()
            return True

        return False

    def generate_orders(self, order_id_start: int,
        market_prices: dict[Good, float] | None = None):
        """Generates trading orders to help execute recipe
        At this point the recipe is determined by economic incentives, persoanlity etc
        Only executing based on goal
        """

        orders: list[Order] = []
        next_id = order_id_start
        prices = market_prices or self.config.base_prices()

        recipe = self.state.current_recipe
        goal = self.state.current_goal

        # Sell surplus
        for good in TRADEABLE_GOODS:
            surplus = self._surplus(good)
            if surplus < self.config.min_order_quantity:
                continue
            qty = min(
                surplus,
                max(self.config.min_order_quantity,
                    int(surplus * self.config.max_order_fraction)),
            )

            orders.append(Order(
                agent_id=self.agent_id,
                good=good,
                quantity=qty,
                price=self._reservation_ask_price(good, prices),
                is_bid=False,
                order_id=next_id,
            ))
            next_id += 1

        # Identify needs
        needed: dict[Good, int] = {}

        # Missing inputs of chosen recipe
        # Here the recipe should already be determined by economics etc
        if recipe is not None:
            for good, required in recipe.inputs.items():
                if good not in TRADEABLE_GOODS:
                    continue
                missing = max(0, required - self.state.inventory_of(good))
                if missing > 0:
                    needed[good] = max(needed.get(good, 0), max(1, missing))

            # inside generate_orders, needed section – after the recipe.inputs loop
            if any(recipe.inputs.values()):
                for good, required in recipe.inputs.items():
                    if good not in TRADEABLE_GOODS:
                        continue
                    missing = max(0, required - self.state.inventory_of(good))
                    if missing > 0:
                        needed[good] = max(needed.get(good, 0), missing)

        # Ignore and Good != GOOD.FIBER
        # if the problem is that they need fiber, then they buy it AND gather it
        # what do i do
        if isinstance(goal, Good) and goal in TRADEABLE_GOODS:
            short = self._shortage(goal)
            if short > 0:
                needed[goal] = max(needed.get(goal, 0), short)

        for good, shortage in needed.items():
            if shortage < self.config.min_order_quantity:
                continue
            # Do not bid for something we are already offering to sell
            if any(o.good == good and not o.is_bid for o in orders):
                continue

            unit_price = self._reservation_bid_price(good, prices)
            market_ref = prices.get(good, unit_price)
            affordable = int(self.state.money / max(0.01, market_ref))
            qty = min(shortage, max(1,affordable))

            if qty >= self.config.min_order_quantity and self.state.money > 0:
                orders.append(Order(
                    agent_id=self.agent_id,
                    good=good,
                    quantity=qty,
                    price=unit_price,
                    is_bid=True,
                    order_id=next_id,
                ))
                        
                next_id += 1
        return orders, next_id
    
    def record_sale(
        self,
        good: Good,
        offered: int,
        sold: int,
        price: float
    ) -> None:
        if offered <= 0:
            return

        realized_rate = max(0.0, min(1.0, sold / offered))

        old_rate = self.expected_sell_rate.get(good, 1.0)
        #old_price = self.expected_prices.get(good, 1.0)
        alpha = self.config.price_ema_alpha

        #print(1-alpha, old_rate, alpha*realized_rate)
        new_rate = (
            (1.0 - alpha) * old_rate
            + alpha * realized_rate
        )
        self.expected_sell_rate[good] = max(0.25, new_rate)
        #print(self.agent_id, good.name, self.expected_sell_rate[good])

        #self.expected_prices[good] = (
        #    (1 - alpha) * old_price
        #    + alpha * price
        #)

    def confirm_current_recipe(self, market_prices: dict[Good, float]) -> tuple[Recipe | None, bool]:
        """Confirms current recipe can be executed. Finds alternatives if it is invalid."""
        planned = self.state.current_recipe

        # If on-track
        if planned is not None and self.can_afford_recipe(planned):
            return planned, True
        # if planned recipe is impossible, find fallbacks that achieve same output item
        goal = self.state.current_goal

        if isinstance(goal, Good):
            alternatives = [
                r for r in self._recipes_toward_good(goal)
                if r is not planned and self.can_afford_recipe(r)
            ]
            if alternatives:
                chain_needs = self._chain_input_needs(goal)
                # Pick the highest-scoring *affordable* alternative
                best = max(
                    alternatives,
                    key=lambda r: self._score_recipe_for_goal(r, goal, market_prices),
                )

                return best, False
        # 3. Last resort: any affordable zero-input recipe (forage / gather_fiber / chop_wood)
        zero_input = [
            r for r in RECIPES
            if not r.inputs and self.can_afford_recipe(r)
        ]
        if zero_input:
            best = max(
                zero_input,
                key=lambda r: self._score_recipe_for_goal(r, goal, market_prices),
            )
            return best, False

        return None, False
        
    def _score_recipe_for_goal(self, recipe: Recipe, 
                           goal: Good | Goal | None, 
                           market_prices: dict[Good, float]) -> float:
        prices = market_prices or self.config.base_prices()
        score = 0.0
        productivity = self._productivity(recipe)  # already includes skill × affinity

        # Necessity pressure (unchanged)
        if isinstance(goal, Good):
            depth = self._chain_depth_of(recipe, goal, self.archetype.planning_depth_long)
            pressure = self._shortage(goal) * self._urgency_weight(goal)
            is_intermediate = any(g != goal for g in recipe.outputs)
            if is_intermediate:
                for out_good in recipe.outputs:
                    if self.state.inventory_of(out_good) >= 6:
                        pressure *= 0.1
            score += pressure * (self.config.chain_discount ** depth)

        # Economic term – simplified and productivity-weighted
        revenue = 0.0
        cost = 0.0
        for good, base_qty in recipe.outputs.items():
            if good not in TRADEABLE_GOODS and good != goal:
                continue
            qty = max(1, int(round(base_qty * productivity))) if base_qty > 0 else 0
            sell_rate = self.expected_sell_rate.get(good, 1.0)
            unit_price = prices.get(good, self.config.base_prices().get(good, 1.0))
            revenue += qty * unit_price * sell_rate

        for good, qty in recipe.inputs.items():
            have = self.state.inventory_of(good)
            missing = max(0, qty - have)
            unit = prices.get(good, self.config.base_prices().get(good, 1.0))
            if good == Good.TOOLS:
                unit = unit / self.config.tool_max_uses
                # Treat tools as partially amortised; do not penalise a full missing tool as heavily
                missing = max(0, math.ceil(missing / 2))
            cost += missing * unit

        # Key change: multiply economic surplus by productivity so high-skill agents see a larger edge
        # raise power for comparative advantage to be amplified
        economic = (revenue - cost) * (productivity ** 1.6) * self.archetype.profit_motivation
        score += economic

        # Explore bonus (keep, already organic)
        if goal == Goal.EXPLORE:
            affinity = self.skill_affinities.get(recipe.domain, 1.0)
            novelty = 1.0 - min(1.0, self.state.recipe_counts.get(recipe.name, 0) / 15.0)
            explore_bonus = (
                1.8 * affinity * novelty
                * getattr(self.archetype, "exploration_drive", 0.3)
            )
            score += explore_bonus

        return score

    def execute_production(self, recipe: Recipe, tick: int) -> dict[str, int]:
        # Consume recipe input items
        for good, qty in recipe.inputs.items():
            input_qty = qty
            self.use_good(good, input_qty)
        
        productivity = self._productivity(recipe)
        outputs: dict[str, int] = {}

        for good, base_qty in recipe.outputs.items():
            if good == Good.SHELTER:
                self.state.has_shelter = True
                outputs[good.value] = 1
                continue

            # Uniform quantity rule
            if base_qty <= 0:
                qty = 0
            else:
                expected = base_qty * productivity
                whole = int(expected)
                frac = expected - whole
                qty = whole + (1 if self.rng.random() < frac else 0)
                self.add_good(good, qty, tick)

            outputs[good.value] = qty

        self.state.last_recipe = recipe.name
        self.state.recipe_counts[recipe.name] = (
            self.state.recipe_counts.get(recipe.name, 0) + 1
        )
        self._apply_learning(recipe)
        self.state.recent_recipes.append(recipe.name)

        return outputs

    # how should I measure primary activity?
    def primary_activity(self, window=50) -> str | None:
        recent = getattr(self.state, "recent_recipes", None)
        if not recent:
            return self.state.last_recipe
        samples = list(recent)[-window:]
        return Counter(samples).most_common(1)[0][0]

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

    def _apply_learning(self, recipe: Recipe) -> None:
        if recipe.domain == SkillDomain.GATHERING:
             return

        current = self.state.skills.get(recipe.domain, 1.0)
        talent = self.skill_affinities.get(recipe.domain, 1.0)
        global_cap = self.config.skill_productivity_cap

        # Personal ceiling
        personal_cap = 1.0 + (global_cap - 1.0) * (talent ** 1.5)
        personal_cap = max(personal_cap, 1.05)          # never degenerate

        # Diminishing returns
        progress = max(0.0, (current - 1.0) / (personal_cap - 1.0))
        gain = (
            self.config.skill_gain_per_use
            * (1.0 - progress)
            * (talent ** 1.5)                           # milder than **3
        )

        self.state.skills[recipe.domain] = min(personal_cap, current + gain)

        # Decay unused skills
        for domain, skill in list(self.state.skills.items()):
            if domain == recipe.domain:
                continue
            if domain in (
                          SkillDomain.CONSTRUCTION,
                          SkillDomain.GATHERING
                          ):
                # Keep a usable floor for the critical chains
                floor = 1.0
            else:
                floor = 0.0

            last_used_tick = self._last_used_by_domain(domain)
            if last_used_tick > self.config.skill_decay_grace_period:
                self.state.skills[domain] = max(floor, skill - self.config.skill_decay_per_tick)

    def _last_used_by_domain(self, domain: SkillDomain) -> int:
        tick_count = 0
        for recipe_name in reversed(self.state.recent_recipes):
            tick_count += 1
            recipe = RECIPE_BY_NAME[recipe_name]
            if recipe.domain == domain:
                return tick_count
        return tick_count

    def _chain_input_needs(self, goal: Good | Goal | None) -> set[Good]:
        """Goods required anywhere along the recipe chain toward `goal`."""
        if not isinstance(goal, Good):
            return set()
        needed: set[Good] = set()
        for r in self._recipes_toward_good(goal):
            needed.update(r.inputs.keys())
        return needed

    def _productivity(self, recipe: Recipe) -> float:
        if recipe.domain == SkillDomain.GATHERING:
            return 1
        
        skill = self.state.skills.get(recipe.domain, 1.0)
        affinity = self.skill_affinities.get(recipe.domain, 1.0)

        skill_factor = 1.0 + self.config.skill_effect * (skill - 1.0)
        affinity_factor = 1.0 + self.config.affinity_effect * (affinity - 1.0)

        cap = self.config.skill_productivity_cap

        raw = skill_factor * affinity_factor

        if skill < recipe.min_skill:
            proficiency = skill / recipe.min_skill
            raw *= proficiency ** self.archetype.novice_penalty_exponent

        productivity_loss = 0
        if self.config.shelter_required and not self.state.has_shelter and recipe.name != "shelter":
            productivity_loss += self.config.shelter_productivity_loss

        if self._shortage(Good.CLOTHES) > 0 and self.config.clothing_enabled:
            productivity_loss += self.config.comfort_productivity_loss

        raw *= self._gathering_penalty(recipe)
        raw *= max(0.1, (1- productivity_loss))

        return min(cap, raw)

    def _gathering_penalty(self, recipe: Recipe) -> float:
        uses = self.state.recipe_counts.get(recipe.name, 0)
        return 1 / (1 + 0.001 * uses)

    def _recipes_toward_good(self, goal: Good) -> list[Recipe]:
            return GOAL_CHAIN_RECIPES[goal]

    # Very basic shortage algo, may not be necessary
    def _shortage(self, good: Good) -> int:
        if good == Good.FOOD:
            target = self.config.targets().get(Good.FOOD, 6)
            buffer = self.config.surplus_buffer
            inv = self.state.inventory_of(Good.FOOD)
            if inv >= target - buffer:   # already comfortable, don't re-trigger
                return 0
            return max(0, target - self.state.inventory_of(Good.FOOD))

        if good == Good.SHELTER:
            return 0 if self.state.has_shelter else 1

        if good == Good.CLOTHES:
            target = self.config.targets().get(Good.CLOTHES, 3)
            return max(0, target - self.state.inventory_of(Good.CLOTHES))
        return 0

    def _reservation_bid_price(self, good: Good, market_prices: dict[Good, float]) -> float:
        """
        Maximum Price agent is willing to pay for a good.
        """
        base = self.config.base_prices().get(good, 1.0)
        market_ref = (market_prices or {}).get(good, base)
        anchor = 0.80 * base + 0.20 * market_ref

        shortage = 0
        recipe = self.state.current_recipe

        # Finds out if buying 
        if self.state.current_goal == good:
            shortage = max(shortage, self._shortage(good))

        # Finds out what is missing for current recipe
        if recipe is not None:
            shortage = max(
                0,
                recipe.inputs.get(good, 0)
                - self.state.inventory_of(good),
            )

        if good == Good.FOOD:
            # Food uses a continuous urgency curve
            target = self.config.targets().get(Good.FOOD, 6)
            urgency = min(1.0, shortage / max(1, target))
        else:
            urgency = 1.0 if shortage > 0 else 0.0

        price = anchor * (1.0 + self.config.shortage_premium * urgency)

        # Don't dump all money into one trade
        if self.state.money > 0:
            price = min(price, self.state.money * 0.7)

        return price
        
    def _reservation_ask_price(self, good: Good, market_prices: dict[Good, float]) -> float:
        """
        Minimum price the agent is willing to accept.
        Anchored the same way, then lowered by surplus.
        """

        base = self.config.base_prices().get(good, 1.0)
        market_ref = (market_prices or {}).get(good, base)
        # Ratio of market influence to fundamental value
        anchor = 0.8 * base + 0.2 * market_ref

        surplus = self._surplus(good)
        if surplus <= 0:
            # Never sell below a floor when we have no surplus
            return anchor

        # Soft discount that grows with how much extra we hold
        discount = min(0.55, self.config.surplus_discount * (surplus / max(1, surplus + 3)))
        return max(0.35 * base, anchor * (1.0 - discount))

    def _surplus(self, good: Good) -> int:
        inventory = self.state.inventory_of(good)
        if inventory <= 0:
            return 0
        if good == Good.FOOD:
            target = self.config.targets().get(Good.FOOD, 6)
            return max(0, inventory - (target + self.config.surplus_buffer))

        reserve = 0
        recipe = self.state.current_recipe
        if recipe is not None:
            reserve = max(reserve, recipe.inputs.get(good, 0))
            if good in recipe.outputs:
                reserve = 0   # about to produce more of it

        if self.state.current_goal == good:
            reserve = max(reserve, 1)

        return max(0, inventory - reserve)

    def _urgency_weight(self, good: Good) -> float:
        weights = {
            Good.FOOD: self.config.food_urgency_weight,
            Good.SHELTER: self.config.shelter_urgency_weight,
            Good.CLOTHES: self.config.comfort_urgency_weight,
        }
        return weights[good]

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

    def consume_comfort(self) -> float:
        available = self.state.inventory_of(Good.CLOTHES)
        if available > 0:
            self.state.comfort_debt += self.config.comfort_consumption_per_tick
            to_remove = int(self.state.comfort_debt)
            if to_remove > 0:
                actual_removed = min(to_remove, available)
                if actual_removed > 0:
                    self.remove_good(Good.CLOTHES, actual_removed)
                self.state.comfort_debt -= actual_removed
        return self.config.comfort_consumption_per_tick

    def total_wealth(self, prices: dict[Good, float]) -> float:
        goods_value = sum(
            self.state.inventory_of(g) * prices.get(g, 0) for g in Good
        )
        return self.state.money + goods_value

    # ------------------------------------------------------------------
    # Food / consumption
    # ------------------------------------------------------------------

    def _food_critical(self, current_tick: int) -> bool:
        total = self.state.inventory_of(Good.FOOD)
        consumption = self.config.food_consumption_per_tick
        shelf = self.config.GOODS_SHELF_LIFE[Good.FOOD]
        spoiling_now = sum(
            lot.quantity for lot in self.state.lots
            if lot.good == Good.FOOD and current_tick - lot.created_tick >= shelf
        )
        effective_food = total - spoiling_now
        # Allow a slightly larger buffer before forcing pure survival
        return effective_food <= consumption  # dropped the + surplus_buffer

    def consume_food(self, tick) -> int:
        """Eat food oldest-first. Returns amount consumed."""
        needed = self.config.food_consumption_per_tick
        available = self.state.inventory_of(Good.FOOD)

        if available < needed:
            #print(f"STARVE agent={self.agent_id} total_food={available} "
            #          f"food_critical={self._food_critical(tick)} "  # pass tick in if not already available
             #         f"goal_last={self.state.current_goal}")
            
            self.state.alive = False
            print(f"DEATH REASON: {self.state.current_goal}, recipe: {self.state.current_recipe}")
            return 0

        self.use_good(Good.FOOD, needed)
        return needed

    # Backwards-compatible wrappers. The simulation can gradually migrate
    # from food-specific methods to the generic lot system.

    def _add_food(self, qty: int, tick: int) -> None:
        self.add_good(Good.FOOD, qty, tick)

    def _remove_food(self, qty: int) -> None:
        self.use_good(Good.FOOD, qty)

    def decay_food(self, current_tick: int) -> int:
        spoiled = self.decay_inventory(current_tick)
        return spoiled.get(Good.FOOD, 0)


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
