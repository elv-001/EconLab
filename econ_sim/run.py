from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from econ_sim.config import DEFAULT_CONFIG, SimConfig
from econ_sim.metrics import SimReport
from econ_sim.simulation import Simulation


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
    spec = ", ".join(f"{k}={v}" for k, v in sorted(snap.specialization.items()))
    prices = ", ".join(f"{k}={v:.1f}" for k, v in sorted(snap.prices.items()))
    print(
        f"tick {snap.tick:4d} | trades={snap.total_trades:3d} | "
        f"gini={snap.gini:.3f} | {prices} | {spec}"
    )


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

    report = SimReport(
        snapshots=sim.snapshots,
        final_agents=[a.snapshot() for a in sim.agents],
    )
    summary = report.summary()

    print("-" * 72)
    print("Summary")
    for k, v in summary.items():
        print(f"  {k}: {v}")

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
        print(f"\nWrote outputs to {args.output_dir}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
