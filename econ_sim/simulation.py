from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from econ_sim.agent import Agent, create_agents
from econ_sim.config import SimConfig
from econ_sim.events import EventLog, EventType
from econ_sim.market import Market
from econ_sim.metrics import SimReport, TickSnapshot, compute_tick_snapshot
from econ_sim.types import Good, TradeRecord


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
        self._last_prices = dict(self.config.base_prices())
        self._initial_total_money = sum(a.state.money for a in self.agents)

    def reset(self) -> None:
        self.event_log.clear()
        self.market = Market()
        self.tick = 0
        self.snapshots.clear()
        self.rng = random.Random(self.config.seed)
        self.agents = create_agents(self.config, self.rng)
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
        production_outputs = self._phase_production()
        bids, asks = self._phase_offers()
        trades = self._phase_matching(bids, asks)
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
            },
        )

        self.tick += 1
        return snapshot

    def _phase_production(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)

        for agent in self.agents:
            recipe = agent.choose_production()
            if recipe is None:
                continue
            outputs = agent.execute_production(recipe)
            for good, qty in outputs.items():
                totals[good] += qty
            self.event_log.record(
                self.tick,
                EventType.PRODUCTION,
                agent_id=agent.agent_id,
                recipe=recipe.name,
                outputs=outputs,
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

        return bids, asks

    def _phase_matching(self, bids: list, asks: list) -> list[TradeRecord]:
        result = self.market.clear(
            self.tick, bids, asks, use_midpoint=self.config.use_midpoint_pricing
        )
        agent_map = {a.agent_id: a for a in self.agents}

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
            consumed = agent.consume_food()
            if consumed > 0:
                self.event_log.record(
                    self.tick,
                    EventType.TICK,
                    agent_id=agent.agent_id,
                    action="consume_food",
                    quantity=consumed,
                )
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
