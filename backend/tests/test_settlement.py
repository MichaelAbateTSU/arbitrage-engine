import httpx
import pytest
import respx
from test_paper import intent

from app.config import Settings
from app.demo import demo_markets
from app.domain import D, PaperState, now
from app.paper import settle
from app.venues.clients import InternationalClient, KalshiClient, USClient
from app.venues.http import PublicHTTP


@pytest.mark.parametrize("status, expected", [("determined", None), ("settled", D("0.5"))])
async def test_kalshi_only_final_settlement(status, expected):
    market = demo_markets()[0]
    with respx.mock:
        respx.get(
            f"https://external-api.kalshi.com/trade-api/v2/markets/{market.external_id}"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "market": {
                        "ticker": market.external_id,
                        "status": status,
                        "settlement_value_dollars": "0.5",
                    }
                },
            )
        )
        async with httpx.AsyncClient() as http:
            assert (
                await KalshiClient(PublicHTTP(http), Settings()).get_settlement(market) == expected
            )


async def test_us_not_settled_is_not_zero_payout():
    market = demo_markets()[1]
    with respx.mock:
        respx.get(f"https://gateway.polymarket.us/v1/markets/{market.external_id}/settlement").mock(
            return_value=httpx.Response(404)
        )
        async with httpx.AsyncClient() as http:
            assert await USClient(PublicHTTP(http)).get_settlement(market) is None


async def test_international_micro_usdc_payout_not_display_price():
    market = demo_markets()[3]
    market.raw = {**market.raw, "conditionId": "condition"}
    with respx.mock:
        respx.get("https://data-api.polymarket.com/v2/resolutions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "condition_id": "condition",
                            "status": "resolved",
                            "payouts": [1000000, 0],
                        }
                    ]
                },
            )
        )
        async with httpx.AsyncClient() as http:
            assert await InternationalClient(PublicHTTP(http)).get_settlement(market) == D("1")


def test_settlement_is_separate_and_releases_hedged_position(scenario, opportunity):
    from app.paper import evaluate_paper

    a, b, books, _, risk = scenario
    trade = intent(opportunity, risk)
    for book in books.values():
        book.received_at = book.exchange_at = trade.due_at
    assert evaluate_paper(trade, opportunity, books, a, b, trade.due_at, False)
    assert trade.state == PaperState.HEDGED
    assert not settle(trade, opportunity, a, b, now())
    a.status = b.status = "settled"
    a.result = b.result = D("1")
    assert settle(trade, opportunity, a, b, now())
    assert trade.state == PaperState.SETTLED
    assert trade.settlement_profit is not None
