from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Literal, TypedDict, Unpack

from app.domain import Book, D, FeeSpec, Level, Market, Side

ONE = D("1")


class BookMetadata(TypedDict, total=False):
    exchange_at: datetime | None
    requested_at: datetime | None
    received_at: datetime
    sequence: int | None
    transport: Literal["rest", "websocket", "demo"]
    source: Literal["demo", "public"]
    connected: bool
    synchronized: bool


class BookIntegrityError(ValueError):
    pass


def ordered(levels: list[Level], bids: bool = False) -> list[Level]:
    return sorted(levels, key=lambda level: level.price, reverse=bids)


def complementary_book(
    market_id: str, yes_bids: list[Level], no_bids: list[Level], **kwargs: Unpack[BookMetadata]
) -> tuple[Book, Book]:
    yes = Book(
        market_id=market_id,
        outcome=Side.YES,
        bids=ordered(yes_bids, True),
        asks=ordered([Level(price=1 - x.price, quantity=x.quantity) for x in no_bids]),
        **kwargs,
    )
    no = Book(
        market_id=market_id,
        outcome=Side.NO,
        bids=ordered(no_bids, True),
        asks=ordered([Level(price=1 - x.price, quantity=x.quantity) for x in yes_bids]),
        **kwargs,
    )
    return yes, no


def consume(
    levels: list[Level], quantity: Decimal, limit: Decimal = ONE, haircut: Decimal = ONE
) -> list[Level]:
    if quantity <= 0 or not quantity.is_finite():
        raise ValueError("INVALID_QUANTITY")
    remaining = quantity
    result: list[Level] = []
    for level in levels:
        if level.price > limit:
            break
        take = min(remaining, level.quantity * haircut)
        if take > 0:
            result.append(Level(price=level.price, quantity=take))
            remaining -= take
        if remaining == 0:
            break
    return result


def cost(levels: list[Level]) -> Decimal:
    return sum((x.price * x.quantity for x in levels), D("0"))


def quantity(levels: list[Level]) -> Decimal:
    return sum((x.quantity for x in levels), D("0"))


def fee(levels: list[Level], spec: FeeSpec) -> Decimal:
    if spec.kind == "unknown":
        raise ValueError("UNKNOWN_FEE")
    exact = sum(
        (spec.rate * x.quantity * (x.price * (1 - x.price)) ** spec.exponent for x in levels),
        D("0"),
    )
    if spec.kind == "zero" or not levels:
        return D("0")
    if spec.kind == "kalshi_bound":
        # Bound per-fill 6dp ceiling at minimum 0.01-contract granularity,
        # plus unrebatable balance alignment. Do not assume favorable rebates.
        return (exact + quantity(levels) / 10000 + D("0.01")).quantize(
            D("0.000001"), rounding=ROUND_CEILING
        )
    if spec.kind == "us_quadratic":
        return exact.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
    return exact.quantize(D("0.00001"), rounding=ROUND_CEILING)


def book_reasons(book: Book, market: Market, instant: datetime, max_age: int) -> list[str]:
    reasons: list[str] = market.entry_reasons(instant)
    if not book.synchronized:
        reasons.append("BOOK_UNSYNCHRONIZED")
    if not book.connected:
        reasons.append("VENUE_UNHEALTHY")
    age = book.age_ms(instant)
    if any(
        timestamp is not None and (timestamp - instant).total_seconds() > 1
        for timestamp in (book.received_at, book.exchange_at, book.requested_at)
    ):
        reasons.append("CLOCK_DRIFT")
    if age > max_age:
        reasons.append("BOOK_STALE")
    if book.source != market.source or book.market_id != market.id:
        reasons.append("BOOK_IDENTITY_MISMATCH")
    if not market.price_ranges or any(
        not market.valid_price(level.price, book.outcome) for level in book.bids + book.asks
    ):
        reasons.append("INVALID_TICK_SIZE")
    return reasons


class LocalBook:
    def __init__(self, book: Book) -> None:
        self.book = book
        self.gaps = 0

    def invalidate(self) -> None:
        self.book = self.book.model_copy(update={"synchronized": False, "connected": False})

    def delta(
        self,
        side: str,
        price: Decimal,
        size: Decimal,
        timestamp: datetime,
        sequence: int | None = None,
        additive: bool = False,
    ) -> bool:
        if not self.book.synchronized:
            raise BookIntegrityError("SNAPSHOT_REQUIRED")
        previous = self.book.sequence
        if sequence is not None and previous is not None:
            if sequence == previous:
                return False
            if sequence != previous + 1:
                self.gaps += 1
                self.invalidate()
                raise BookIntegrityError("SEQUENCE_GAP")
        if self.book.exchange_at and timestamp < self.book.exchange_at:
            self.invalidate()
            raise BookIntegrityError("OUT_OF_ORDER_TIMESTAMP")
        if side not in ("bids", "asks"):
            self.invalidate()
            raise BookIntegrityError("INVALID_DELTA_SIDE")
        levels = {x.price: x.quantity for x in getattr(self.book, side)}
        amount = levels.get(price, D("0")) + size if additive else size
        if amount < 0:
            self.invalidate()
            raise BookIntegrityError("NEGATIVE_BOOK_QUANTITY")
        if amount == 0:
            levels.pop(price, None)
        else:
            levels[price] = amount
        payload = self.book.model_dump()
        payload.update(
            {
                side: ordered(
                    [Level(price=p, quantity=q) for p, q in levels.items()], side == "bids"
                ),
                "exchange_at": timestamp,
                "sequence": sequence,
                "version": self.book.version + 1,
            }
        )
        try:
            self.book = Book.model_validate(payload)
        except ValueError as exc:
            self.invalidate()
            raise BookIntegrityError("INVALID_DELTA_BOOK") from exc
        return True
