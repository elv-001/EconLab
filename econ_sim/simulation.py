from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Counter
import statistics

from econ_sim.agent import Agent, create_agents
from econ_sim.config import SimConfig
from econ_sim.events import EventLog, EventType
from econ_sim.market import Market
from econ_sim.metrics import SimReport, TickSnapshot, compute_tick_snapshot
from econ_sim.sim_types import Good, Recipe, TradeRecord, Order


@dataclass
class Simulation:
    config: SimConfig = field(default_factory=SimConfig)
    rng: random.Random = field(default_factory=random.Random)
    event_log: EventLog = field(default_factory=EventLog)
    market: Market = field(default_factory=Market)
    agents: list[Agent] = field(default_factory=list)
    tick: int = 0
    snapshots: list[TickSnapshot] = field(default_factory=list)
    _last_prices: dict[Good, float] = field(default_factory=dict)
    _initial_total_money: float = 0.0
    all_trades: list[TradeRecord] = field(default_factory=list)

    total_tool_use: int = 0
    total_clothes_produced: int = 0
    total_fiber_trades: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.config.seed)
        self.agents = create_agents(self.config, self.rng)
        self.dead_agents = []
        self._last_prices = dict(self.config.base_prices())
        self._initial_total_money = sum(a.state.money for a in self.agents)

        self.total_fiber_trades = {}

    def reset(self) -> None:
        self.event_log.clear()
        self.market = Market()
        self.tick = 0
        self.snapshots.clear()
        self.rng = random.Random(self.config.seed)
        self.agents = create_agents(self.config, self.rng)
        self.dead_agents.clear()
        self._last_prices = dict(self.config.base_prices())
        self._initial_total_money = sum(a.state.money for a in self.agents)
        self.all_trades.clear()

    def run(self, num_ticks: int | None = None) -> SimReport:
        ticks = num_ticks if num_ticks is not None else self.config.num_ticks
        for _ in range(ticks):
            self.step()
        return SimReport(
            snapshots=list(self.snapshots),
            final_agents=[a.snapshot() for a in self.agents],
        )

    def step(self) -> TickSnapshot:
        self._phase_decisions()
        bids, asks = self._phase_offers()
        trades = self._phase_matching(bids, asks)
        self.all_trades.extend(trades)

        production_outputs = self._phase_production()

        self._phase_housekeeping(trades)
        snapshot = compute_tick_snapshot(
            self.tick,
            self.agents,
            trades,
            production_outputs,
            self.config,
            self._last_prices,
        )
        self.snapshots.append(snapshot)
        
        self.event_log.record(
            self.tick,
            EventType.TICK,
            data={
                "gini": snapshot.gini,
                "total_trades": snapshot.total_trades,
                "prices": snapshot.prices,
                "specialization": snapshot.specialization,
                "food_per_capita": snapshot.food_per_capita,
            },
        )

        self.tick += 1
        if self.tick == 499:
            craft_counts = sorted((a.state.recipe_counts.get("craft_tools", 0) for a in self.agents), reverse=True)
            chop_counts = sorted((a.state.recipe_counts.get("chop_wood", 0) for a in self.agents), reverse=True)
            print("craft_tools top 10:", craft_counts[:10])
            print("chop_wood top 10:", chop_counts[:10])

            total_tools = sum(
                a.state.inventory_of(Good.TOOLS)
                for a in self.agents
            )

            total_wood = sum(
                a.state.inventory_of(Good.WOOD)
                for a in self.agents
            )

            tool_producers = [
                a for a in self.agents
                if a.state.current_recipe
                and a.state.current_recipe.name == "craft_tools"
            ]

            print(
                "tool producer inventory:",
                sum(a.state.inventory_of(Good.TOOLS) for a in tool_producers)
)

            print("total tools in inventory:", total_tools)
            print("total wood:", total_wood)
            print('total tool used:', int(self.total_tool_use/7))
            print("total clothes in inventory", sum(
                a.state.inventory_of(Good.CLOTHES)
                for a in self.agents
            ))
            print("total fiber in inventory", sum(
                            a.state.inventory_of(Good.FIBER)
                            for a in self.agents
                        ))
        if self.tick == 499:
            
            wealth_by_role = defaultdict(list)
            money_by_role = defaultdict(list)
    
            for agent in self.agents:
                role = agent.primary_activity() if agent.primary_activity() else "none"
                wealth_by_role[role].append(agent.total_wealth(self._last_prices))
                money_by_role[role].append(agent.state.money)
    
            print("\nWealth by Specialization")
            for role, wealth in sorted(wealth_by_role.items()):
                print(
                    f"  {role:<12} "
                    f"n={len(wealth):<3} "
                    f"min={min(wealth):>8.2f} "
                    f"median={statistics.median(wealth):>8.2f} "
                    f"mean={statistics.mean(wealth):>8.2f} "
                    f"max={max(wealth):>8.2f} "
                    f"stdev={statistics.stdev(wealth) if len(wealth) > 1 else 0:>8.2f}"
                )

            print("\nMoney by Specialization")
            for role, wealth in sorted(money_by_role.items()):
                print(
                    f"  {role:<12} "
                    f"n={len(wealth):<3} "
                    f"min={min(wealth):>8.2f} "
                    f"median={statistics.median(wealth):>8.2f} "
                    f"mean={statistics.mean(wealth):>8.2f} "
                    f"max={max(wealth):>8.2f} "
                    f"stdev={statistics.stdev(wealth) if len(wealth) > 1 else 0:>8.2f}"
                )

            #print('total clothes produced:', self.total_clothes_produced) # type: ignore

            """
            sales = Counter()
            revenue = Counter()

            for trade in self.all_trades:
                sales[trade.seller_id] += trade.quantity
                revenue[trade.seller_id] += int(trade.quantity * trade.price)

            print("\nSales / Revenue by Agent")

            for agent in sorted(self.agents, key=lambda a: a.state.money, reverse=True)[:15]:
                print(
                    f"id={agent.agent_id:<3} "
                    f"money={agent.state.money:>8.2f} "
                    f"sales={sales[agent.agent_id]:>5} "
                    f"revenue={revenue[agent.agent_id]:>9.2f}"
                )
            """

            print("\nFiber Buyers")

            for buyer_id, spent in sorted(
                self.total_fiber_trades.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                agent = next((a for a in self.agents if a.agent_id == buyer_id), None)
                if agent:
                    print(
                        f"id={buyer_id} "
                        f"specialization={agent.primary_activity()} "
                        f"fiber_spent={spent:.2f}"
                    )
            
            """"""
            print("\nSpecialization Economics")
            for role, agents in wealth_by_role.items():
                print(f"\n{role}")
                for agent in sorted(
                    [a for a in self.agents
                    if a.state.current_recipe and a.state.current_recipe.name == role],
                    key=lambda a: a.state.money,
                    reverse=True
                )[:5]:
                    if agent.state.current_recipe and agent.state.current_recipe.domain.name != 'GATHERING':
                        print(
                            f"  id={agent.agent_id} "
                            f"money={agent.state.money:.2f} "
                            f"skill={agent.state.skills[agent.state.current_recipe.domain]:.2f} "
                            f"inventory={agent.state.inventory}"
                    )
                    else:
                         print(
                            f"  id={agent.agent_id} "
                            f"money={agent.state.money:.2f} "
                            f"inventory={agent.state.inventory}"
                         )
                """"""
        return snapshot

    def _phase_decisions(self):
        for agent in self.agents:
            agent.plan(self.tick, self._last_prices)

    def _phase_production(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        choices: list[tuple[Agent, Recipe]] = []
        e=0
        tool_uses = 0
        wood_used = 0
        clothes_produced = 0
        wood_produced = 0
        fiber_produced = 0


        for agent in self.agents:
            recipe,f = agent.confirm_current_recipe(self._last_prices)
            if recipe is not None:
                if Good.TOOLS in recipe.inputs:
                    tool_uses += recipe.inputs[Good.TOOLS]

                if Good.WOOD in recipe.inputs:
                    wood_used += recipe.inputs[Good.WOOD]

                if Good.CLOTHES in recipe.outputs:
                    clothes_produced += recipe.outputs[Good.CLOTHES]

                if Good.WOOD in recipe.outputs:
                    wood_produced += recipe.outputs[Good.WOOD]

                if Good.FIBER in recipe.outputs:
                    fiber_produced += recipe.outputs[Good.FIBER]

            #print(
           #     f"tools: produced={tool_produced} demand={tool_uses} | "
            #    f"wood: produced={wood_produced} demand={wood_used}"
            #)
            if not f:
                e+=1
            

            if recipe is not None:
                choices.append((agent, recipe))
        #print(f"Tools produced this tick: {tool_produced}")
        #print(f"Total Tools produced: {self.total_tool_produce}")
        #print(f"Clothes produced this tick: {clothes_produced}")
        #print(f"Fiber produced this tick: {fiber_produced}")
        self.total_clothes_produced += clothes_produced
        self.total_tool_use += tool_uses

        #print(f"Fallback: {e}")
        recipe_counts = Counter(recipe.name for agent, recipe in choices)

        #for recipe, count in recipe_counts.items():
        #    print(f"{recipe}: {count} agent(s)")
        #print("FIBER:", sum(agent.state.inventory[Good.FIBER] for agent in self.agents))

        for agent, recipe in choices:
            outputs = agent.execute_production(
                recipe, tick=self.tick
            )
            for good, qty in outputs.items():
                totals[good] += qty
            log_data: dict[str, object] = {
                "recipe": recipe.name,
                "outputs": outputs,
            }

            self.event_log.record(
                self.tick,
                EventType.PRODUCTION,
                agent_id=agent.agent_id,
                **log_data,
            )

        return dict(totals)

    def _phase_offers(self) -> tuple[list, list]:
        bids = []
        asks = []
        next_id = self.market.next_order_id()

        agents = self.agents.copy()
        self.rng.shuffle(agents)

        for agent in agents:
            orders, next_id = agent.generate_orders(next_id, self._last_prices)
            for order in orders:
                if order.is_bid:
                    bids.append(order)
                    self.event_log.record(
                        self.tick,
                        EventType.BID,
                        agent_id=agent.agent_id,
                        good=order.good.value,
                        quantity=order.quantity,
                        price=round(order.price, 2),
                    )
                else:
                    asks.append(order)
                    self.event_log.record(
                        self.tick,
                        EventType.OFFER,
                        agent_id=agent.agent_id,
                        good=order.good.value,
                        quantity=order.quantity,
                        price=round(order.price, 2),
                    )

        tool_bids = [o for o in bids if o.good == Good.TOOLS]
        tool_asks = [o for o in asks if o.good == Good.TOOLS]
        """
        print(
            f"WOOD  bids={sum(o.quantity for o in bids if o.good == Good.WOOD)} "
            f"asks={sum(o.quantity for o in asks if o.good == Good.WOOD)} | "
            f"TOOLS bids={sum(o.quantity for o in bids if o.good == Good.TOOLS)} "
            f"asks={sum(o.quantity for o in asks if o.good == Good.TOOLS)}"
        )
        
        
        print(
            f"TOOLS BOOK: "
            f"bids={len(tool_bids)} "
            f"bid_qty={sum(o.quantity for o in tool_bids)} "
            f"asks={len(tool_asks)} "
            f"ask_qty={sum(o.quantity for o in tool_asks)} "
            f"max_bid={max((o.price for o in tool_bids), default=0):.2f} "
            f"min_ask={min((o.price for o in tool_asks), default=0):.2f}"
        )
        
        
        print(
        f"bids={len(bids)} "
        f"asks={len(asks)} "
        f"food_bids={sum(o.quantity for o in bids if o.good == Good.FOOD)} "
        f"food_asks={sum(o.quantity for o in asks if o.good == Good.FOOD)} "
        f"tool_bids={sum(o.quantity for o in bids if o.good == Good.TOOLS)} "
        f"tool_asks={sum(o.quantity for o in asks if o.good == Good.TOOLS)}"
    )
    """
        return bids, asks

    def _phase_matching(self, bids: list[Order], asks: list[Order]) -> list[TradeRecord]:
        result = self.market.clear(
            self.tick, bids, asks, use_midpoint=self.config.use_midpoint_pricing
        )
        agent_map = {a.agent_id: a for a in self.agents}

        ask_by_id = {a.order_id: a for a in asks}
        sold_by_ask_id: dict[int, int] = defaultdict(int)

        tool_total = 0
        for trade in result.trades:
            buyer = agent_map[trade.buyer_id]
            seller = agent_map[trade.seller_id]
            cost = trade.price * trade.quantity

            if buyer.state.money <= 0:
                continue

            affordable_qty = int( buyer.state.money / max(0.01, trade.price) )
            qty = min(trade.quantity, affordable_qty)

            if qty <= 0: 
                continue 

            cost = trade.price * qty

            buyer.state.money -= cost
            seller.state.money += cost

            buyer.add_good(trade.good, qty, self.tick)
            seller.remove_good(trade.good, qty)

            # NEW: accumulate actual fulfilled qty against the seller's original ask.
            # Need the ask's order_id on the trade — see note below if TradeRecord
            # doesn't currently carry it.
            sold_by_ask_id[trade.ask_order_id] += qty

            if trade.good == Good.FIBER:
                self.total_fiber_trades[trade.buyer_id] = (
                    self.total_fiber_trades.get(trade.buyer_id, 0)
                    + trade.quantity * trade.price
                )

            if trade.good == Good.TOOLS:
                tool_total += 1

            self._update_price(trade.good, trade.price)

            self.event_log.record(
                self.tick,
                EventType.TRADE,
                agent_id=trade.buyer_id,
                buyer_id=trade.buyer_id,
                seller_id=trade.seller_id,
                good=trade.good.value,
                quantity=trade.quantity,
                price=round(trade.price, 2),
                total_cost=round(cost, 2),
            )

        for ask in asks:
            seller = agent_map.get(ask.agent_id)
            if seller is None:
                continue
            sold = sold_by_ask_id.get(ask.order_id, 0)
            seller.record_sale(ask.good, offered=ask.quantity, sold=sold, price=ask.price)

        if self.config.check_invariants:
            self._check_invariants(result.trades)

        #print(f"TOOLS TRADED: {tool_total}")

        return result.trades

    def _update_price(self, good: Good, trade_price: float) -> None:
        base = self.config.base_prices()[good]
        lo = base * self.config.price_clamp_min_factor
        hi = base * self.config.price_clamp_max_factor
        clamped = max(lo, min(hi, trade_price))
        prev = self._last_prices.get(good, base)
        alpha = self.config.price_ema_alpha
        self._last_prices[good] = (1 - alpha) * prev + alpha * clamped

    def _phase_housekeeping(self, trades: list[TradeRecord]) -> None:
        for agent in self.agents:
            agent.decay_inventory(self.tick)
            consumed = agent.consume_food(self.tick)
            if consumed > 0:
                self.event_log.record(
                    self.tick,
                    EventType.TICK,
                    agent_id=agent.agent_id,
                    action="consume_food",
                    quantity=consumed,
                )
            else:
                self.event_log.record(
                    self.tick,
                    EventType.TICK,
                    agent_id=agent.agent_id,
                    action="starvation",
                )
                self.agents.remove(agent)
                self.dead_agents.append(agent)
                # on death
                recent = list(getattr(agent.state, "recent_recipes", []))[-20:]
                #print(f"DEATH agent={agent.agent_id} tick={self.tick} recent_recipes={Counter(recent)}")

            if self.config.clothing_enabled:
                agent.consume_comfort()
            
    def _check_invariants(self, trades: list[TradeRecord]) -> None:
        for agent in self.agents:
            for good in Good:
                if agent.state.inventory_of(good) < 0:
                    self.event_log.record(
                        self.tick,
                        EventType.INVARIANT_VIOLATION,
                        agent_id=agent.agent_id,
                        reason="negative_inventory",
                        good=good.value,
                        amount=agent.state.inventory_of(good),
                    )

        total_money = sum(a.state.money for a in self.agents)
        goods_value_in_trades = sum(t.price * t.quantity for t in trades)
        if total_money < 0:
            self.event_log.record(
                self.tick,
                EventType.INVARIANT_VIOLATION,
                reason="negative_total_money",
                total=total_money,
            )

    def agent_history(self, agent_id: int) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.event_log.agent_history(agent_id)]

    def full_state(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "seed": self.config.seed,
            "agents": [a.snapshot() for a in self.agents],
            "last_prices": {g.value: p for g, p in self._last_prices.items()},
            "config": {
                "num_agents": self.config.num_agents,
                "num_ticks": self.config.num_ticks,
            },
        }

    def save_snapshot(self) -> dict[str, Any]:
        """Serializable full-state snapshot for reproducibility."""
        return {
            "tick": self.tick,
            "seed": self.config.seed,
            "rng_state": self.rng.getstate(),
            "agents": [a.snapshot() for a in self.agents],
            "last_prices": {g.value: p for g, p in self._last_prices.items()},
        }
