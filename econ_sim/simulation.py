from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Counter

from econ_sim.agent import Agent, create_agents
from econ_sim.config import SimConfig
from econ_sim.events import EventLog, EventType
from econ_sim.market import Market
from econ_sim.metrics import SimReport, TickSnapshot, compute_tick_snapshot
from econ_sim.sim_types import Good, Recipe, TradeRecord


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

    def __post_init__(self) -> None:
        self.rng = random.Random(self.config.seed)
        self.agents = create_agents(self.config, self.rng)
        self.dead_agents = []
        self._last_prices = dict(self.config.base_prices())
        self._initial_total_money = sum(a.state.money for a in self.agents)

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
        return snapshot

    def _phase_decisions(self):
        for agent in self.agents:
            agent.choose_goal(self.tick)
            agent.choose_production(self._last_prices, self.tick)

    def _phase_production(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        choices: list[tuple[Agent, Recipe]] = []
        e=0

        for agent in self.agents:
            recipe,f = agent.get_affordable_recipe(self._last_prices)
            if not f:
                e+=1
            if recipe is not None:
                choices.append((agent, recipe))
        #print(f"Fallback: {e}")
        num_forgers = sum(1 for _, recipe in choices if recipe.name == "forage")
        recipe_counts = Counter(recipe.name for agent, recipe in choices)

        #for recipe, count in recipe_counts.items():
         #   print(f"{recipe}: {count} agent(s)")
        #print("TOOLS:", sum(agent.state.inventory[Good.TOOLS] for agent in self.agents))

        for agent, recipe in choices:
            outputs = agent.execute_production(
                recipe, tick=self.tick, num_forgers=num_forgers
            )
            for good, qty in outputs.items():
                totals[good] += qty
            log_data: dict[str, object] = {
                "recipe": recipe.name,
                "outputs": outputs,
            }
            if recipe.name == "forage":
                log_data["num_forgers"] = num_forgers
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

        for agent in self.agents:
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
            f"TOOLS BOOK: "
            f"bids={len(tool_bids)} "
            f"bid_qty={sum(o.quantity for o in tool_bids)} "
            f"asks={len(tool_asks)} "
            f"ask_qty={sum(o.quantity for o in tool_asks)} "
            f"max_bid={max((o.price for o in tool_bids), default=0):.2f} "
            f"min_ask={min((o.price for o in tool_asks), default=0):.2f}"
        )
        """
        return bids, asks

    def _phase_matching(self, bids: list, asks: list) -> list[TradeRecord]:
        result = self.market.clear(
            self.tick, bids, asks, use_midpoint=self.config.use_midpoint_pricing
        )
        agent_map = {a.agent_id: a for a in self.agents}

        tool_total = 0
        for trade in result.trades:
            buyer = agent_map[trade.buyer_id]
            seller = agent_map[trade.seller_id]
            cost = trade.price * trade.quantity

            if buyer.state.money < cost:
                if self.config.check_invariants:
                    self.event_log.record(
                        self.tick,
                        EventType.INVARIANT_VIOLATION,
                        agent_id=trade.buyer_id,
                        reason="insufficient_money",
                        required=cost,
                        available=buyer.state.money,
                    )
                continue

            buyer.state.money -= cost
            seller.state.money += cost
            if trade.good == Good.FOOD:
                buyer.add_food(trade.quantity, self.tick)
                seller.remove_food(trade.quantity)
            elif trade.good == Good.TOOLS:
                tool_total += 1
                buyer.add_tool(trade.quantity)
                seller.remove_tool(trade.quantity)
            else:
                buyer.state.add_good(trade.good, trade.quantity)
                seller.state.remove_good(trade.good, trade.quantity)

            buyer.record_trade(trade.seller_id, self.tick)
            seller.record_trade(trade.buyer_id, self.tick)

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

        if self.config.check_invariants:
            self._check_invariants(result.trades)
        #print(f"Tools Traded: {tool_total}")
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
            spoiled = agent.decay_food(self.tick)
            if spoiled > 0:
                self.event_log.record(
                    self.tick,
                    EventType.FOOD_DECAY,
                    agent_id=agent.agent_id,
                    quantity=spoiled,
                    shelf_life=self.config.food_shelf_life_ticks,
                )
            consumed = agent.consume_food()
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
            agent.decay_memory(self.tick)

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
