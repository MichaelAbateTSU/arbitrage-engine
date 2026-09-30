"""Bounded read-only probes. Never routes orders or circumvents restrictions."""

import asyncio
import json

import httpx
from app.config import get_settings
from app.venues.clients import InternationalClient, KalshiClient, USClient
from app.venues.http import PublicHTTP
from app.venues.mapper import gamma_market, kalshi_market, us_market
from app.venues.schemas import GammaMarket, KalshiPage, USPage


async def main():
    settings = get_settings()
    results = []
    async with httpx.AsyncClient() as http:
        network = PublicHTTP(http)
        kalshi = KalshiClient(network, settings)
        data = await network.get(
            kalshi.base + "/markets",
            {
                "limit": 1,
                "series_ticker": "KXNFLGAME",
                "status": "open",
                "mve_filter": "exclude",
            },
        )
        wires = KalshiPage.model_validate(data)
        if wires.markets:
            series = (await network.get(kalshi.base + "/series/KXNFLGAME"))["series"]
            rate = await kalshi._event_rate(wires.markets[0].event_ticker, series)
            market = kalshi_market(wires.markets[0], "NFL", series, rate, [])
            books = await kalshi.get_orderbooks(market)
            results.append(
                {
                    "venue": "kalshi",
                    "markets": 1,
                    "books": len(books),
                    "fee": market.fee.kind,
                    "identity": market.external_id,
                }
            )
        us = USClient(network)
        data = await network.get(
            us.base + "/v1/markets",
            {
                "limit": 1,
                "active": "true",
                "closed": "false",
                "sportsMarketTypes": "SPORTS_MARKET_TYPE_MONEYLINE",
            },
        )
        wires_us = USPage.model_validate(data)
        if wires_us.markets:
            market = us_market(wires_us.markets[0], [])
            books = await us.get_orderbooks(market)
            results.append(
                {
                    "venue": "polymarket_us",
                    "markets": 1,
                    "books": len(books),
                    "identity": market.external_id,
                    "fee": market.fee.kind,
                }
            )
        international = InternationalClient(network)
        data = await network.get(
            international.gamma + "/markets", {"limit": 1, "closed": "false"}
        )
        wire = GammaMarket.model_validate(data[0])
        market = gamma_market(wire, [])
        books = await international.get_orderbooks(market)
        results.append(
            {
                "venue": "polymarket_international",
                "markets": 1,
                "books": len(books),
                "identity": market.external_id,
                "fee": market.fee.kind,
                "scope": "public protocol probe; not necessarily a sports market",
            }
        )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
