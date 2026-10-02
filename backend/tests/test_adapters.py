from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.config import Settings
from app.demo import demo_markets
from app.domain import D, Side
from app.venues.clients import KalshiClient, us_books
from app.venues.http import PublicHTTP, VenueError, decode
from app.venues.schemas import USBook
from app.venues.streams import InternationalStreamState, KalshiStreamState


async def test_kalshi_current_fixed_point_snapshot():
    market = demo_markets()[0]
    with respx.mock:
        respx.get(
            f"https://external-api.kalshi.com/trade-api/v2/markets/{market.external_id}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "market": {
                        "status": "active",
                        "title": market.title,
                        "rules_primary": market.rules_text,
                        "rules_secondary": "",
                    }
                },
            )
        )
        respx.get(
            f"https://external-api.kalshi.com/trade-api/v2/markets/{market.external_id}/orderbook"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "orderbook_fp": {
                        "yes_dollars": [["0.44", "100.55"]],
                        "no_dollars": [["0.56", "200.25"]],
                    },
                },
            )
        )
        async with httpx.AsyncClient() as http:
            books = await KalshiClient(PublicHTTP(http, 10), Settings()).get_orderbooks(market)
    assert books[0].asks[0].price == D("0.44")
    assert books[0].asks[0].quantity == D("200.25")
    assert books[1].asks[0].price == D("0.56")


def test_us_documented_single_instrument_book():
    market = demo_markets()[1]
    wire = USBook.model_validate(
        {
            "marketData": {
                "marketSlug": market.external_id,
                "state": "MARKET_STATE_OPEN",
                "bids": [{"px": {"value": "0.55", "currency": "USD"}, "qty": "100"}],
                "offers": [{"px": {"value": "0.56", "currency": "USD"}, "qty": "200"}],
                "transactTime": "2026-09-30T16:00:00Z",
            }
        }
    )
    books = us_books(market, wire)
    assert books[0].asks[0].price == D("0.56")
    assert books[1].asks[0].price == D("0.45")


def test_kalshi_sequence_is_subscription_scoped():
    markets = demo_markets()
    first, other = markets[0], markets[2]
    stream = KalshiStreamState([first, other])
    for ticker, sequence in ((first.external_id, 1), (other.external_id, 2)):
        books = stream.apply(
            {
                "type": "orderbook_snapshot",
                "sid": 10,
                "seq": sequence,
                "msg": {
                    "market_ticker": ticker,
                    "yes_dollars_fp": [["0.44", "100"]],
                    "no_dollars_fp": [["0.55", "100"]],
                },
            }
        )
        assert len(books) == 2
    books = stream.apply(
        {
            "type": "orderbook_delta",
            "sid": 10,
            "seq": 3,
            "msg": {
                "market_ticker": first.external_id,
                "side": "yes",
                "price_dollars": "0.44",
                "delta_fp": "-25",
            },
        }
    )
    assert books[1].asks[0].quantity == 75
    from app.pricing import BookIntegrityError

    with pytest.raises(BookIntegrityError):
        stream.apply({"type": "orderbook_delta", "sid": 10, "seq": 5, "msg": {}})


def test_lifecycle_invalidates_even_a_connected_book():
    market = demo_markets()[0]
    state = KalshiStreamState([market])
    books = state.apply(
        {
            "type": "market_lifecycle_v2",
            "sid": 11,
            "seq": 1,
            "msg": {
                "market_ticker": market.external_id,
                "event_type": "price_level_structure_updated",
            },
        }
    )
    assert all(not x.connected and not x.synchronized for x in books)
    assert not market.tradable


def test_event_lifecycle_consumes_the_shared_lifecycle_sequence():
    market = demo_markets()[0]
    state = KalshiStreamState([market])
    state.apply(
        {
            "type": "market_lifecycle_v2",
            "sid": 2,
            "seq": 1,
            "msg": {"market_ticker": "UNTRACKED", "event_type": "created"},
        }
    )
    assert (
        state.apply(
            {
                "type": "event_lifecycle",
                "sid": 2,
                "seq": 2,
                "msg": {"event_ticker": "NEW"},
            }
        )
        == []
    )
    assert (
        state.apply(
            {
                "type": "market_lifecycle_v2",
                "sid": 2,
                "seq": 3,
                "msg": {"market_ticker": "UNTRACKED", "event_type": "created"},
            }
        )
        == []
    )


def test_international_absolute_deltas_do_not_duplicate():
    market = demo_markets()[3]
    market.token_ids = {Side.YES: "123", Side.NO: "456"}
    state = InternationalStreamState([market])
    timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    state.apply(
        {
            "event_type": "book",
            "asset_id": "123",
            "market": "condition",
            "bids": [{"price": "0.4", "size": "100"}],
            "asks": [{"price": "0.5", "size": "100"}],
            "timestamp": timestamp,
            "hash": "recorded-shape",
        }
    )
    event = {
        "event_type": "price_change",
        "timestamp": timestamp,
        "market": "condition",
        "price_changes": [{"asset_id": "123", "side": "BUY", "price": "0.4", "size": "125"}],
    }
    state.apply(event)
    books = state.apply(event)
    assert books[0].bids[0].quantity == D("125")


async def test_error_and_malformed_json():
    with respx.mock:
        respx.get("https://example.invalid/shape").mock(
            return_value=httpx.Response(200, text="{bad")
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(VenueError, match="MALFORMED_JSON"):
                await PublicHTTP(http).get("https://example.invalid/shape")
    with pytest.raises(VenueError):
        decode('{"money":NaN}')


async def test_429_retries_then_succeeds(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("app.venues.http.asyncio.sleep", no_sleep)
    with respx.mock:
        route = respx.get("https://example.invalid/limit")
        route.side_effect = [httpx.Response(429), httpx.Response(200, json={"ok": True})]
        async with httpx.AsyncClient() as http:
            assert await PublicHTTP(http).get("https://example.invalid/limit") == {"ok": True}
        assert route.call_count == 2
