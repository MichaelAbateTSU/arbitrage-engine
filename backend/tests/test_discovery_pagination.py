import httpx
import respx

from app.venues.clients import InternationalClient, USClient
from app.venues.http import PublicHTTP


def international_market(identifier):
    return {
        "id": identifier,
        "slug": "nfl-atl-car-2026-10-04-" + identifier,
        "question": "Atlanta Falcons wins",
        "description": "Official game winner",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "outcomes": '["Atlanta Falcons","Carolina Panthers"]',
        "clobTokenIds": '["100","200"]',
        "sportsMarketType": "moneyline",
        "gameStartTime": "2026-10-04T17:00:00Z",
        "events": [{"id": "event", "title": "Atlanta Falcons vs Carolina Panthers"}],
    }


def us_market(identifier):
    return {
        "id": identifier,
        "slug": "aec-nfl-atl-car-2026-10-04-" + identifier,
        "question": "Atlanta vs Carolina",
        "description": "Official game winner",
        "active": True,
        "closed": False,
        "status": "MARKET_STATUS_OPEN",
        "sportsMarketType": "moneyline",
        "gameStartTime": "2026-10-04T17:00:00Z",
        "marketSides": [
            {"long": True, "team": {"name": "Atlanta Falcons", "league": "nfl"}},
            {"long": False, "team": {"name": "Carolina Panthers", "league": "nfl"}},
        ],
    }


async def test_gamma_uses_cursor_not_requested_page_length():
    with respx.mock:
        route = respx.get("https://gamma-api.polymarket.com/markets/keyset")
        route.side_effect = [
            httpx.Response(
                200, json={"markets": [international_market("1")], "next_cursor": "next"}
            ),
            httpx.Response(200, json={"markets": [international_market("2")]}),
        ]
        async with httpx.AsyncClient() as http:
            markets = [
                market async for market in InternationalClient(PublicHTTP(http)).discover([])
            ]
        assert len(markets) == 2
        assert route.calls[1].request.url.params["after_cursor"] == "next"
        assert "offset" not in route.calls[0].request.url.params
        assert all(market.quote_currency == "USDC" for market in markets)


async def test_us_continues_past_short_pages_and_filters_locally():
    with respx.mock:
        route = respx.get("https://gateway.polymarket.us/v1/markets")
        route.side_effect = [
            httpx.Response(200, json={"markets": [us_market("1")]}),
            httpx.Response(200, json={"markets": [us_market("2")]}),
            httpx.Response(200, json={"markets": []}),
        ]
        async with httpx.AsyncClient() as http:
            markets = [market async for market in USClient(PublicHTTP(http)).discover([])]
        assert len(markets) == 2
        assert [call.request.url.params["offset"] for call in route.calls] == ["0", "1", "2"]
        assert "sportsMarketTypes" not in route.calls[0].request.url.params
