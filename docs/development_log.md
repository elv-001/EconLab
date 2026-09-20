# Development log

This simulation began as a Cursor-generated scaffold (which was nice). However, I had to rewrite the agent decision logic twice for the simulation to truly work.

## v0: Goal Backtracking

Agents scored recipes by their immediate payoff. This made their decision making short-sighted, so everyone chose foraging despite farming being more efficient in the long run. Recursive lookahead was too expensive (80 agents, 500 ticks => 40k calls, and each one becomes expensive as recipe chain grows). I considered beam pruning, but it's difficult to consider long term investments. I ended up using backward chaining from a goal over a precomputed dependency graph.

Two main bugs occurred. 

Price collapsed to the floor. I originally thought that was due to goods being abundant (thus excess supply). Deeper inspection revealed that the pricing algorithm referenced the previous tick's price, so small directional imbalances compounded. Anchoring price changes to its base price fixed this. 

Foraging was a zero-cost recipe, so agents were highly incentivised to execute that instead of specialising. Making foraging net-neutral caused mass extinction, so I reduced farming skill requirements and shortened tool life to promote secondary specialisations.

Result: 600 ticks, 85%+ survival, ~50% farming, Gini 0.15 → 0.31. But food
per capita was 70+, so there was no scarcity and little reason to trade.

## v0.5: Feature Expansion

I added productivity, personalities, shelf life and a longer recipe chain, then realised specialisation was coming from my hand-set goal scores rather than from the economy. Goals were chosen based on necessity, which was basically hand-scored.

## v1: Unified Value Function

Agents score every action once based on various factors, and random production action chosen from top n (weighted). The same failures came back, but I sought for general solutions this time instead of quick fixes. Foraging was dominant, so I added a congestion penalty to disincentivize the action if too many agents execute it. Inventory piled up, so carrying cost penalized excessive surplus. Agents also gained access to an 'expected_sell_rate' variable to better understand market demands.

Baseline: Gini 0.33, 80/80 alive, farm-dominant.

## Other Interesting Things

- Single-seed eval was unreliable, had to build an eval script with multi-seeds for better understanding of simulation
- Profiling & Caching can save a lot of runtime (1.7x speedup)
- Small changes (and errors) can compound quickly in simulations (prices, action valuation)

AI was used throughout the development process.
