import pstats

p = pstats.Stats('profile.out')

# Sort by cumulative time: function + everything it called.
# This tells you which *call trees* dominate — good for seeing the big picture,
# e.g. "80% of runtime is inside Simulation.step -> Agent.plan"
p.sort_stats('cumulative').print_stats(20)

# Sort by total time: function's OWN time, excluding functions it calls.
# This tells you which individual function bodies are actually expensive —
# this is usually the more actionable view.
p.sort_stats('tottime').print_stats(20)
