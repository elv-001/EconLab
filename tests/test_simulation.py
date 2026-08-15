"""Tests for econ-sim MVP."""

from __future__ import annotations

import json

from econ_sim.config import RECIPES, SimConfig
from econ_sim.events import EventType
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
