from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Wire(BaseModel):
    model_config = ConfigDict(extra="allow")


class KalshiMarket(Wire):
    ticker: str
    event_ticker: str
    title: str
    status: str
    market_type: str
    rules_primary: str = ""
    rules_secondary: str = ""
    yes_sub_title: str = ""
    price_ranges: list[dict[str, str]] = Field(default_factory=list)
    result: str = ""
    open_time: str | None = None
    close_time: str | None = None
    floor_strike: Decimal | None = None
    strike_type: str | None = None
    notional_value_dollars: Decimal | None = None

    @field_validator("floor_strike", mode="before")
    @classmethod
    def exact_strike(cls, value: Any) -> Any:
        if isinstance(value, (float, bool)):
            raise ValueError("Strike requires an exact decimal")
        return value


class KalshiPage(Wire):
    markets: list[KalshiMarket]
    cursor: str = ""


class KalshiBook(Wire):
    orderbook_fp: dict[str, list[tuple[Decimal, Decimal]]]


class GammaMarket(Wire):
    id: str
    question: str | None = None
    slug: str
    description: str = ""
    active: bool
    closed: bool
    acceptingOrders: bool = False
    enableOrderBook: bool = False
    outcomes: str
    clobTokenIds: str | None = None
    sportsMarketType: str | None = None
    gameStartTime: str | None = None
    orderPriceMinTickSize: Decimal | None = None
    orderMinSize: Decimal | None = None
    feeSchedule: dict[str, Any] | None = None
    feesEnabled: bool | None = None
    resolutionSource: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)


class InternationalBook(Wire):
    asset_id: str
    market: str
    timestamp: str
    bids: list[dict[str, str]]
    asks: list[dict[str, str]]
    tick_size: str
    min_order_size: str


class Amount(Wire):
    value: Decimal
    currency: str

    @field_validator("value", mode="before")
    @classmethod
    def exact_amount(cls, value: Any) -> Any:
        if isinstance(value, (float, bool)):
            raise ValueError("Amount requires an exact decimal")
        return value


class CryptoAsset(Wire):
    assetClass: str
    symbol: str


class AssetPriceTerms(Wire):
    marketType: str
    asset: CryptoAsset
    indexSymbol: str
    horizon: str
    windowStart: str | None = None
    windowEnd: str | None = None
    priceToBeat: Amount | None = None


class USMarket(Wire):
    id: str
    slug: str
    question: str | None = None
    description: str = ""
    active: bool
    closed: bool
    status: str = ""
    sportsMarketType: str | None = None
    sportsMarketTypeV2: str | None = None
    gameStartTime: str | None = None
    marketSides: list[dict[str, Any]]
    feeCoefficient: Decimal | None = None
    orderPriceMinTickSize: Decimal | None = None
    minimumTradeQty: Decimal | None = None
    assetPriceTerms: AssetPriceTerms | None = None


class USPage(Wire):
    markets: list[USMarket]


class USLevel(Wire):
    px: Amount
    qty: Decimal


class USBookData(Wire):
    marketSlug: str
    bids: list[USLevel]
    offers: list[USLevel]
    state: str
    transactTime: datetime


class USBook(Wire):
    marketData: USBookData
