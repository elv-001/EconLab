# EconForge

Economic simulator where I attempt to make specialization, trade, prices and wealth patterns emerge from rule-based agents.

Experiments were conducted (see experiments.md) to demonstrate that the model can replicate certain macroeconomic behaviour.

## Quick start

```bash
python -m econ_sim.run --seed 42 --agents 80 --ticks 400
```

Write logs and time series to disk:

```bash
python -m econ_sim.run --ticks 400 --output-dir ./output
```
