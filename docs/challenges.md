# GOALS

The initial goals were to replicate specializations, trading and wealth distribution based on skill.

# CHALLENGES

The initial code was generated with Cursor. That was insanely efficient, but the painful system debugging came later. Below, I will categorise and explain the key problems I faced.

## Short Term Planning

The original agent action planning function was incredibly short-sighted. It looked at each recipe and calculated the immediate benefit. Unsurprisingly, that made every agent specialize in foraging, because it was the most direct way to food. There was no incentive to go for more efficient methods like farming. 

The idea was to make the agent act on all possible actions for a few ticks (using recursion), then evaluate all the hypothetical inventories for the best one and choose that action path. However, the recipe at that time required 3 wood for one tool, then one farming action to yield the food. That would require 5 ticks for the investment in chopping wood to manifest. Applying this recursive technique at a depth of 5, for 80+ agents (maybe hundreds in the future if I scale), for hundreds of ticks, is incredibly inefficient.

A better way would've been to select the top 2 or 3 inventory states at each hypothetical tick. This would indeed increase the efficiency. However, if some recipes require long term investment (say 10 ticks for a better tool in the future), the algorithm would have no way of knowing the potential upside of that tool.

The way I ended up doing it was by backtracking from the goal. The agent would pick a goal depending shortages and find all the recipes that output its target good. It would then trace the recipe's dependencies and compare based on efficiency. By creating the entire recipe dependency graph at initialization, it's O(1) to lookup the dependency for every agent's goal, making this way more efficient than previous solutions.

## Low Trading Volume

Solving this still didn't solve the trading problem though. Trade volume was extremely low as each agent was focused on its own goal. I had to implement a 'make or buy' decision which factored in skill levels. It was interesting when I had to balance between allow beginners to develop their skill, but setting the threshold high enough to promote specialization. Introducing a trait called 'self-reliance' created some differences in behaviour, making some agents more focused on creating goods rather than trading for them.

## Market Pricing Issues

Even when trade volume increased, the market prices of goods always plumetted to the min price level set. I spent way too much time chasing config issues that I thought were making goods too abundant. As it turns out, the market pricing algorithm used last tick's price as the reference point, meaning the price would either compound up or down. Even if a good was slightly abundant each tick, that curve was exponential. Anchoring the price to a baseline price smoothed things out.

## Wrong People Getting Rich

Once all that stabilised, I turned to specializations and measuring wealth. Strangely, the laziest action (foraging) was creating the wealthiest people in this simulator. That wasn't the desired outcome. The agents who invested in long term solutions like farming and carpentry were supposed to get rich. Foraging was just a zero-cost temporary survival solution.

A lot of digging in the logs revealed that foraging's zero-cost characteristic enabled foragers to infinitely compound wealth at zero cost. Farmers and Carpenters, who had to trade or craft, spent cash. As there was no way to redistribute wealth, this ended up with foragers being the richest of them all.

There were a few core reasons. Firstly, the market pricing algorithm didn't care about labour costs. It merely matched via bidding & asking price. Labour was not valued. Tools were also treated as consumables after one run. The skill difference was also insufficient to distinguish specialists from normal people.

Initially, I reduced the foraging output to be net neutral (2 food per yield, vs 2 food consumed per tick). That incentivised farming, yes. It also killed 50% of the population.

To fix this, I made the lifespan of tools 3-5 ticks instead (and reverted the foraging output change). I also reduced skill accummulation gains and raised the skill ceiling. Combining all of this, the distribution of agents above the median amount of wealth went from being dominated by foragers to being 60|40 farmers/carpenters to foragers.

## v0 Conclusion

This baseline version replicates specializations. The endstate at 600 ticks shows 50% of agents specializing in farming, 33% in foraging and a few in the wood trade. 

Prices move depending on demand & supply. For example, as tools became consumables over a few ticks (instead of being immediately consumed), the demand for wood plumetted and price of wood dropped from 3.6 (initial runs) to 2 at the end. Tools became more valuable, so its price raised from 4 to 4.6.

Gini Coefficient shows how wealth is redistributed based on initial affinitties and skills, and how the economy becomes more unequal (0.15 -> 0.31). 

There are obvious links between config variables and economic outcomes. Increasing the durability of the tool reduces trade and increases the number of agents who specialize in farming. It also skews the foraging/farming ratio.

There also aren't any mass deaths (85%+ survival rate). It is a stable economy.

Some issues still remain. Food per capita is 70+, so there's basically no scarcity and need for trade anymore after tick 50. Foraging is still too efficient. I also want to run actual experiments by mixing up agent decisioning (depending on personality), or enabling/disabling trade to see if it makes things more efficient.

But this is a starting point.

a precondition to coding this 'emerging specialisation' is that agents must understand a hierarchy fo goals
aka food is more important than clothes. the question is how I code this in, and how does a difference in goal lead to difference in choices

i sohuld be hard coding the structure of goals into the recipe, but how the agents behave should not be due to direct explicit manipulation

pricing algo defies laws of demand & supply. bids > asks should cause demand to go up, but price of clothes collapsed. thus no incentivise production
after limiting foraging with yield, it's now farming dominant vs clothing chain dominant
this was because tool max use was too small, so the limited num of tools locked agents into one given path
it seems like when weaving and farming compete for tools the price skyrockets so they cant afford
i suspect there's an edge, where if it passes one side dominates, else the other
amplifying both skill & affinity seems to have helped

but now no bids being made cuz pricing cant understand the huge supply
Producing gives positive utility; owning inventory is almost free.