Model Design

### Goods

| Good  | Role                          |
|-------|-------------------------------|
| Food  | Basic consumption / survival  |
| Wood  | Raw material for tools        |
| Tools | Intermediate capital good     |

### Recipes

| Recipe      | Inputs    | Outputs   |
|-------------|-----------|-----------|
| Forage      | —         | 2 Food    |
| Chop Wood   | —         | 2 Wood    |
| Craft Tools | 3 Wood    | 1 Tool    |
| Hunt/Farm   | 1 Tool    | 6 Food    |

The tool-consuming Hunt/Farm recipe is the productivity jump that makes specialization worthwhile (hopefully).

### Tick loop

Each tick runs four phases:

1. **Production** — agents pick a feasible recipe by considering immediate & long term interests
2. **Offer/Demand** — surplus → sell offers; shortage → buy bids at reservation prices
3. **Matching & Trade** — centralized order book clears compatible orders
4. **Housekeeping** — memory decay, aggregate recording

### Agents

Each agent has inventory, money, inventory targets, per-recipe skills (learning-by-doing), and short counterparty memory (trust/recency). They also have certain personality parameters (self_reliance) which determine goal-stickiness.

### Instrumentation

- `agent_history(id)` returns full history for any agent
- Per-tick snapshots: output, prices, trade volume, Gini, specialization
- Seeded RNG for full reproducibility

### Configuration

All coefficients live in `econ_sim/config.py` (`SimConfig`). Tune targets, base prices, skill gains, and urgency weights without touching simulation logic.