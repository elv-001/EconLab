from __future__ import annotations

from dataclasses import dataclass

from econ_sim.sim_types import Good, Order, TradeRecord


@dataclass
class MatchResult:
    trades: list[TradeRecord]
    unmatched_bids: list[Order]
    unmatched_asks: list[Order]


class Market:
    """Simple centralized order book with price-time priority matching."""

    def __init__(self) -> None:
        self._order_seq = 0

    def next_order_id(self) -> int:
        self._order_seq += 1
        return self._order_seq

    def clear(
        self,
        tick: int,
        bids: list[Order],
        asks: list[Order],
        use_midpoint: bool = True,
    ) -> MatchResult:
        """
        Match compatible orders. Bids sorted high-to-low, asks low-to-high.
        Trade price is the ask price (seller's offer).
        """
        bids_sorted = sorted(bids, key=lambda o: (-o.price, o.order_id))
        asks_sorted = sorted(asks, key=lambda o: (o.price, o.order_id))

        trades: list[TradeRecord] = []
        bid_remaining: dict[int, int] = {b.order_id: b.quantity for b in bids_sorted}
        ask_remaining: dict[int, int] = {a.order_id: a.quantity for a in asks_sorted}

        for bid in bids_sorted:
            bqty = bid_remaining.get(bid.order_id, 0)
            if bqty <= 0:
                continue

            for ask in asks_sorted:
                if ask.good != bid.good:
                    continue
                aqty = ask_remaining.get(ask.order_id, 0)
                if aqty <= 0:
                    continue
                if bid.price < ask.price:
                    break

                trade_qty = min(bqty, aqty)
                trade_price = (bid.price + ask.price) / 2 if use_midpoint else ask.price

                trades.append(
                    TradeRecord(
                        tick=tick,
                        buyer_id=bid.agent_id,
                        seller_id=ask.agent_id,
                        good=ask.good,
                        quantity=trade_qty,
                        price=trade_price,
                    )
                )

                bid_remaining[bid.order_id] -= trade_qty
                ask_remaining[ask.order_id] -= trade_qty
                bqty -= trade_qty
                if bqty <= 0:
                    break

        unmatched_bids = [
            Order(
                agent_id=b.agent_id,
                good=b.good,
                quantity=bid_remaining[b.order_id],
                price=b.price,
                is_bid=True,
                order_id=b.order_id,
            )
            for b in bids_sorted
            if bid_remaining.get(b.order_id, 0) > 0
        ]
        unmatched_asks = [
            Order(
                agent_id=a.agent_id,
                good=a.good,
                quantity=ask_remaining[a.order_id],
                price=a.price,
                is_bid=False,
                order_id=a.order_id,
            )
            for a in asks_sorted
            if ask_remaining.get(a.order_id, 0) > 0
        ]

        return MatchResult(
            trades=trades,
            unmatched_bids=unmatched_bids,
            unmatched_asks=unmatched_asks,
        )

    def last_trade_prices(self, trades: list[TradeRecord]) -> dict[Good, float]:
        prices: dict[Good, float] = {}
        for t in trades:
            prices[t.good] = t.price
        return prices
