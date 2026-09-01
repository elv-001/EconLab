# EconForge

Economic simulator where I attempt to make specialization, trade, prices and wealth patterns emerge from agent behaviour. Agents are governed by explicit rules.

A tribal economy is simulated in v0. Basic goods can be produced (food, tools, wood). Agents start with random levels of affinities. They improve at a skill (carpentry, farming...) by executing the action that requires said skill. 

Each agent starts with a configured amount of goods and cash. Each tick, the agent first consumes an amount of food. It then determines its action depending on its inventory’s surplus and shortages. It then bids for certain goods, or attempts to sell them, based on a simple utility function. The goal for each agent is to achieve the optimal number of goods in its inventory (‘optimal’ as defined in the config file). 

The market operates by matching the highest bidder to the lowest seller. The selling price is the average of the two prices. It’s a rather primitive way of doing it, but it works for now. The market price is then updated per tick based on a price smoothing algorithm (Exponential Moving Average) with a low alpha as the default config.
Each simulation run outputs the number of specializations for each action, by counting the action which the agent executes the most. 


The goal is to incentivize agents to specialize based on their skills & affinities so there's a good distribution at the end, with a decent survival rate (80%+).

## Quick start

```bash
python -m econ_sim.run --seed 42 --agents 80 --ticks 400
```

Write logs and time series to disk:

```bash
python -m econ_sim.run --ticks 400 --output-dir ./output
```

Inspect one agent's full history:

```bash
python -m econ_sim.run --ticks 100 --agent-history 7
```