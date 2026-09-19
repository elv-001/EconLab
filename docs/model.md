Model Design

### Goods

| Good    | Role                          |
|---------|-------------------------------|
| Food    | Basic consumption / survival  |
| Wood    | Raw material for tools        |
| Fiber   | Raw material for clothes      |
| Tools   | Intermediate capital good     |
| Clothes | Productivity Enhancer         |
| Shelter | Productivity Enhancer         |

### Tick loop

Each tick runs four phases:

1. **Production** — agents pick a feasible recipe by considering immediate & long term interests
2. **Offer/Demand** — surplus → sell offers; shortage → buy bids at reservation prices
3. **Matching & Trade** — centralized order book clears compatible orders
4. **Housekeeping** — aggregate recording, consumption of goods

### Agents

Each agent has inventory, money, inventory targets, and per-recipe skills (learning-by-doing). They also have certain personality parameters (self_reliance) which determine goal-stickiness.

### Instrumentation

- `agent_history(id)` returns full history for any agent
- Per-tick snapshots: output, prices, trade volume, Gini, specialization
- Seeded RNG for full reproducibility

### Configuration

All coefficients live in `econ_sim/config.py` (`SimConfig`). Tune targets, base prices, skill gains, and urgency weights without touching simulation logic.
