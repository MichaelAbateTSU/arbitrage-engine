from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.db import ReplayRow, SnapshotRow
from app.domain import Book, D, Market, Opportunity, PaperState, PaperTrade, Side, fingerprint
from app.paper import evaluate_paper
from app.store import Store


def replay_trade(
    trade: PaperTrade,
    opportunity: Opportunity,
    events: list[Book],
) -> tuple[PaperTrade, list[dict[str, Any]]]:
    result = trade.model_copy(deep=True)
    # Replay starts at the persisted submitted intent, never at an invented fill.
    result.state = PaperState.SUBMITTED
    result.first = result.first.__class__()
    result.second = result.second.__class__()
    result.unhedged_quantity = D("0")
    result.locked_profit = None
    result.settlement_profit = None
    result.failure_reason = None
    result.fill_at = None
    result.transitions = []
    result.replay_of = trade.id
    result.id = uuid4().hex
    a = Market.model_validate(opportunity.inputs["first_market"])
    b = Market.model_validate(opportunity.inputs["second_market"])
    books: dict[tuple[str, Side], Book] = {}
    steps = []
    for event in sorted(events, key=lambda x: x.received_at):
        if event.received_at < trade.decision_at:
            continue
        books[(event.market_id, event.outcome)] = event
        changed = evaluate_paper(result, opportunity, books, a, b, event.received_at, False)
        steps.append(
            {
                "at": event.received_at.isoformat(),
                "book": f"{event.market_id}:{event.outcome}",
                "state": result.state,
            }
        )
        if changed:
            break
    if not result.fill_at and result.state == "SUBMITTED":
        result.failure_reason = "REPLAY_DATA_INCOMPLETE"
    return result, steps


async def persisted_replay(
    store: Store, trade: PaperTrade, opportunity: Opportunity
) -> dict[str, Any]:
    async with store.sessions() as session:
        rows = (
            await session.scalars(
                select(SnapshotRow)
                .where(
                    SnapshotRow.source == store.source,
                    SnapshotRow.market_id.in_(
                        [
                            opportunity.first_market_id,
                            opportunity.second_market_id,
                        ]
                    ),
                    SnapshotRow.created_at >= trade.decision_at,
                    SnapshotRow.created_at <= opportunity.expires_at,
                )
                .order_by(SnapshotRow.created_at)
            )
        ).all()
    events = [Book.model_validate(row.payload) for row in rows]
    result, steps = replay_trade(trade, opportunity, events)
    payload = {
        "id": result.id,
        "replay_of": trade.id,
        "result": result.model_dump(mode="json"),
        "steps": steps,
        "source_version": fingerprint([x.model_dump(mode="json") for x in events]),
        "matching_version": opportunity.inputs["match"]["version"],
        "fee_versions": [
            opportunity.inputs["first_market"]["fee"]["version"],
            opportunity.inputs["second_market"]["fee"]["version"],
        ],
        "fill_version": trade.model_version,
        "risk": trade.settings.model_dump(mode="json"),
        "software_version": store.settings.build_version,
        "time_multiplier": "deterministic-step",
        "limitations": (
            "No resting-order queue identity. Historical replay, not a predictive backtest."
        ),
    }
    async with store.sessions.begin() as session:
        session.add(
            ReplayRow(
                id=result.id,
                source=store.source,
                trade_id=trade.id,
                payload=payload,
            )
        )
    return payload
