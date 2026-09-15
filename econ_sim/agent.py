from __future__ import annotations

import random, math
from collections import deque, Counter

from econ_sim.config import (
    GOAL_CHAIN_RECIPES, RECIPES, SimConfig, RECIPE_BY_NAME, ENABLED_GOODS,
    TRADEABLE_GOODS, ARCHETYPE_MIX
)

from econ_sim.sim_types import (
    AgentState, Good, Order, Recipe, SkillDomain, 
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
        for good in ENABLED_GOODS:
            self._update_unmet_streak(good)

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
        # Survival first
        if self._food_critical(current_tick):
            return Good.FOOD

        if self.config.shelter_required and not self.state.has_shelter:
            return Good.SHELTER

        # Meaningful shortages (not just 1 unit)
        if self._shortage(Good.FOOD) >= 3:
            return Good.FOOD

        if self.config.clothing_enabled and self._shortage(Good.CLOTHES) >= 1:
            return Good.CLOTHES

        # Otherwise opportunity
        explore_p = max(0.05, min(0.7, getattr(self.archetype, "exploration_drive", 0.25)))
        if self.rng.random() < explore_p:
            return Goal.EXPLORE
        return Goal.PROFIT

    def choose_production(self, goal: Goal | Good, market_prices: dict[Good, float], tick: int) -> Recipe | None:
        if isinstance(goal, Good) and goal not in TRADEABLE_GOODS:
            candidates = self._recipes_toward_good(goal)
        else:
            # Tradeable necessity (food, clothes, wood, tools, fiber): buying is
            # always a live alternative via generate_orders' bid, so don't force
            # self-production — let the agent's specialty compete honestly
            # against the direct producer/chain recipes for this goal.
            candidates = list(RECIPES)
        if not candidates:
            return None

        scored = sorted(
            ((self._score_recipe_for_goal(r, goal, market_prices), r) for r in candidates),
            key=lambda x: x[0], reverse=True,
        )

        # Soft selection among top 2
        top = scored[:min(2, len(scored))]
        if self.rng.random() < 0.8 or len(top) == 1:
            chosen = top[0][1]
        else:
            weights = [max(s, 0.1) for s, _ in top]
            chosen = self.rng.choices([r for _, r in top], weights=weights, k=1)[0]

        # Very light safety net: only when food is critical AND the chosen recipe
        # cannot produce food at all. Do NOT force forage just because inputs are missing.
        if (goal == Good.FOOD
                and self._food_critical(tick)
                and Good.FOOD not in chosen.outputs):
            # Prefer any food-producing recipe we can actually run
            affordable_food = [
                r for r in candidates
                if Good.FOOD in r.outputs and self.can_afford_recipe(r)
            ]
            if affordable_food:
                chosen = max(
                    affordable_food,
                    key=lambda r: self._score_recipe_for_goal(r, goal, market_prices)
                )
            else:
                chosen = RECIPE_BY_NAME["forage"]

        return chosen

    def _update_unmet_streak(self, good: Good) -> int:
        shortage = self._shortage(good)
        if shortage > 0:
            streak = self.state.unmet_shortage_streak.get(good, 0) + 1
        else:
            streak = 0
        self.state.unmet_shortage_streak[good] = streak
        return streak

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
                    buy_fraction = 1.0 - 0.5 * self.state.self_reliance
                    wanted = max(1, math.ceil(missing * buy_fraction))
                    needed[good] = max(needed.get(good, 0), wanted)

        if isinstance(goal, Good) and goal in TRADEABLE_GOODS:
            short = self._shortage(goal)
            if short > 0:
                buy_fraction = 1.0 - 0.5 * self.state.self_reliance
                wanted = max(1, math.ceil(short * buy_fraction))
                needed[goal] = max(needed.get(goal, 0), wanted)

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
        #print(self.expected_sell_rate.get(Good.TOOLS), self.expected_sell_rate.get(Good.WOOD))

        #print(self.agent_id, good.name, self.expected_sell_rate[good])

        #self.expected_prices[good] = (
        #    (1 - alpha) * old_price
        #    + alpha * price
        #)

    def confirm_current_recipe(self, market_prices: dict[Good, float]) -> tuple[Recipe | None, bool]:
        planned = self.state.current_recipe
        if planned is not None and self.can_afford_recipe(planned):
            return planned, True

        goal = self.state.current_goal

        # Prefer any affordable alternative that still helps the goal
        if isinstance(goal, Good):
            alts = [r for r in self._recipes_toward_good(goal)
                    if r is not planned and self.can_afford_recipe(r)]
            if alts:
                best = max(alts, key=lambda r: self._score_recipe_for_goal(r, goal, market_prices))
                return best, False

        # NEW: do NOT fall back to zero-input.  
        # If we cannot afford anything useful, return None (agent produces nothing this tick).
        # This stops the automatic “I can’t craft → I forage” loop.
        return None, False
        
    def _score_recipe_for_goal(self, recipe: Recipe,
                           goal: Good | Goal | None,
                           market_prices: dict[Good, float]) -> float:
        prices = market_prices or self.config.base_prices()
        productivity = self._productivity(recipe)
        score = 0.0

        # --- Goal contribution ---
        if isinstance(goal, Good):
            streak = self.state.unmet_shortage_streak.get(goal, 0)
            persistence = 1.0 + min(2.5, streak / 12.0)
            if goal in recipe.outputs:
                score += (10.0 + self._shortage(goal) * self._urgency_weight(goal)) * persistence
            else:
                needed = self._chain_input_needs(goal)
                if any(g in recipe.outputs for g in needed):
                    score += 3.5 * persistence

        # --- Economic term ---
        revenue = 0.0
        for good, base_qty in recipe.outputs.items():
            if good not in TRADEABLE_GOODS and good != goal:
                continue
            qty = max(0, int(round(base_qty * productivity)))
            if qty == 0:
                continue
            sell_rate = self.expected_sell_rate.get(good, 0.7)
            unit = prices.get(good, self.config.base_prices().get(good, 1.0))
            revenue += qty * unit * sell_rate

        cost = 0.0
        missing_any = False
        for good, qty in recipe.inputs.items():
            missing = max(0, qty - self.state.inventory_of(good))
            if missing > 0:
                missing_any = True
            unit = prices.get(good, self.config.base_prices().get(good, 1.0))
            if good == Good.TOOLS:
                unit /= self.config.tool_max_uses
            cost += missing * unit

        net = (revenue - cost) * (productivity ** 1.5) * getattr(self.archetype, "profit_motivation", 1.0)
        score += net

        # Big bonus if the agent can actually run the recipe right now
        if not missing_any and recipe.inputs:
            score += 6.0

        # Exploration
        if goal == Goal.EXPLORE:
            affinity = self.skill_affinities.get(recipe.domain, 1.0)
            novelty = 1.0 - min(1.0, self.state.recipe_counts.get(recipe.name, 0) / 12.0)
            score += 2.0 * affinity * novelty * getattr(self.archetype, "exploration_drive", 0.3)

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
            * (1.0 - progress) ** 2
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
                floor = 0.3

            last_used_tick = self._last_used_by_domain(domain)
            grace = self.config.skill_decay_grace_period * (1.0 + self.archetype.time_preference)
            if last_used_tick > grace:
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
            proficiency = max(0.25, skill / recipe.min_skill)
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
        if recipe.domain == SkillDomain.GATHERING:
            uses = self.state.recipe_counts.get(recipe.name, 0)
            return 1 / (1 + 0.001 * uses)
        return 1

    def _recipes_toward_good(self, goal: Good) -> list[Recipe]:
            return GOAL_CHAIN_RECIPES[goal]

    # Very basic shortage algo, may not be necessary
    def _shortage(self, good: Good) -> int:
        if good == Good.FOOD:
            target = self.config.targets().get(Good.FOOD, 6)
            buffer = self.config.surplus_buffer * (1 + self.archetype.time_preference)
            inv = self.state.inventory_of(Good.FOOD)
            if inv >= target - buffer:   # already comfortable, don't re-trigger
                return 0
            return max(0, target - self.state.inventory_of(Good.FOOD))

        if good == Good.SHELTER:
            return 0 if self.state.has_shelter else 1

        if good == Good.CLOTHES:
            target = self.config.targets().get(Good.CLOTHES, 3)
            return max(0, (target + 1) - self.state.inventory_of(Good.CLOTHES))
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
            # Risk-tolerant agents will commit a bigger share of their cash to one trade.
            spend_cap = 0.5 + 0.3 * self.archetype.risk_tolerance
            price = min(price, self.state.money * spend_cap)

        return price
        
    def _reservation_ask_price(self, good: Good, market_prices: dict[Good, float]) -> float:
        """
        Minimum price the agent is willing to accept.
        Anchored the same way, then lowered by surplus.
        """

        base = self.config.base_prices().get(good, 1.0)
        market_ref = (market_prices or {}).get(good, base)
        # Ratio of market influence to fundamental value
        anchor = 0.75 * base + 0.25 * market_ref

        surplus = self._surplus(good)
        if surplus <= 0:
            # Never sell below a floor when we have no surplus
            return anchor

        # Soft discount that grows with how much extra we hold
        discount = min(0.65, self.config.surplus_discount * (surplus / max(1, surplus + 2)))

        # Pure quantity pressure (no age needed)
        inventory_pressure = min(0.5, 0.05 * surplus)
        discount = min(0.7, discount + inventory_pressure)

        return max(0.25 * base, anchor * (1.0 - discount))

    def _surplus(self, good: Good) -> int:
        inventory = self.state.inventory_of(good)
        if inventory <= 0:
            return 0
        if good == Good.FOOD:
            target = self.config.targets().get(Good.FOOD, 6)
            buffer = self.config.surplus_buffer * (1.0 + self.archetype.time_preference)
            return max(0, inventory - int((target + buffer)))

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
        safety_margin = self.config.surplus_buffer * (1.0 - self.archetype.risk_tolerance)
        return effective_food <= consumption + safety_margin  # dropped the + surplus_buffer

    def consume_food(self, tick) -> int:
        """Eat food oldest-first. Returns amount consumed."""
        needed = self.config.food_consumption_per_tick
        available = self.state.inventory_of(Good.FOOD)

        if available < needed:
            #print(f"STARVE agent={self.agent_id} total_food={available} "
            #          f"food_critical={self._food_critical(tick)} "  # pass tick in if not already available
             #         f"goal_last={self.state.current_goal}")
            
            self.state.alive = False
            #print(f"DEATH REASON: {self.state.current_goal}, recipe: {self.state.current_recipe}")
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
