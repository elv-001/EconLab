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

### Recipes

| Recipe       | Inputs             | Outputs    | Domain       | Min Skill |
| ------------ | ------------------ | ---------- | ------------ | --------- |
| forage       | None               | Food: 3    | GATHERING    | 1.0       |
| chop_wood    | None               | Wood: 2    | LUMBERJACK   | 1.0       |
| craft_tools  | Wood: 3            | Tools: 1   | CARPENTRY    | 1.3       |
| farm         | Tools: 1           | Food: 6    | FARMING      | 1.3       |
| shelter      | Wood: 3, Tools: 1  | Shelter: 1 | CONSTRUCTION | 1.0       |
| gather_fiber | None               | Fiber: 2   | HARVESTING   | 1.0       |
| weave_cloth  | Fiber: 3, Tools: 1 | Clothes: 6 | WEAVING      | 1.4       |

### Agents

Agent State:
- Inventory Lots: Each lot shows the tick it was created, number of uses left, quantity of good
- Skills: Contains the skill value for each Skill Domain (Gathering, Farming, Weaving...)
- Money: Amount of cash on hand
- Production & Trade Actions: The chosen actions for each tick, to be executed

**Value Function:**
Agents decide how to act based on the value function:
- First, the set of possible actions is generated
- The action is then valued depending on the intent: BUY, SELL, or PRODUCE

If the intent is to produce:
- Expected output is calculated with productivity multiplier
- Value of this output is found via marginal value function
- Applies carrying cost penalty to disincentivize overproduction
- Net Production Value found after subtracting input costs
- Apply behavioural modifications (profit motivation, self reliance, survival)

If the intent is to buy:
- Calculate marginal value of purchasing good
- Finds net value

If intent is to sell:
- Calculates expected sales proceed with expected selling rate
- Expected selling rate is updated each tick so agent has an idea of supply & demand
- Net value found after subtracting opportunity cost of selling & idle inventory cost


After valuing all actions, the algorithm takes the top n actions and assigns them a weight based on scoring. Random generator is then used to pick the executed production action. This prevents greedy selection from finding a locally optimal result (like foraging) and letting that dominate the solution, allowing agents to experiment with different actions.

Trade actions are also ranked and the top n are put onto the market.

### Order Generation & Market Clearing

The order that agents are in to generate orders is randomised. This is because keeping the order constant was prioritizing certain agents, allowing them to get first-move advantage over others. 

The orders are then passed to the market function (market.clear()), which matches the highest bids to the lowest asks. If bid.price < ask.price, the trade does not execute. Trade quantity is determined by the minimum amount (min(bqty, aqty)) so orders can be partially filled.

The trade price is the average of bid & asking price (when use_midpoint_pricing is True). Unfilled quantity is added to unmatched_bids and unmatched_asks to update the market price.

The market price is determined by VWAP of completed trades. VWAP is calculating the average price, but larger trades have more influence. If no trades were executed, the bid/ask imbalance is calculated to find the price.

Unfilled buy orders push the price up, unfilled sell orders push the price down. This ratio is asymmetric because the primary issue I faced was overproduction of goods. Finally, price is clamped based on config and smoothed with an EMA (exponential moving average) to reduce volatility.

### Instrumentation

- `agent_history(id)` returns full history for any agent
- Per-tick snapshots: output, prices, trade volume, Gini, specialization
- Seeded RNG for full reproducibility

### Configuration

All coefficients live in `econ_sim/config.py` (`SimConfig`). Tune targets, base prices, skill gains, and urgency weights without touching simulation logic.
