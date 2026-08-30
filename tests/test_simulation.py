"""Tests for econ-sim MVP."""

from __future__ import annotations

import json

from econ_sim.config import RECIPES, SimConfig
from econ_sim.events import EventType
from econ_sim.agent import create_agents
from econ_sim.simulation import Simulation
from econ_sim.types import Good


def test_recipes_defined():
    names = {r.name for r in RECIPES}
    assert names == {"forage", "chop_wood", "craft_tools", "hunt_farm"}


def test_simulation_runs_200_ticks():
    config = SimConfig(seed=123, num_agents=60, num_ticks=200)
    sim = Simulation(config=config)
    report = sim.run()
    assert len(report.snapshots) == 200
    assert report.summary()["total_trades"] > 0


def test_reproducibility():
    config = SimConfig(seed=99, num_agents=40, num_ticks=100)
    sim1 = Simulation(config=config)
    sim1.run()
    sim2 = Simulation(config=config)
    sim2.run()
    assert sim1.snapshots[-1].gini == sim2.snapshots[-1].gini
    assert sim1.snapshots[-1].specialization == sim2.snapshots[-1].specialization


def test_no_negative_inventories():
    config = SimConfig(seed=7, num_agents=80, num_ticks=300)
    sim = Simulation(config=config)
    sim.run()
    for agent in sim.agents:
        for good in Good:
            assert agent.state.inventory_of(good) >= 0


def test_agent_history():
    config = SimConfig(seed=42, num_agents=20, num_ticks=50)
    sim = Simulation(config=config)
    sim.run()
    history = sim.agent_history(0)
    assert len(history) > 0
    types = {e["event_type"] for e in history}
    assert EventType.PRODUCTION.value in types


def test_emergence_patterns():
    """After 400 ticks we expect trade, specialization, and wealth inequality."""
    config = SimConfig(seed=42, num_agents=80, num_ticks=400)
    sim = Simulation(config=config)
    report = sim.run()
    summary = report.summary()

    assert summary["total_trades"] > 50, "Expected substantial trade volume"
    assert summary["gini_end"] > 0.05, "Expected some wealth inequality"

    spec = summary["specialization_end"]
    assert len(spec) >= 2, "Expected multiple activity types"
    assert sum(spec.values()) == config.num_agents


def test_event_log_json_serializable():
    config = SimConfig(seed=1, num_agents=10, num_ticks=20)
    sim = Simulation(config=config)
    sim.run()
    data = json.loads(sim.event_log.to_json())
    assert len(data) > 0


def test_full_state_snapshot():
    config = SimConfig(seed=5, num_agents=15, num_ticks=30)
    sim = Simulation(config=config)
    sim.run()
    state = sim.full_state()
    assert state["tick"] == 30
    assert len(state["agents"]) == 15


def test_food_decay():
    config = SimConfig(
        seed=1,
        num_agents=1,
        num_ticks=20,
        food_shelf_life_ticks=5,
        food_consumption_per_tick=0,
    )
    agent = create_agents(config, __import__("random").Random(1))[0]
    agent.state.food_lots = [(10, 0)]
    agent._sync_food_inventory()

    spoiled = agent.decay_food(5)
    assert spoiled == 10
    assert agent.state.inventory_of(Good.FOOD) == 0

    agent.add_food(8, tick=6)
    assert agent.decay_food(10) == 0
    assert agent.state.inventory_of(Good.FOOD) == 8
    assert agent.decay_food(11) == 8


def test_forage_crowding():
    from econ_sim.config import forage_yield_per_agent

    config = SimConfig(forage_crowding_half_life=10, forage_min_yield=1)
    solo = forage_yield_per_agent(config, num_forgers=1, productivity=1.0, base_yield=2)
    crowded = forage_yield_per_agent(
        config, num_forgers=30, productivity=1.0, base_yield=2
    )
    assert solo == 2
    assert crowded < solo
    assert crowded >= config.forage_min_yield


def test_forage_crowding_in_simulation():
    config = SimConfig(seed=0, num_agents=40, num_ticks=1)
    sim = Simulation(config=config)
    sim.step()
    forage_events = [
        e
        for e in sim.event_log.events
        if e.event_type == EventType.PRODUCTION and e.data.get("recipe") == "forage"
    ]
    if len(forage_events) >= 2:
        num_forgers = forage_events[0].data["num_forgers"]
        assert num_forgers == len(forage_events)
        yields = [e.data["outputs"]["food"] for e in forage_events]
        if num_forgers > 1:
            assert max(yields) <= 2
