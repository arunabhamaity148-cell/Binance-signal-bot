"""Binance USDⓈ-M wire-format models.

These map raw REST/WS JSON payloads into typed structures before
anything else in the system touches them. Parsing here is strict:
missing or malformed fields raise immediately rather than being
defaulted, per the fail-closed principle and G1 (data integrity).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import DataIntegrityError


@dataclass(frozen=True)
class RawKline:
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int
    taker_buy_base_volume: float
    is_closed: bool

    @staticmethod
    def from_rest_row(row: list) -> "RawKline":
        """Parse a single row from /fapi/v1/klines.

        Binance kline REST rows are:
        [open_time, open, high, low, close, volume, close_time,
         quote_asset_volume, number_of_trades, taker_buy_base_volume,
         taker_buy_quote_volume, ignore]
        """
        if len(row) < 11:
            raise DataIntegrityError(f"malformed kline REST row (len={len(row)}): {row}")
        try:
            return RawKline(
                open_time_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                close_time_ms=int(row[6]),
                taker_buy_base_volume=float(row[9]),
                is_closed=True,  # REST only ever returns closed klines
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse kline REST row: {row}: {exc}") from exc

    @staticmethod
    def from_ws_payload(payload: dict) -> "RawKline":
        """Parse a kline_<interval> WebSocket event's 'k' object."""
        k = payload.get("k")
        if not isinstance(k, dict):
            raise DataIntegrityError(f"malformed kline WS payload, missing 'k': {payload}")
        required = ("t", "T", "o", "h", "l", "c", "v", "V", "x")
        missing = [f for f in required if f not in k]
        if missing:
            raise DataIntegrityError(f"kline WS payload missing fields {missing}: {payload}")
        try:
            return RawKline(
                open_time_ms=int(k["t"]),
                open=float(k["o"]),
                high=float(k["h"]),
                low=float(k["l"]),
                close=float(k["c"]),
                volume=float(k["v"]),
                close_time_ms=int(k["T"]),
                taker_buy_base_volume=float(k["V"]),
                is_closed=bool(k["x"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse kline WS payload: {payload}: {exc}") from exc


@dataclass(frozen=True)
class RawAggTrade:
    agg_trade_id: int
    price: float
    quantity: float
    is_buyer_maker: bool
    trade_time_ms: int

    @staticmethod
    def from_ws_payload(payload: dict) -> "RawAggTrade":
        required = ("a", "p", "q", "m", "T")
        missing = [f for f in required if f not in payload]
        if missing:
            raise DataIntegrityError(f"aggTrade payload missing fields {missing}: {payload}")
        try:
            return RawAggTrade(
                agg_trade_id=int(payload["a"]),
                price=float(payload["p"]),
                quantity=float(payload["q"]),
                is_buyer_maker=bool(payload["m"]),
                trade_time_ms=int(payload["T"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse aggTrade payload: {payload}: {exc}") from exc

    @property
    def is_taker_buy(self) -> bool:
        # If the buyer is the maker, the taker was the seller.
        return not self.is_buyer_maker


@dataclass(frozen=True)
class RawBookTicker:
    symbol: str
    best_bid: float
    best_bid_qty: float
    best_ask: float
    best_ask_qty: float
    event_time_ms: int

    @staticmethod
    def from_ws_payload(payload: dict) -> "RawBookTicker":
        required = ("s", "b", "B", "a", "A")
        missing = [f for f in required if f not in payload]
        if missing:
            raise DataIntegrityError(f"bookTicker payload missing fields {missing}: {payload}")
        try:
            # bookTicker payloads don't always carry an explicit E field
            # on futures; fall back to using local receipt time as the
            # event time is handled by the caller, not here — this
            # parser only extracts what Binance actually sent.
            event_time_ms = int(payload.get("E", payload.get("T", 0)))
            return RawBookTicker(
                symbol=str(payload["s"]),
                best_bid=float(payload["b"]),
                best_bid_qty=float(payload["B"]),
                best_ask=float(payload["a"]),
                best_ask_qty=float(payload["A"]),
                event_time_ms=event_time_ms,
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse bookTicker payload: {payload}: {exc}") from exc


@dataclass(frozen=True)
class RawDepthLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class RawDepthSnapshot:
    symbol: str
    bids: list[RawDepthLevel]
    asks: list[RawDepthLevel]
    event_time_ms: int
    last_update_id: int

    @staticmethod
    def from_ws_payload(payload: dict, symbol: str) -> "RawDepthSnapshot":
        required = ("b", "a")
        missing = [f for f in required if f not in payload]
        if missing:
            raise DataIntegrityError(f"depth payload missing fields {missing}: {payload}")
        try:
            bids = [RawDepthLevel(price=float(p), quantity=float(q)) for p, q in payload["b"]]
            asks = [RawDepthLevel(price=float(p), quantity=float(q)) for p, q in payload["a"]]
            event_time_ms = int(payload.get("E", payload.get("T", 0)))
            last_update_id = int(payload.get("u", payload.get("lastUpdateId", 0)))
            return RawDepthSnapshot(
                symbol=symbol,
                bids=bids,
                asks=asks,
                event_time_ms=event_time_ms,
                last_update_id=last_update_id,
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse depth payload: {payload}: {exc}") from exc


@dataclass(frozen=True)
class RawFundingRate:
    symbol: str
    funding_rate: float
    funding_time_ms: int

    @staticmethod
    def from_rest_row(row: dict) -> "RawFundingRate":
        required = ("symbol", "fundingRate", "fundingTime")
        missing = [f for f in required if f not in row]
        if missing:
            raise DataIntegrityError(f"fundingRate row missing fields {missing}: {row}")
        try:
            return RawFundingRate(
                symbol=str(row["symbol"]),
                funding_rate=float(row["fundingRate"]),
                funding_time_ms=int(row["fundingTime"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse fundingRate row: {row}: {exc}") from exc


@dataclass(frozen=True)
class RawOpenInterest:
    symbol: str
    open_interest: float
    timestamp_ms: int

    @staticmethod
    def from_rest_row_hist(row: dict) -> "RawOpenInterest":
        required = ("symbol", "sumOpenInterest", "timestamp")
        missing = [f for f in required if f not in row]
        if missing:
            raise DataIntegrityError(f"openInterestHist row missing fields {missing}: {row}")
        try:
            return RawOpenInterest(
                symbol=str(row["symbol"]),
                open_interest=float(row["sumOpenInterest"]),
                timestamp_ms=int(row["timestamp"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse openInterestHist row: {row}: {exc}") from exc

    @staticmethod
    def from_rest_current(row: dict) -> "RawOpenInterest":
        required = ("symbol", "openInterest", "time")
        missing = [f for f in required if f not in row]
        if missing:
            raise DataIntegrityError(f"openInterest current row missing fields {missing}: {row}")
        try:
            return RawOpenInterest(
                symbol=str(row["symbol"]),
                open_interest=float(row["openInterest"]),
                timestamp_ms=int(row["time"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse openInterest current row: {row}: {exc}") from exc


@dataclass(frozen=True)
class RawLongShortRatio:
    symbol: str
    long_short_ratio: float
    timestamp_ms: int

    @staticmethod
    def from_rest_row(row: dict) -> "RawLongShortRatio":
        required = ("symbol", "longShortRatio", "timestamp")
        missing = [f for f in required if f not in row]
        if missing:
            raise DataIntegrityError(f"longShortRatio row missing fields {missing}: {row}")
        try:
            return RawLongShortRatio(
                symbol=str(row["symbol"]),
                long_short_ratio=float(row["longShortRatio"]),
                timestamp_ms=int(row["timestamp"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse longShortRatio row: {row}: {exc}") from exc


@dataclass(frozen=True)
class RawTakerLongShortRatio:
    """Wire row from /futures/data/takerlongshortRatio.

    Unlike the four other Futures statistics histories, this response
    has no symbol field; callers supply the requested symbol.
    """

    symbol: str
    taker_buy_sell_ratio: float
    taker_buy_vol: float
    taker_sell_vol: float
    ts_ms: int

    @staticmethod
    def from_rest_row(row: dict, symbol: str) -> "RawTakerLongShortRatio":
        required = ("buySellRatio", "buyVol", "sellVol", "timestamp")
        missing = [field for field in required if field not in row]
        if missing:
            raise DataIntegrityError(f"takerlongshortRatio row missing fields {missing}: {row}")
        try:
            return RawTakerLongShortRatio(
                symbol=str(symbol),
                taker_buy_sell_ratio=float(row["buySellRatio"]),
                taker_buy_vol=float(row["buyVol"]),
                taker_sell_vol=float(row["sellVol"]),
                ts_ms=int(row["timestamp"]),
            )
        except (TypeError, ValueError) as exc:
            raise DataIntegrityError(f"failed to parse takerlongshortRatio row: {row}: {exc}") from exc


@dataclass(frozen=True)
class RawExchangeInfoSymbol:
    symbol: str
    status: str
    price_tick: float
    qty_step: float
    min_qty: float

    @staticmethod
    def from_rest_payload(entry: dict) -> "RawExchangeInfoSymbol":
        required = ("symbol", "status", "filters")
        missing = [f for f in required if f not in entry]
        if missing:
            raise DataIntegrityError(f"exchangeInfo symbol entry missing fields {missing}: {entry}")
        price_tick: float | None = None
        qty_step: float | None = None
        min_qty: float | None = None
        for f in entry["filters"]:
            ftype = f.get("filterType")
            if ftype == "PRICE_FILTER":
                price_tick = float(f["tickSize"])
            elif ftype == "LOT_SIZE":
                qty_step = float(f["stepSize"])
                min_qty = float(f["minQty"])
        if price_tick is None or qty_step is None or min_qty is None:
            raise DataIntegrityError(
                f"exchangeInfo symbol entry missing PRICE_FILTER/LOT_SIZE filters: {entry['symbol']}"
            )
        return RawExchangeInfoSymbol(
            symbol=str(entry["symbol"]),
            status=str(entry["status"]),
            price_tick=price_tick,
            qty_step=qty_step,
            min_qty=min_qty,
        )
