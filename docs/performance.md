# Performance Optimizations

As the simulation metrics became stable, I then focused on runtime performance before running experiments. The current issue is that simulations take way too long to run.

## Initial Benchmark
The runtime for:
```
python3 -m econ_sim.run --seed 26 --agents 80 --ticks 500
```
is ~14s. If I increase the tick count to 1500, it takes ~47 seconds.
On average each step takes ~30ms. 

## Profiling and Optimizations

Running this:
```
python -m cProfile -o profile.out -m econ_sim.run --agents 80 --ticks 300 --quiet
```
I outputted the results using performance.py. The top functions costing the most time were 'compute_shadow_values', '_productivity', and '_value_produce'.

```
ncalls  tottime  percall  cumtime  percall filename:lineno(function)
    65454    2.703    0.000   10.700    0.000 /econ_sim/agent.py:475(compute_shadow_values)
 15734116    2.207    0.000    2.932    0.000 {method 'get' of 'dict' objects}
  1588880    2.089    0.000    5.262    0.000 econ_sim/agent.py:760(_productivity)
8447708/8278389    1.424    0.000    2.964    0.000 {built-in method builtins.max}
   191102    1.279    0.000    6.715    0.000 econ_sim/agent.py:223(_value_produce)
```

Next I used lineprofiler on those 3 functions.
```
export PYTHONPATH=.                                                               
kernprof -l -v econ_sim/run.py --agents 80 --ticks 100 --quiet
```

compute_shadow_value (Total time: 6.76905 s) spends 53.6% of the time on this single line. 
   489    393876    3628205.0      9.2     53.6                  prod = self._productivity(recipe)

_productivity (Total time: 2.76607 s) spends 28.4% on this line:
   781    396796     786243.0      2.0     28.4          if self.config.clothing_enabled and self._shortage(Good.CLOTHES) > 0:

value_produce (Total time: 3.71089 s) spends 18% of its time on:
   239     62529     669362.0     10.7     18.0              out_val += self._marginal_value(good, expected, tick, market_prices, shadow)
    277     64139     567380.0      8.8     15.3              current_domain_streak = Counter(RECIPE_BY_NAME[r].domain for r in recent)

These functions are being called way too many times. Oh, this was because I actually called shadow_values twice in the planning process. Literally removing that changed total time to:
```
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
11207836    1.478    0.000    1.857    0.000 {method 'get' of 'dict' objects}
   191102    1.248    0.000    6.570    0.000 econ_sim/agent.py:223(_value_produce)
    27735    1.197    0.000    4.674    0.000 econ_sim/agent.py:475(compute_shadow_values)
```

This one change shrunk runtime to ~10.4s (compared to initial benchmark). A 30% reduction in runtime! Speedrup formula is old/new runtime, so it's a 39.4% speedup. 

The next line to speedup is shortage(Good.CLOTHES). Calculating that once and caching it will help. Lineprofiler shows % of total time dropped from 28.4% to 5.6%.  Also cached domain streak beacuse it was creating a new Counter object every call for _value_produce(). These two changes dropped runtime to 9.2s.

Now, ._productivity(recipe) in shadow values is taking up 50% of the time:
```
   489    227976    1771321.0      7.8     49.9                  prod = self._productivity(recipe)
```
That's because it is being called a ridiculous 227976 times in 100 ticks, because of:
```
for _ in range(iterations):
            updated = dict(values)
            for recipe in RECIPES:
                prod = self._productivity(recipe)
```
That's O(I x R) complexity, for 80 agents, across 100 ticks. I cached the productivity results at the start in plan() instead:
```
   497    165522      47417.0      0.3      3.5                  prod = self._productivity_values[recipe.name]
```

After a few more optimizations, the runtime dropped to 8.25s. 1.7x speedup.

# Ideas
I could probably parallelize a lot of the work with numpy (productivity calculations, prices etc).
