"""
Wealth distribution metrics beyond the Gini coefficient.

Drop this alongside `metrics.py` (e.g. as `econ_sim/wealth_metrics.py`) and
call `wealth_distribution_report(values)` with a list of per-agent wealth
(or money) values — the same kind of list you already build for
`gini_coefficient()` in metrics.py.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field


@dataclass
class WealthDistributionReport:
    n: int = 0
    total_wealth: float = 0.0
    mean: float = 0.0
    median: float = 0.0
    stdev: float = 0.0
    coefficient_of_variation: float = 0.0

    # Percentiles
    p10: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p90: float = 0.0
    p99: float = 0.0

    # Percentile ratios -- how stretched the distribution is
    p90_p10_ratio: float = 0.0
    p90_p50_ratio: float = 0.0
    p50_p10_ratio: float = 0.0

    # Tail / share metrics
    top_1pct_share: float = 0.0
    top_10pct_share: float = 0.0
    bottom_10pct_share: float = 0.0
    bottom_50pct_share: float = 0.0
    palma_ratio: float = 0.0  # top 10% share / bottom 40% share

    # Inequality indices -- alternatives to Gini, each sensitive to a
    # different part of the distribution
    theil_index: float = 0.0       # sensitive to top-end dispersion
    atkinson_0_5: float = 0.0      # moderate aversion to inequality
    atkinson_1_0: float = 0.0      # heavy aversion -- weights the bottom

    # Shape of the distribution
    skewness: float = 0.0
    excess_kurtosis: float = 0.0

    # Relative-poverty style metrics (fraction below X% of the median)
    share_below_50pct_median: float = 0.0
    share_below_60pct_median: float = 0.0

    # Wealth share held by each decile, poorest (index 0) to richest (index 9)
    decile_shares: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Plain, JSON-serializable dict -- use this when embedding the report
        inside another dict (e.g. SimReport.summary()) or dumping to a file."""
        return asdict(self)

    def format_report(self) -> str:
        """Compact table-style summary matching other simulation reports."""

        def pct(x: float) -> str:
            return "inf" if math.isinf(x) else f"{x * 100:.1f}%"

        def ratio(x: float) -> str:
            return "inf" if math.isinf(x) else f"{x:.2f}x"

        lines = [
            "Wealth Distribution",
            f"  {'n':>4} {'total':>12} {'mean':>10} {'median':>10} "
            f"{'stdev':>10} {'CV':>7}",
            f"  {self.n:>4} {self.total_wealth:>12,.2f} {self.mean:>10,.2f} "
            f"{self.median:>10,.2f} {self.stdev:>10,.2f} "
            f"{self.coefficient_of_variation:>7.2f}",
            "",
            f"  {'percentile':<12} {'P10':>9} {'P25':>9} {'P50':>9} "
            f"{'P75':>9} {'P90':>9} {'P99':>9}",
            f"  {'wealth':<12} {self.p10:>9,.2f} {self.p25:>9,.2f} "
            f"{self.p50:>9,.2f} {self.p75:>9,.2f} "
            f"{self.p90:>9,.2f} {self.p99:>9,.2f}",
            "",
            f"  {'spread':<12} {'P90/P10':>10} {'P90/P50':>10} {'P50/P10':>10}",
            f"  {'ratio':<12} {ratio(self.p90_p10_ratio):>10} "
            f"{ratio(self.p90_p50_ratio):>10} {ratio(self.p50_p10_ratio):>10}",
            "",
            f"  {'wealth share':<16} {'top 1%':>10} {'top 10%':>10} "
            f"{'bottom 10%':>12} {'bottom 50%':>12} {'Palma':>10}",
            f"  {'share':<16} {pct(self.top_1pct_share):>10} "
            f"{pct(self.top_10pct_share):>10} "
            f"{pct(self.bottom_10pct_share):>12} "
            f"{pct(self.bottom_50pct_share):>12} "
            f"{ratio(self.palma_ratio):>10}",
            "",
            f"  {'inequality':<16} {'Theil':>10} {'Atkinson .5':>12} "
            f"{'Atkinson 1.0':>12}",
            f"  {'index':<16} {self.theil_index:>10.4f} "
            f"{self.atkinson_0_5:>12.4f} {self.atkinson_1_0:>12.4f}",
            "",
            f"  {'shape':<16} {'skewness':>10} {'excess kurtosis':>18}",
            f"  {'value':<16} {self.skewness:>10.2f} "
            f"{self.excess_kurtosis:>18.2f}",
            "",
            f"  {'poverty threshold':<22} {'share':>10}",
            f"  {'< 50% median':<22} {pct(self.share_below_50pct_median):>10}",
            f"  {'< 60% median':<22} {pct(self.share_below_60pct_median):>10}",
            "",
            "  Decile wealth shares",
            "  " + " ".join(
                f"D{i + 1}={pct(share):>6}"
                for i, share in enumerate(self.decile_shares)
            ),
        ]

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format_report()


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolation percentile (same convention numpy defaults to)."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (pct / 100) * (n - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _share_of(sorted_vals: list[float], total: float, start_pct: float, end_pct: float) -> float:
    """Share of total wealth held by the population slice between
    start_pct and end_pct (0-100, poorest to richest)."""
    n = len(sorted_vals)
    if n == 0 or total == 0:
        return 0.0
    lo = max(0, min(int(round(n * start_pct / 100)), n))
    hi = max(lo, min(int(round(n * end_pct / 100)), n))
    return sum(sorted_vals[lo:hi]) / total


def _theil_index(values: list[float], mean: float) -> float:
    """Theil's T statistic. Requires strictly positive values -- agents with
    zero or negative wealth are simply skipped (rare in practice, but keeps
    the log defined)."""
    n = len(values)
    if n == 0 or mean <= 0:
        return 0.0
    total = 0.0
    for v in values:
        if v > 0:
            ratio = v / mean
            total += ratio * math.log(ratio)
    return total / n


def _atkinson_index(values: list[float], mean: float, epsilon: float) -> float:
    """Atkinson inequality index for inequality-aversion parameter epsilon.
    0 = insensitive to inequality (index -> 0); higher epsilon weights the
    bottom of the distribution more heavily. Requires strictly positive
    values, so non-positive wealth entries are excluded."""
    positives = [v for v in values if v > 0]
    n = len(positives)
    if n == 0 or mean <= 0:
        return 0.0
    if abs(epsilon - 1.0) < 1e-9:
        log_sum = sum(math.log(v) for v in positives)
        geo_mean = math.exp(log_sum / n)
        return 1 - (geo_mean / mean)
    exponent = 1 - epsilon
    avg = sum(v ** exponent for v in positives) / n
    ede = avg ** (1 / exponent)  # equally-distributed-equivalent wealth
    return 1 - (ede / mean)


def _moments(values: list[float], mean: float, stdev: float) -> tuple[float, float]:
    """Sample skewness and excess kurtosis (0, 0 for degenerate inputs)."""
    n = len(values)
    if n < 3 or stdev == 0:
        return 0.0, 0.0
    m3 = sum((v - mean) ** 3 for v in values) / n
    skew = m3 / (stdev ** 3)
    kurt = 0.0
    if n >= 4:
        m4 = sum((v - mean) ** 4 for v in values) / n
        kurt = (m4 / (stdev ** 4)) - 3  # 0 ~ normal-like tails
    return skew, kurt


def wealth_distribution_report(values: list[float]) -> WealthDistributionReport:
    """
    Compute a broad set of wealth-distribution statistics for a population,
    as a complement to a single Gini number.

    Parameters
    ----------
    values : list[float]
        Per-agent wealth (or money), e.g. [a.total_wealth(prices) for a in agents].
        Zero values are fine everywhere; negative values are excluded from
        Theil/Atkinson (which require strictly positive wealth) but still
        count in percentiles, shares, and moments.

    Returns
    -------
    WealthDistributionReport
    """
    if not values:
        return WealthDistributionReport()

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    mean = total / n
    median = statistics.median(sorted_vals)
    stdev = statistics.pstdev(sorted_vals)
    cv = stdev / mean if mean != 0 else 0.0

    p10 = _percentile(sorted_vals, 10)
    p25 = _percentile(sorted_vals, 25)
    p50 = _percentile(sorted_vals, 50)
    p75 = _percentile(sorted_vals, 75)
    p90 = _percentile(sorted_vals, 90)
    p99 = _percentile(sorted_vals, 99)

    p90_p10 = p90 / p10 if p10 > 0 else float("inf")
    p90_p50 = p90 / p50 if p50 > 0 else float("inf")
    p50_p10 = p50 / p10 if p10 > 0 else float("inf")

    top_1 = _share_of(sorted_vals, total, 99, 100)
    top_10 = _share_of(sorted_vals, total, 90, 100)
    bottom_10 = _share_of(sorted_vals, total, 0, 10)
    bottom_40 = _share_of(sorted_vals, total, 0, 40)
    bottom_50 = _share_of(sorted_vals, total, 0, 50)
    palma = top_10 / bottom_40 if bottom_40 > 0 else float("inf")

    theil = _theil_index(sorted_vals, mean)
    atkinson_05 = _atkinson_index(sorted_vals, mean, 0.5)
    atkinson_10 = _atkinson_index(sorted_vals, mean, 1.0)

    skew, kurt = _moments(sorted_vals, mean, stdev)

    below_50pct_median = sum(1 for v in sorted_vals if v < 0.5 * median) / n
    below_60pct_median = sum(1 for v in sorted_vals if v < 0.6 * median) / n

    decile_shares = [
        round(_share_of(sorted_vals, total, i * 10, (i + 1) * 10), 4)
        for i in range(10)
    ]

    def _safe_round(x: float, digits: int) -> float:
        return x if x in (float("inf"), float("-inf")) else round(x, digits)

    return WealthDistributionReport(
        n=n,
        total_wealth=round(total, 2),
        mean=round(mean, 2),
        median=round(median, 2),
        stdev=round(stdev, 2),
        coefficient_of_variation=round(cv, 4),
        p10=round(p10, 2),
        p25=round(p25, 2),
        p50=round(p50, 2),
        p75=round(p75, 2),
        p90=round(p90, 2),
        p99=round(p99, 2),
        p90_p10_ratio=_safe_round(p90_p10, 3),
        p90_p50_ratio=_safe_round(p90_p50, 3),
        p50_p10_ratio=_safe_round(p50_p10, 3),
        top_1pct_share=round(top_1, 4),
        top_10pct_share=round(top_10, 4),
        bottom_10pct_share=round(bottom_10, 4),
        bottom_50pct_share=round(bottom_50, 4),
        palma_ratio=_safe_round(palma, 3),
        theil_index=round(theil, 4),
        atkinson_0_5=round(atkinson_05, 4),
        atkinson_1_0=round(atkinson_10, 4),
        skewness=round(skew, 4),
        excess_kurtosis=round(kurt, 4),
        share_below_50pct_median=round(below_50pct_median, 4),
        share_below_60pct_median=round(below_60pct_median, 4),
        decile_shares=decile_shares,
    )
