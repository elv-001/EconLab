# EconLab

Agent-based economic simulator where specialisation, trade, inequality, and prices emerge from a value-function decision model. Agents have a number of actions to choose from, and can buy, sell or produce based on the value of these actions. 

**Key Findings:**

- Enabling trade cut the number of agnts foraging (a basic survival mechanism) from 64% to 2%, raising food per capita from 8 to 13
- Specialisations emerge: 56% of the population are farmers, 32.5% are fiber gatherers, 6% are lumberjacks, with the rest being carpenters or foragers (using the seed & ticks in quick start)
- High volume of trades (38,496), meaning agents exchange goods to aid in their production or gain money to purchase goods
- Interesting wealth distribution: top 10% controls 27.9% of wealth, bottom 50% control 28.1%


## Quick start

```bash
pip install -e
sim
```

Run an eval:

```bash
python3 src/eval.py batch
```

Play around with variables in config.py to see how different variables impact the economy.

## Design

The simulator keeps track of time using ticks. Each tick runs five phases:

1. **Valuation & Decision** - agents identify top production and trade actions to execute based on valuation
2. **Offer/Demand** — surplus → sell offers; shortage → buy bids at reservation prices
3. **Matching & Trade** — centralized order book clears compatible orders
4. **Production** — agents execute a recipe based on action valuations
5. **Housekeeping** — aggregate recording, consumption of goods

Each good and recipe was valued based on:
- direct need/survival
- opportunity cost of inputs
- marginal value
- productivity & skill
- inventory

## Development Process

The initial codebase design was scaffolded with Cursor. Whilst incredibly efficient and helpful, I had to rewrite the agent decision code as it was too myopic and focused on immediate benefits. Specialization could not occur with that decision model. The improved method for v0 was with goal-backtracking, where the agent picks a goal (a target good) and identifies the steps necessary to achieve it. This achieved some specialisation between farmers and foragers.

As I expanded the features to include productivity, skills, inventory lots, and introduced new recipes, this algorithm proved to be insufficient. Adding new necessities like shelter and clothing often led to mass extinction events. This expanded the existing goal-decision logic, which ended up hard-wiring specialisations instead of allowing them to emerge based on the value of independent actions.

This led to v1. New valuation algorithm was implemented, with new metrics to track (wealth distribution) and a different market pricing algorithm to better reflect supply & demand. These helped create a stable simulation that achieved my initial goal (demonstrating macroeconomic behaviour).

## Limitations
- Comparative advantage is slightly weak (Experiment 4) and can't be easily amplified with config variable changes
- Zero jitter (equal starting point for all agents) somehow results in higher inequality and a higher death rate at the end (Experiment 2)
- Runtime still slow (to me), could probably parallelize a lot of the computations


## Full Documentation

[Model spec](docs/model_spec.md)

[Development log](docs/development_log.md)

[Experiments](docs/experiments.md)
