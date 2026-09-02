from __future__ import annotations
import statistics

from collections import Counter
from dataclasses import dataclass, field

from econ_sim.agent import Agent
from econ_sim.config import SimConfig
from econ_sim.sim_types import Good, TradeRecord


@dataclass
class TickSnapshot:
    tick: int
    total_output: dict[str, int] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    trade_volume: dict[str, int] = field(default_factory=dict)
    total_trades: int = 0
    gini: float = 0.0
    last_action: dict[str, int] = field(default_factory=dict)
    specialization: dict[str, int] = field(default_factory=dict)
    avg_money: float = 0.0
    total_money: float = 0.0
    food_per_capita: float = 0.0
    agents_alive: int = 0


def gini_coefficient(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumulative = 0.0
    for i, v in enumerate(sorted_vals, start=1):
        cumulative += i * v
    return (2 * cumulative) / (n * total) - (n + 1) / n


def compute_specialization(agents: list[Agent]) -> dict[str, int]:
    """Count agents by their most-used recipe over the run."""
    counts: Counter[str] = Counter()
    for agent in agents:
        activity = agent.state.last_recipe
        if activity:
           counts[activity] += 1
        else:
           counts["idle"] += 1
    return dict(counts)

def compute_last_action(agents: list[Agent]) -> dict[str, int]:
    """Count agents by their last action."""
    counts: Counter[str] = Counter()
    for agent in agents:
        last_action = agent.state.last_recipe
        if last_action:
            counts[last_action] += 1
        else:
            counts["idle"] += 1
    return dict(counts)

def compute_tick_snapshot(
    tick: int,
    agents: list[Agent],
    trades: list[TradeRecord],
    production_outputs: dict[str, int],
    config: SimConfig,
    market_prices: dict[Good, float] | None = None,
) -> TickSnapshot:
    prices = market_prices or config.base_prices()
    wealths = [agent.total_wealth(prices) for agent in agents]
    money_values = [agent.state.money for agent in agents]

    trade_volume: dict[str, int] = {g.value: 0 for g in Good}
    for t in trades:
        trade_volume[t.good.value] += t.quantity
        """
    if tick >450:
        print(f"Median Money at {tick}:", statistics.median(a.state.money for a in agents))
        print(f"Average Money at {tick}:", statistics.mean(a.state.money for a in agents))
        print(f"Gini at {tick}:", gini_coefficient(wealths))
        """

    """
    median = statistics.median(a.total_wealth(prices) for a in agents)
    counter = Counter()
    e = 0
    for a in agents:
        #e += a.state.inventory[Good.FOOD]
        if a.total_wealth(prices) > median:
            counter[a.primary_activity()] += 1
    print(dict(counter))
    #print(f"Tick {tick}: ", e - len(agents)* config.food_consumption_per_tick)
    counter = Counter()
    wealth = Counter()
    c1 = Counter()
    
    for a in agents:
        activity = a.primary_activity()
        if activity:
            counter[activity] += a.state.money
            wealth[activity] += a.total_wealth(prices)
            c1[activity] += 1
    
    for k,v in wealth.items():
        wealth[k] = v/c1[k]
    print(dict(wealth))
    """

    food_per_capita = sum(agent.state.inventory_of(Good.FOOD) for agent in agents) / len(agents) if agents else 0

    price_snapshot = {g.value: round(prices.get(g, config.base_prices()[g]),2) for g in Good}

    return TickSnapshot(
        tick=tick,
        total_output=dict(production_outputs),
        prices=price_snapshot,
        trade_volume=trade_volume,
        total_trades=len(trades),
        gini=round(gini_coefficient(wealths), 4),
        last_action=compute_last_action(agents),
        specialization=compute_specialization(agents),
        avg_money=round(sum(money_values) / len(money_values), 2) if money_values else 0,
        total_money=round(sum(money_values), 2),
        food_per_capita=round(food_per_capita, 2),
        agents_alive = len(agents)
    )


@dataclass
class SimReport:
    snapshots: list[TickSnapshot] = field(default_factory=list)
    final_agents: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        if not self.snapshots:
            return {}
        first = self.snapshots[0]
        last = self.snapshots[-1]
        total_trades = sum(s.total_trades for s in self.snapshots)
        return {
            "ticks_run": last.tick + 1,
            "total_trades": total_trades,
            "gini_start": first.gini,
            "gini_end": last.gini,
            "specialization_end": last.specialization,
            "prices_end": last.prices,
            "avg_money_end": last.avg_money,
            "food_per_capita": last.food_per_capita,
            "agents_alive": last.agents_alive,
        }
