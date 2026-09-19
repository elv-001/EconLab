# Experiments

With a stable baseline, I want to run some experiments to answer some basic economic questions and see if they replicate here.

## Protocol
1. Run baseline with fixed seeds, fixed number of ticks
2. Change the indepenent variable (in config.py)
3. Record metrics (python eval.py batch)

## Baseline

Gini 0.33 · median money 24 · 80/80 alive · farm-dominant with strong fiber sector

Specialisation: farm 45, gather_fiber 26, chop_wood 5, weave 2, tools 1, forage 1  
Prices: food 1.09, wood 0.73, tools 1.08, fiber 0.92, clothes 1.63  
Trades: 38,496 · Food/capita: 14.1 · Shelter/Clothes: 80/79

## Experiment List

### 1. Trade vs Autarky

**Conditions:**
- A: Trades enabled (baseline)
- B: Trades disabled

<br>

**Results:**

| Metric                  | Trade Off | Trade On | 
|-------------------------|-----------|----------|
| Gini                    | 0.22      | 0.31     |
| Median money            | 39.8      | 29.1     |
| Food per capita         | 8.8       | 13.0     |
| Death rate              | 0.0%      | 2.5%     |
| Forage share            | 64%       | 2%       |
| Farm share              | 19%       | 47%      |
| Fiber share             | 1.5%      | 36%      |
| Weave + Tools share     | ~1.7%     | ~2.9%    |
| Total clothes           | 12        | 455      |
| Farming skill (median)  | 1.00      | 1.55     |

<br>

**Conclusion:**

Trade strongly reduces foraging, enabling large-scale food and fiber production to produce necessity goods. These goods also become more abundant (food_per_capita increases, total clothes increases). However, inequality increases and median money falls, most likely due to the wealthier agents buying more goods.

### 2. Starting Inequality

I want to see if an equal starting point reduces inequality, and what other side effects there are.

**Conditions:**
- A: Zero Jitter
- B: Normal Jitter (Baseline)
- C: High Jitter (Double for money_jitter and endowment_jitter)

<br>

**Results:**

| Metric            | Zero   | Baseline | Double |
|-------------------|--------|----------|--------|
| Death rate        | 12.6%  | 2.5%     | 1.8%   |
| Gini              | 0.39   | 0.31     | 0.31   |
| Median money      | 22.6   | 29.1     | 30.4   |
| Farm / Fiber share| 48/32% | 47/36%   | 51/34% |

<br>

**Conclusion:**

Zero jitter significantly raised mortality and inequality. That contradicts what I thought would happen: an equal starting point provides a more equal outcome. Perhaps the personality differences are amplified.

### 3. Skill Effect

Now, I want to investigate whether a higher skill multiplier causes greater comparative advantage and helps sustain secondary specialisations.

**Conditions:**
- A: Low (0.6)
- B: Baseline (1.0)
- C: High (1.4)

<br>

**Results:**

| Metric            | Low    | Baseline | High   |
|-------------------|--------|----------|--------|
| Gini              | 0.399  | 0.311    | 0.279  |
| Median money      | 26.2   | 29.1     | 29.7   |
| Weave Share       | 1.7%   | 1.0%     | 1.1%   |
| Farming Share     | 44%    | 47%      | 52%    |


<br>

**Conclusion:**
Minor effects. Low skill effect means affinity matters more, so initial variation causes more inequality. The dominant strategy (farming) is utilised slightly more when skill matters. However, skill_effect is one of many factors that impacts action valuation. Comparative advantage matters but the true impact of skill effect is minor in this simulation. 

### 4. Farming Productivity

Currently, farming dominates the primary activity of agents. How will changing the productivity of farming impact its economy?

**Conditions:**
- A: Low (4 food)
- B: Baseline (6 food)
- C: High (8 food)

<br>

**Results:**

| Metric                  | Output=4  | Output=6  | Output=8  |
|-------------------------|-----------|-----------|-----------|
| Death rate              | 15.2%     | 2.5%      | 0.6%      |
| Agents alive (mean)     | 67.8      | 78.0      | 79.5      |
| Food per capita         | 8.72      | 12.97     | 16.13     |
| Gini                    | 0.570     | 0.311     | 0.302     |
| Median money            | 12.76     | 29.05     | 26.64     |
| Farm share              | 33.5%     | 47.1%     | 36.4%     |
| Forage share            | 12.6%     | 1.9%      | 0.0%      |
| Fiber share             | 31.0%     | 36.4%     | 39.8%     |
| Craft tools share       | 2.3%      | 1.9%      | 5.0%      |
| Total tools             | 361       | 573       | 786       |
| Total trades            | 32,416    | 37,653    | 44,228    |

**Conclusion:**

Farming, as the dominant strategy, acts as a survival mechanism. If its output is low, the death rate increases significantly. Interestingly enough, a high farm output causes the number of farmers to drop and the number of fiber gatherers to increase, as agents shift their focus to clothing, which becomes a scarcer resource.

## Conclusion

This model shows:
- Specialization and a division of labour occur once trade is allowed
- Trade raises living standards (in terms of goods) whilst increasing inequality
- Farming productivity is the key constraint that impacts the entire economy

However, it is still relatively weak in areas like:
- Comparative advantage (adjusting skill effect shows little to no impact)
- Price formation (goods become too abundant and plummet, no real differences in price)

Zero jitter increasing inequality is still something to investigate. Not sure why as of now.
