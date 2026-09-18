from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from econ_sim.config import DEFAULT_CONFIG, SimConfig
from econ_sim.metrics import SimReport
from econ_sim.sim_types import Good
from econ_sim.simulation import Simulation


def run_once(config: SimConfig, quiet: bool = True) -> dict:
    """Run a single simulation and return its summary dict. No printing, no argparse."""
    sim = Simulation(config=config)

    for _ in range(config.num_ticks):
        sim.step()

    report = SimReport(
        snapshots=sim.snapshots,
        final_agents=[a.snapshot() for a in sim.agents],
    )
    return report.summary()

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run the econ-sim multi-agent economic simulator."
    )
    p.add_argument("--seed", type=int, default=DEFAULT_CONFIG.seed)
    p.add_argument("--agents", type=int, default=DEFAULT_CONFIG.num_agents)
    p.add_argument("--ticks", type=int, default=DEFAULT_CONFIG.num_ticks)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Write event log and time series JSON to this directory.",
    )
    p.add_argument(
        "--agent-history",
        type=int,
        default=None,
        metavar="ID",
        help="Print full event history for a single agent.",
    )
    p.add_argument("--quiet", action="store_true", help="Only print final summary.")
    return p

def print_progress(sim: Simulation, every: int = 50) -> None:
    if not sim.snapshots:
        return
    snap = sim.snapshots[-1]
    if snap.tick % every != 0 and snap.tick != sim.config.num_ticks - 1:
        return
    last_action = ", ".join(f"{k}={v}" for k, v in sorted(snap.last_action.items()))
    prices = ", ".join(f"{k}={v:.1f}" for k, v in sorted(snap.prices.items()))
    alive = snap.agents_alive
    print(
        f"tick {snap.tick:4d} | trades={snap.total_trades:3d} | "
        f"gini={snap.gini:.3f} | {prices} | {last_action} | alive={alive}"
    )

def print_summary(report: SimReport) -> None:
    summary = report.summary()
    if not summary:
        print("No data to summarize.")
        return

    print("-" * 72)
    print("Summary")
    for k, v in summary.items():
        if k in ("skill_distribution", "wealth_by_archetype", "roles_by_archetype", "wealth_distribution"):
            continue
        print(f"  {k}: {v}")

    wealth_distribution = summary.get("wealth_distribution")
    if wealth_distribution:
        print("\n")
        print(wealth_distribution)

    # Skill distribution
    print("\nSkill Distribution")
    print(f"  {'domain':<14}{'min':>8}{'max':>8}{'median':>8}{'stdev':>8}{'n':>6}")
    for domain, stats in summary.get("skill_distribution", {}).items():
        print(
            f"  {domain:<14}{stats['min']:>8}{stats['max']:>8}"
            f"{stats['median']:>8}{stats['stdev']:>8}{stats['n']:>6}"
        )

    # Wealth by archetype
    wealth = summary.get("wealth_by_archetype", {})
    if wealth:
        print("\nWealth by Archetype")
        print(f"  {'archetype':<16}{'min':>9}{'max':>9}{'median':>9}{'stdev':>9}{'n':>5}{'>median%':>10}")
        for archetype, stats in wealth.items():
            print(
                f"  {archetype:<16}{stats['min']:>9}{stats['max']:>9}"
                f"{stats['median']:>9}{stats['stdev']:>9}{stats['n']:>5}"
                f"{stats['above_median_share']*100:>9.1f}%"
            )

    # Roles by archetype
    roles = summary.get("roles_by_archetype", {})
    if roles:
        print("\nRoles by Archetype")
        print(f"  {'archetype':<16}{'dominant_role':<16}{'share':>8}{'n':>5}   counts")
        for archetype, stats in roles.items():
            print(
                f"  {archetype:<16}{stats['dominant_role']!s:<16}"
                f"{stats['dominant_share']*100:>7.1f}%{stats['n']:>5}   {stats['counts']}"
            )

    print("-" * 72)

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = SimConfig(
        seed=args.seed,
        num_agents=args.agents,
        num_ticks=args.ticks,
    )
    sim = Simulation(config=config)

    if not args.quiet:
        print(f"Running econ-sim: {config.num_agents} agents, {config.num_ticks} ticks, seed={config.seed}")
        print("-" * 72)

    for _ in range(config.num_ticks):
        sim.step()
        if not args.quiet:
            print_progress(sim)

    e = sum(1 for agent in sim.agents if agent.state.has_shelter)
    print(f"Has shelter: {e}")
    f = sum(1 for agent in sim.agents if agent.state.inventory_of(Good.CLOTHES) > 0)
    print(f"Has Clothes: {f}")

    # Single report construction – carries live agents/prices for archetype stats
    report = SimReport(
        snapshots=sim.snapshots,
        final_agents=[a.snapshot() for a in sim.agents],
        _agents=sim.agents,
        _prices=sim._last_prices,
    )

    print_summary(report)

    if args.agent_history is not None:
        history = sim.agent_history(args.agent_history)
        print(f"\nAgent {args.agent_history} history ({len(history)} events):")
        print(json.dumps(history[:20], indent=2))
        if len(history) > 20:
            print(f"  ... and {len(history) - 20} more events")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "events.json").write_text(sim.event_log.to_json())
        (args.output_dir / "timeseries.json").write_text(
            json.dumps([s.__dict__ for s in sim.snapshots], indent=2)
        )
        (args.output_dir / "final_state.json").write_text(
            json.dumps(sim.full_state(), indent=2)
        )
        (args.output_dir / "report.json").write_text(
            json.dumps(report.summary(), indent=2)
        )
        print(f"\nWrote outputs to {args.output_dir}/")

    return 0

if __name__ == "__main__":
    sys.exit(main())
