from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from econ_sim.agent import Agent, create_agents
from econ_sim.config import SimConfig
from econ_sim.events import EventLog, EventType
from econ_sim.market import Market
from econ_sim.metrics import SimReport, TickSnapshot, compute_tick_snapshot
from econ_sim.sim_types import ActionType, Good, Order, SkillDomain, TradeRecord


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
    _last_congestion: dict[str, float] = field(default_factory=dict)

    _initial_total_money: float = 0.0
    all_trades: list[TradeRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.config.seed)
        self.agents = create_agents(self.config, self.rng)
        self.dead_agents = []
        self._last_prices = dict(self.config.base_prices())
        self._initial_total_money = sum(a.state.money for a in self.agents)
        self._last_congestion = {}
        self._congestion_ema = {}

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
        self._last_congestion = {}
        self._congestion_ema = {}

    def run(self, num_ticks: int | None = None, quiet: bool = False) -> SimReport:
        ticks = num_ticks if num_ticks is not None else self.config.num_ticks
        for _ in range(ticks):
            self.step()
            if not quiet:
                self.print_progress()
        return SimReport(
            snapshots=list(self.snapshots),
            final_agents=[a.snapshot() for a in self.agents],
        )

    def print_progress(self, every: int = 50) -> None:
        if not self.snapshots:
            return
        snap = self.snapshots[-1]
        if snap.tick % every != 0 and snap.tick != self.config.num_ticks - 1:
            return
        last_action = ", ".join(f"{k}={v}" for k, v in sorted(snap.last_action.items()))
        prices = ", ".join(f"{k}={v:.1f}" for k, v in sorted(snap.prices.items()))
        alive = snap.agents_alive
        print(
            f"tick {snap.tick:4d} | trades={snap.total_trades:3d} | "
            f"gini={snap.gini:.3f} | {prices} | {last_action} | alive={alive}"
        )

    def step(self) -> TickSnapshot:
        self._phase_decisions()
        self._update_congestion()
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

        return snapshot

    def _phase_decisions(self):
        for agent in self.agents:
            agent.plan(self.tick, self._last_prices, self._last_congestion)

    def _phase_production(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)

        for agent in self.agents:
            outputs = agent.execute_action(self.tick, self._last_prices)
            if not outputs:
                continue

            for good, qty in outputs.items():
                totals[good] += qty

            self.event_log.record(
                self.tick,
                EventType.PRODUCTION,
                agent_id=agent.agent_id,
                recipe=agent.state.last_recipe,
                outputs=outputs,
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

        return bids, asks

    def _phase_matching(self, bids: list[Order], asks: list[Order]) -> list[TradeRecord]:
        result = self.market.clear(
            self.tick, bids, asks, use_midpoint=self.config.use_midpoint_pricing
        )
        agent_map = {a.agent_id: a for a in self.agents}

        sold_by_ask_id: dict[int, int] = defaultdict(int)

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

        goods = {o.good for o in bids}
        for good in goods:
            self._update_price_from_book(
                good, 
                [o for o in bids if o.good == good],
                [o for o in asks if o.good == good],
                result.trades
            )

        if self.config.check_invariants:
            self._check_invariants()

        return result.trades

    def _update_congestion(self) -> None:
        counts = Counter()
        n = max(1, len(self.agents))
        for agent in self.agents:
            action = agent.state.current_production_action
            if action and action.action_type == ActionType.PRODUCE and action.recipe \
            and action.recipe.domain == SkillDomain.GATHERING:
                counts[action.recipe.name] += 1
        raw = {name: c / n for name, c in counts.items()}
        alpha = self.config.congestion_ema_alpha  # e.g. 0.15, same spirit as price_ema_alpha
        for name in set(raw) | set(self._congestion_ema):
            prev = self._congestion_ema.get(name, 0.0)
            self._congestion_ema[name] = (1 - alpha) * prev + alpha * raw.get(name, 0.0)
        self._last_congestion = self._congestion_ema

    def _update_price_from_book(self, good, bids, asks, trades):
        base = self.config.base_prices()[good]
        prev = self._last_prices.get(good, base)
        reversion = 0.05

        good_trades = [t for t in trades if t.good == good]
        filled_qty = sum(t.quantity for t in good_trades)
        ask_qty = sum(o.quantity for o in asks if o.good == good)
        bid_qty = sum(o.quantity for o in bids if o.good == good)

        # Confidence scaling: don't let a handful of orders produce a maximal signal.
        # Ramps from 0 at near-zero volume to 1 once order flow is reasonably deep.
        min_confident_qty = max(5, self.config.num_agents * 0.05)
        ask_confidence = min(1.0, ask_qty / min_confident_qty)
        bid_confidence = min(1.0, bid_qty / min_confident_qty)

        unfilled_ask = max(0, ask_qty - filled_qty)
        unfilled_bid = max(0, bid_qty - filled_qty)
        ask_backlog = (unfilled_ask / ask_qty * ask_confidence) if ask_qty > 0 else 0.0
        bid_backlog = (unfilled_bid / bid_qty * bid_confidence) if bid_qty > 0 else 0.0

        if good_trades:
            vwap = sum(t.price * t.quantity for t in good_trades) / filled_qty
            anchor = (1 - reversion) * vwap + reversion * base
        elif bid_qty + ask_qty > 0:
            flow_imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
            momentum = prev * (1.0 + 0.15 * flow_imbalance)
            anchor = (1 - reversion) * momentum + reversion * base
        else:
            return

        pressure = 1.0 - 0.3 * ask_backlog + 0.10 * bid_backlog  # asymmetric per last message
        target_price = anchor * pressure

        lo = base * self.config.price_clamp_min_factor
        hi = base * self.config.price_clamp_max_factor
        target_price = max(lo, min(hi, target_price))

        self._last_prices[good] = (1 - self.config.price_ema_alpha) * prev + self.config.price_ema_alpha * target_price

    def _phase_housekeeping(self, trades: list[TradeRecord]) -> None:
        for agent in self.agents:
            agent.state.money = max(1, agent.state.money)
            consumed = agent.consume_food(self.tick)
            agent.decay_inventory(self.tick)
            agent.decay_sell_rate_belief()
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
            if self.config.clothing_enabled:
                agent.consume_comfort()
            
    def _check_invariants(self) -> None:
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
