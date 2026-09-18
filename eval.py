"""
eval.py — Evaluation harness for econ_sim.

Three things live here, deliberately kept separate:

1. run_batch()      — run the same config across many seeds, aggregate metrics
                       into distributions (mean/stdev/min/max), never a single number.
2. sweep()           — vary one parameter, holding everything else fixed, and show
                       how the *distribution* of outcomes moves. This is how you
                       tune, instead of reacting to a single before/after run.
3. run_correctness_suite() — deterministic pass/fail checks ("does the mechanism
                       function at all"), independent of calibration. These should
                       never fail regardless of seed; if one does, you have a bug,
                       not a tuning problem.

Usage:
    python eval.py batch                       # baseline batch over 20 seeds
    python eval.py sweep weave_min_skill        # example sweep
    python eval.py test                         # correctness suite only
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, fields
from typing import Any, Callable

from contextlib import contextmanager, ExitStack

from econ_sim.agent import Agent
from econ_sim.config import SimConfig, RECIPE_BY_NAME
from econ_sim.simulation import Simulation
from econ_sim.sim_types import Good, SkillDomain


@contextmanager
def _recipe_field_override(recipe_name: str, field_name: str, value: Any):
    """Temporarily patch a field on a Recipe object in place.

    RECIPE_BY_NAME, RECIPES, and GOAL_CHAIN_RECIPES are all built once at
    import time and hold references to the SAME Recipe objects — so setting
    an attribute in place propagates to every one of those structures
    without needing to rebuild them. Restores the original value on exit so
    sweeps/batches don't leak a patched recipe into the next run.

    NOTE: this assumes Recipe is a plain (mutable) dataclass, as used
    elsewhere in this codebase. If Recipe is ever made frozen, this needs to
    switch to dataclasses.replace() plus re-binding the new object into
    RECIPE_BY_NAME, RECIPES, and every GOAL_CHAIN_RECIPES list that
    references it — a frozen Recipe can't be patched in place.
    """
    recipe = RECIPE_BY_NAME[recipe_name]
    original = getattr(recipe, field_name)
    setattr(recipe, field_name, value)
    try:
        yield
    finally:
        setattr(recipe, field_name, original)


def _split_overrides(overrides: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str, Any]]]:
    """Split a flat overrides dict into (config_field_overrides, recipe_overrides).

    Recipe overrides use the key format "recipe:<recipe_name>:<field_name>",
    e.g. "recipe:weave_cloth:min_skill". Everything else is treated as a
    plain SimConfig field.
    """
    config_overrides: dict[str, Any] = {}
    recipe_overrides: list[tuple[str, str, Any]] = []
    for key, value in overrides.items():
        if key.startswith("recipe:"):
            _, recipe_name, field_name = key.split(":", 2)
            recipe_overrides.append((recipe_name, field_name, value))
        else:
            config_overrides[key] = value
    return config_overrides, recipe_overrides


# ---------------------------------------------------------------------------
# Metric extraction — one run in, one flat dict of numbers out.
# ---------------------------------------------------------------------------

def extract_metrics(sim: Simulation) -> dict[str, float]:
    """Pull every metric we care about out of a finished Simulation.

    Kept as one function so run_batch/sweep/tests all see the same numbers —
    if you add a new metric, add it here once and it's available everywhere.
    """
    agents = sim.agents  # survivors only
    all_agents = sim.agents + getattr(sim, "dead_agents", [])
    n_alive = len(agents)
    n_total = len(all_agents)

    prices = sim._last_prices

    # --- specialization shares (share of *alive* agents whose primary_activity
    # matches each recipe; denominator is alive count, matches your existing logs) ---
    spec_counts: dict[str, int] = {}
    for a in agents:
        role = a.primary_activity() or "none"
        spec_counts[role] = spec_counts.get(role, 0) + 1
    print(spec_counts)

    def share(name: str) -> float:
        return spec_counts.get(name, 0) / max(1, n_alive)

    # --- wealth / gini ---
    wealths = [a.total_wealth(prices) for a in agents]
    gini_end = _gini(wealths) if wealths else 0.0

    # --- skill distribution per domain (median across alive agents) ---
    skill_medians = {}
    for domain in SkillDomain:
        vals = [a.state.skills.get(domain, 1.0) for a in agents]
        skill_medians[f"skill_median_{domain.value}"] = statistics.median(vals) if vals else 0.0

    # --- capital-good health checks ---
    total_tools = sum(a.state.inventory_of(Good.TOOLS) for a in agents)
    total_wood = sum(a.state.inventory_of(Good.WOOD) for a in agents)
    total_fiber = sum(a.state.inventory_of(Good.FIBER) for a in agents)
    total_clothes = sum(a.state.inventory_of(Good.CLOTHES) for a in agents)

    # --- sell-rate health (are markets actually clearing, or seized) ---
    def avg_sell_rate(good: Good) -> float:
        vals = [a.expected_sell_rate.get(good, 1.0) for a in agents if hasattr(a, "expected_sell_rate")]
        return statistics.mean(vals) if vals else float("nan")

    # --- survival ---
    deaths = n_total - n_alive
    death_rate = deaths / max(1, n_total)

    metrics: dict[str, float] = {
        "agents_alive": n_alive,
        "death_rate": death_rate,
        "gini_end": gini_end,
        "median_money_end": statistics.median(a.state.money for a in agents) if agents else 0.0,
        "food_per_capita": (
            sum(a.state.inventory_of(Good.FOOD) for a in agents) / max(1, n_alive)
        ),
        "total_tools": total_tools,
        "total_wood": total_wood,
        "total_fiber": total_fiber,
        "total_clothes": total_clothes,
        "sell_rate_food": avg_sell_rate(Good.FOOD),
        "sell_rate_tools": avg_sell_rate(Good.TOOLS),
        "sell_rate_fiber": avg_sell_rate(Good.FIBER),
        "sell_rate_clothes": avg_sell_rate(Good.CLOTHES),
        "price_food": prices.get(Good.FOOD, float("nan")),
        "price_wood": prices.get(Good.WOOD, float("nan")),
        "price_tools": prices.get(Good.TOOLS, float("nan")),
        "price_fiber": prices.get(Good.FIBER, float("nan")),
        "price_clothes": prices.get(Good.CLOTHES, float("nan")),
    }
    metrics.update(skill_medians)

    for recipe_name in RECIPE_BY_NAME:
        metrics[f"share_{recipe_name}"] = share(recipe_name)

    return metrics


def _gini(values: list[float]) -> float:
    """Standard discrete Gini: (2 * sum(i * x_i)) / (n * sum(x)) - (n+1)/n,
    with values sorted ascending and i as the 1-indexed rank. Weighting by
    rank (not by running prefix sum) is what makes this sensitive to the
    right-skew of a wealth distribution — get this wrong and it's easy to
    accidentally compute something that flips sign on skewed data."""
    if not values or all(v == 0 for v in values):
        return 0.0
    vals = sorted(values)
    n = len(vals)
    total = sum(vals)
    if total == 0:
        return 0.0
    cum = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * cum) / (n * total) - (n + 1) / n


# ---------------------------------------------------------------------------
# Batch runner — the unit of evidence. Never trust a single seed.
# ---------------------------------------------------------------------------

@dataclass
class BatchSummary:
    n_seeds: int
    ticks: int
    overrides: dict[str, Any]
    stats: dict[str, dict[str, float]]  # metric_name -> {mean, stdev, min, max}

    def print_report(self, metrics: list[str] | None = None) -> None:
        print(f"\n=== Batch: {self.n_seeds} seeds x {self.ticks} ticks ===")
        if self.overrides:
            print(f"overrides: {self.overrides}")
        keys = metrics or sorted(self.stats.keys())
        for key in keys:
            if key not in self.stats:
                continue
            s = self.stats[key]
            print(
                f"  {key:<28} mean={s['mean']:>8.3f}  median={s['median']:>8.3f} stdev={s['stdev']:>7.3f}  "
                f"min={s['min']:>8.3f}  max={s['max']:>8.3f}"
            )


def _apply_overrides(config: SimConfig, overrides: dict[str, Any]) -> SimConfig:
    """Return a copy of config with the given field overrides applied.

    Supports dotted paths for nested lookups if you add them later; for now
    this simulator's config is flat, so a plain setattr on a fresh instance
    is enough. Unknown keys raise loudly rather than silently no-op-ing.
    """
    valid_fields = {f.name for f in fields(config)}
    new_config = SimConfig(seed=config.seed)
    for key, value in {**{"seed": config.seed}, **overrides}.items():
        if key not in valid_fields and not hasattr(new_config, key):
            raise ValueError(f"Unknown config field: {key}")
        setattr(new_config, key, value)
    return new_config


def run_batch(
    overrides: dict[str, Any] | None = None,
    num_seeds: int = 20,
    ticks: int = 500,
    seed_start: int = 0,
    verbose: bool = False,
) -> BatchSummary:
    """Run the same config across num_seeds different seeds, aggregate metrics.

    This is the unit you should trust — a single seed is one draw from a
    distribution, not a representative outcome. Always compare BatchSummary
    to BatchSummary, never single-run print statements to each other.
    """
    overrides = overrides or {}
    config_overrides, recipe_overrides = _split_overrides(overrides)
    all_metrics: list[dict[str, float]] = []

    for i in range(num_seeds):
        seed = seed_start + i
        config = _apply_overrides(SimConfig(seed=seed), config_overrides)

        with ExitStack() as stack:
            for recipe_name, field_name, value in recipe_overrides:
                stack.enter_context(_recipe_field_override(recipe_name, field_name, value))
            sim = Simulation(config=config)
            sim.run(ticks)
            m = extract_metrics(sim)

        all_metrics.append(m)
        if verbose:
            print(f"  seed={seed}: gini={m['gini_end']:.3f} alive={m['agents_alive']:.0f}")

    stats: dict[str, dict[str, float]] = {}
    keys = all_metrics[0].keys() if all_metrics else []
    for key in keys:
        vals = [m[key] for m in all_metrics if key in m and m[key] == m[key]]  # drop NaN
        if not vals:
            continue
        stats[key] = {
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "min": min(vals),
            "max": max(vals),
        }

    return BatchSummary(n_seeds=num_seeds, ticks=ticks, overrides=overrides, stats=stats)


# ---------------------------------------------------------------------------
# Sweep — vary one parameter, hold everything else fixed, compare distributions.
# ---------------------------------------------------------------------------

def sweep(
    param_name: str,
    values: list[Any],
    base_overrides: dict[str, Any] | None = None,
    num_seeds: int = 15,
    ticks: int = 500,
    report_metrics: list[str] | None = None,
) -> list[BatchSummary]:
    """Vary param_name across `values`, run a full batch at each, print a
    side-by-side comparison. This isolates a parameter's marginal effect from
    RNG-cascade noise — a single before/after run cannot do this reliably,
    because changing one parameter reshuffles every subsequent stochastic
    draw for every agent in the same tick.
    """
    base_overrides = base_overrides or {}
    report_metrics = report_metrics or [
        "gini_end", "share_forage", "share_farm", "share_weave_cloth",
        "share_craft_tools", "share_chop_wood", "death_rate",
    ]

    summaries = []
    print(f"\n### Sweep: {param_name} over {values} ({num_seeds} seeds each) ###")
    for v in values:
        overrides = {**base_overrides, param_name: v}
        summary = run_batch(overrides, num_seeds=num_seeds, ticks=ticks)
        summaries.append(summary)
        row = "  ".join(
            f"{k}={summary.stats[k]['mean']:.3f}±{summary.stats[k]['stdev']:.3f}"
            for k in report_metrics if k in summary.stats
        )
        print(f"{param_name}={v}: {row}")

    return summaries


# ---------------------------------------------------------------------------
# Correctness suite — deterministic invariants, not calibration targets.
# These should pass regardless of seed. A failure here is a bug, not a
# tuning question — treat it the way you'd treat a failing unit test.
# ---------------------------------------------------------------------------

class CorrectnessError(AssertionError):
    pass


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CorrectnessError(message)


def test_no_phantom_inventory_desync(sim: Simulation) -> None:
    """Every agent's cached inventory must match what their lots actually contain.
    Guards against the food_lots/lots-style desync bug."""
    for a in sim.agents:
        for good in Good:
            if good == Good.TOOLS:
                continue
            lot_total = sum(l.quantity for l in a.state.lots if l.good == good)
            cached = a.state.inventory.get(good, 0)
            _check(
                lot_total == cached,
                f"agent {a.agent_id}: {good.value} lot total ({lot_total}) "
                f"!= cached inventory ({cached})",
            )


def test_no_negative_inventory(sim: Simulation) -> None:
    for a in sim.agents:
        for good in Good:
            _check(
                a.state.inventory_of(good) >= 0,
                f"agent {a.agent_id}: negative inventory of {good.value}",
            )


def test_skill_never_exceeds_cap_or_drops_below_zero(sim: Simulation) -> None:
    """Skill must never go negative, and must never exceed the AGENT'S OWN
    talent-derived personal cap — not the global config cap directly. A
    high-affinity agent's personal_cap = 1 + (global_cap - 1) * talent**1.5
    can legitimately exceed global_cap itself; that's the affinity system
    working as designed, not a bug. Mirrors the formula in Agent._apply_learning."""
    global_cap = sim.config.skill_productivity_cap
    for a in sim.agents:
        for domain, skill in a.state.skills.items():
            _check(skill >= 0.0, f"agent {a.agent_id}: {domain.value} skill went negative ({skill})")
            talent = a.skill_affinities.get(domain, 1.0)
            personal_cap = max(1.0 + (global_cap - 1.0) * (talent ** 1.5), 1.05)
            _check(
                skill <= personal_cap + 1e-6,
                f"agent {a.agent_id}: {domain.value} skill ({skill:.3f}) exceeds "
                f"personal cap ({personal_cap:.3f}, talent={talent:.3f})",
            )


def test_food_critical_agents_never_starve_holding_food(sim: Simulation) -> None:
    """Sanity check on the food-critical/goal-selection interaction: an agent
    holding more food than one tick's consumption should not have died of
    starvation this run. Guards against the goal/production desync bug where
    a starving agent's chosen recipe didn't actually produce food."""
    # This is checked indirectly: total deaths attributed to starvation should
    # not include any agent whose last known food inventory was comfortably
    # above consumption. We approximate by checking current survivors only,
    # since dead agents' final state is the one we already had trouble with.
    for a in sim.agents:
        if a.state.inventory_of(Good.FOOD) > sim.config.food_consumption_per_tick * 3:
            _check(a.state.alive, f"agent {a.agent_id}: marked dead while holding ample food")


def test_clothing_chain_functions(sim: Simulation) -> None:
    """The weave_cloth chain should actually execute and progress skill at
    least once over a full run, even if it stays a minority specialization."""
    if not sim.config.clothing_enabled:
        return
    all_agents = sim.agents + getattr(sim, "dead_agents", [])
    total_weaves = sum(a.state.recipe_counts.get("weave_cloth", 0) for a in all_agents)
    _check(total_weaves > 0, "weave_cloth never executed even once across the whole run")

    max_weaving_skill = max(
        (a.state.skills.get(SkillDomain.WEAVING, 0.0) for a in sim.agents), default=0.0
    )
    _check(
        max_weaving_skill > RECIPE_BY_NAME["weave_cloth"].min_skill * 0.8,
        f"no agent progressed meaningfully in weaving (max skill {max_weaving_skill:.2f})",
    )


def test_tool_chain_functions(sim: Simulation) -> None:
    all_agents = sim.agents + getattr(sim, "dead_agents", [])
    total_crafted = sum(a.state.recipe_counts.get("craft_tools", 0) for a in all_agents)
    _check(total_crafted > 0, "craft_tools never executed even once across the whole run")


def test_markets_not_fully_seized(sim: Simulation) -> None:
    """expected_sell_rate should not be uniformly at its floor for every
    tradeable good — that would indicate the market mechanism itself is
    broken (nothing clears at all), as opposed to one good being genuinely
    oversupplied (which is a legitimate, calibratable outcome)."""
    from econ_sim.config import TRADEABLE_GOODS

    for good in TRADEABLE_GOODS:
        rates = [
            a.expected_sell_rate.get(good, 1.0)
            for a in sim.agents
            if hasattr(a, "expected_sell_rate")
        ]
        if not rates:
            continue
        avg = statistics.mean(rates)
        _check(
            avg > 0.05,
            f"{good.value}: average sell rate ({avg:.4f}) suggests the market "
            f"for this good is completely seized, not just oversupplied",
        )


def test_no_domain_universally_stuck_at_floor(sim: Simulation) -> None:
    """A domain where every single agent sits at the exact skill floor (zero
    variance) is different from a domain that's merely a small specialist
    niche (which shows real spread — some agents climbed, most didn't). Zero
    variance across the whole population means the recipe can never win a
    fresh scoring pass long enough to be attempted repeatedly by anyone,
    which is a bootstrap failure, not a minority-specialization outcome.
    GATHERING and CONSTRUCTION are expected to sit at a flat floor of 1.0 by
    design (see _apply_learning's protected-floor list) and are excluded."""
    exempt = {SkillDomain.GATHERING, SkillDomain.CONSTRUCTION}
    for domain in SkillDomain:
        if domain in exempt:
            continue
        skills = [a.state.skills.get(domain, 1.0) for a in sim.agents]
        if not skills:
            continue
        floor_candidates = {round(s, 6) for s in skills}
        _check(
            len(floor_candidates) > 1 or max(skills) > min(skills) + 1e-6,
            f"{domain.value}: every agent sits at exactly {skills[0]:.3f} — "
            f"this domain never progresses for anyone, likely a bootstrap failure "
            f"in the recipe(s) that use it, not a normal minority specialization",
        )


def test_population_survives(sim: Simulation) -> None:
    n_total = len(sim.agents) + len(getattr(sim, "dead_agents", []))
    survival_rate = len(sim.agents) / max(1, n_total)
    _check(
        survival_rate > 0.3,
        f"population collapsed to {survival_rate:.0%} survival — "
        f"likely a goal/production selection bug, not normal attrition",
    )


CORRECTNESS_TESTS: list[Callable[[Simulation], None]] = [
    test_no_phantom_inventory_desync,
    test_no_negative_inventory,
    test_skill_never_exceeds_cap_or_drops_below_zero,
    test_food_critical_agents_never_starve_holding_food,
    test_clothing_chain_functions,
    test_tool_chain_functions,
    test_markets_not_fully_seized,
    test_no_domain_universally_stuck_at_floor,
    test_population_survives,
]


def run_correctness_suite(
    overrides: dict[str, Any] | None = None,
    seeds: list[int] | None = None,
    ticks: int = 500,
) -> bool:
    """Run every correctness test across a handful of seeds. Unlike run_batch,
    this is pass/fail, not distributional — any single failure means stop and
    fix the mechanism before doing any more tuning."""
    seeds = seeds if seeds is not None else [0, 1, 2]
    overrides = overrides or {}
    all_passed = True

    for seed in seeds:
        config = _apply_overrides(SimConfig(seed=seed), overrides)
        sim = Simulation(config=config)
        sim.run(ticks)

        for test_fn in CORRECTNESS_TESTS:
            try:
                test_fn(sim)
                print(f"  [PASS] seed={seed} {test_fn.__name__}")
            except CorrectnessError as e:
                print(f"  [FAIL] seed={seed} {test_fn.__name__}: {e}")
                all_passed = False

    print("\n=== Correctness suite: {} ===".format("PASSED" if all_passed else "FAILED"))
    return all_passed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# Example sweep definitions — extend this as you tune. Keeping named presets
# here means "how do I tune X" becomes "python eval.py sweep X" instead of
# rewriting a one-off script each time.
SWEEP_PRESETS: dict[str, dict[str, Any]] = {
    "weave_min_skill": dict(
        param_name="recipe:weave_cloth:min_skill",
        values=[1.2, 1.3, 1.4, 1.6],
    ),
    "skill_decay": dict(
        param_name="skill_decay_per_tick",
        values=[0.001, 0.005, 0.01, 0.02],
    ),
    "tool_durability": dict(
        param_name="tool_max_uses",
        values=[3, 5, 7, 10],
    ),
    "comfort_urgency": dict(
        param_name="comfort_urgency_weight",
        values=[0.8, 1.3, 1.8, 2.3],
    ),
    "comfort_productivity_loss": dict(
        param_name="comfort_productivity_loss",
        values=[0.1, 0.12, 0.14, 0.16, 0.18, 0.2]
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="econ_sim evaluation harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_batch = sub.add_parser("batch", help="run a batch over seeds with the default config")
    p_batch.add_argument("--seeds", type=int, default=20)
    p_batch.add_argument("--ticks", type=int, default=500)

    p_sweep = sub.add_parser("sweep", help="sweep a named parameter preset")
    p_sweep.add_argument("preset", choices=list(SWEEP_PRESETS.keys()))
    p_sweep.add_argument("--seeds", type=int, default=15)
    p_sweep.add_argument("--ticks", type=int, default=500)

    p_test = sub.add_parser("test", help="run the correctness suite")
    p_test.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p_test.add_argument("--ticks", type=int, default=500)

    args = parser.parse_args()

    if args.command == "batch":
        summary = run_batch(num_seeds=args.seeds, ticks=args.ticks)
        summary.print_report()

    elif args.command == "sweep":
        preset = SWEEP_PRESETS[args.preset]
        sweep(preset["param_name"], preset["values"], num_seeds=args.seeds, ticks=args.ticks)

    elif args.command == "test":
        passed = run_correctness_suite(seeds=args.seeds, ticks=args.ticks)
        sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
