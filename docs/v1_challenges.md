# V1

## Background (v0.5)
I wanted to add more functionality after v0 was done. That meant adding features like productivity, personalities, shelf life, and extending the recipe chain to introduce more complexity. Agents would choose a goal based on the scores of individual recipes (which was pre-set).

Agents selected a high-level goal, then scored recipes only in service of that goal. All market interactions were also based on the pre-chosen recipe. This meant specialisations came from explicit scores in the goal-selection logic, rather than 'emerging' organically from local economic incentives. 

## New Valuation Algorithm

The solution was to rewrite the decision-making logic so agents had all options on the table simultaneously. Each good and recipe was valued based on:
- direct need/survival
- opportunity cost of inputs
- marginal value
- productivity & skill
- inventory

The agent would then choose randomly out of the top n actions (instead of greedy). That differed to v0.5, where market interaction happened before production to buy missing inputs for the chosen recipe.

## Key Challenges
The main issues that arose after this change were market & inventory.

### 1. Price Dynamics

Early on, the price was updated based on its previous price, so in the long run it compounded exponentially in whatever direction it was drifting.
Fix: Anchor market prices of goods to its fundamental value.

### 2. Inventory Overproduction & Hoarding

Agents treated goods as having constant value even when inventories were enormous.

Fixes:
- Introducing an 'expected_sell_rate' variable for each good meant agents could 'understand' how much demand each good had.
- Carrying_cost punished actions in the value function if inventory of good was already high
- Adjusted reasonable stock targets for intermediate goods so specialists could hold onto inventory before selling

### 3. Collapsing Specializations

Foraging was dominant because it required zero inputs & cost.

Fixes:
- A congestion mechanism was implemented to cap the number of foragers (by restricting yield)
- Skill bonus was added to valuation (based on momentum) to incentivize specialists to persist
- Balanced skill requirements so there would be a healthy spread of specialisations

### 4. Performance & Measurement

Initially, I was just using a random seed to evaluate how well the algorithm did. After a while, I found out that single-seed evaluation was misleading. 

Fix:
- Built an evaluation script to test out the changes and measure them accurately (multi-seed)
- Slow harness meant optimizations: reduced runtime, 1.7x speedup with caching 

## Baseline
This was the stable v1 baseline run:

Gini 0.33 · median money 24 · 80/80 alive · farm-dominant with strong fiber sector

Specialisation: farm 45, gather_fiber 26, chop_wood 5, weave 2, tools 1, forage 1  
Prices: food 1.09, wood 0.73, tools 1.08, fiber 0.92, clothes 1.63  
Trades: 38,496 · Food/capita: 14.1 · Shelter/Clothes: 80/79

## Conclusion

The original goal of this simulator was for macroeconomic behaviour like specialisations, division of labour, trade, and wealth patterns to emerge from agent behaviour. 

In v1, all actions come from a value function. There is some guidance at the lowest level to increase the marginal value for necessity goods, but the agent still chooses randomly based on the top n actions available. Urgency influences the score but the agent chooses the goal.
