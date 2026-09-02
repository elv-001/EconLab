from contextlib import contextmanager
from dataclasses import replace
from econ_sim.config import DEFAULT_CONFIG, RECIPE_BY_NAME
from econ_sim.run import run_once
from econ_sim.sim_types import Good
import itertools
import json

def evaluate_run(summary: dict) -> dict:
    survival_rate = summary["agents_alive"] / 80
    specialization = summary["specialization_end"]
    total_active = sum(specialization.values())
    # crude diversity measure: how far from a single recipe dominating
    max_share = max(specialization.values()) / total_active if total_active else 1.0
    diversity_score = 1.0 - max_share  # higher = more spread out

    price_bounds_ok = all(
        0.3 <= p / base for p, base in zip(
            summary["prices_end"].values(), [3.0, 2.0, 4.0]  # food, wood, tools base
        ) for p in [p]
    ) and all(
        p / base <= 3.0 for p, base in zip(summary["prices_end"].values(), [3.0, 2.0, 4.0])
    )

    trade_health = summary["total_trades"] / summary["ticks_run"]  # trades/tick
    gini_ok = 0.15 <= summary["gini_end"] <= 0.45  # your judged "plausible" band

    return {
        "survival_rate": round(survival_rate, 3),
        "diversity_score": round(diversity_score, 3),
        "price_bounds_ok": price_bounds_ok,
        "trades_per_tick": round(trade_health, 3),
        "gini_end": summary["gini_end"],
        "gini_ok": gini_ok,
        "food_per_capita": summary["food_per_capita"],
        "passes_all": survival_rate > 0.85 and diversity_score > 0.3 and price_bounds_ok and trade_health > 0.5 and gini_ok,
    }

def run_sweep():
    param_grid = {
        "tool_max_uses": [3, 5, 7, 10],
        "food_shelf_life_ticks": [6, 8, 10],
        "farming_food_output": [5,6,7,8,9],
    }
    seeds = [20, 42, 7]

    results = []
    for tmu, shelf, hf_out in itertools.product(*param_grid.values()):
        scored_runs = []
        for seed in seeds:
            with with_modified_recipes(hf_out):
                config = replace(
                    DEFAULT_CONFIG,
                    seed=seed,
                    tool_max_uses=tmu,
                    food_shelf_life_ticks=shelf,
                    num_ticks=600,
                )
                summary = run_once(config)
            scored_runs.append(evaluate_run(summary))

        avg_result = {
            "tool_max_uses": tmu,
            "food_shelf_life_ticks": shelf,
            "farming_output": hf_out,
            "avg_survival": round(sum(r["survival_rate"] for r in scored_runs) / len(scored_runs), 2),
            "avg_gini": round(sum(r["gini_end"] for r in scored_runs) / len(scored_runs),2),
            "avg_trades_per_tick": round(sum(r["trades_per_tick"] for r in scored_runs) / len(scored_runs),2),
            "all_pass": all(r["passes_all"] for r in scored_runs),
        }
        results.append(avg_result)
        print(f"tmu={tmu} shelf={shelf} -> {avg_result}")

    results.sort(key=lambda r: (-r["all_pass"], abs(r["avg_gini"] - 0.3)))
    print("\nTop configs:")
    for r in results[:5]:
        print(json.dumps(r, indent=2))

@contextmanager
def with_modified_recipes(new_output: int):
    recipe = RECIPE_BY_NAME["farm"]
    original = recipe.outputs[Good.FOOD]
    recipe.outputs[Good.FOOD] = new_output
    try:
        yield
    finally:
        recipe.outputs[Good.FOOD] = original

if __name__ == "__main__":
    run_sweep()
