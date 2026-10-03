import re
import unicodedata
from datetime import timedelta
from typing import Any, Literal

from pydantic import Field

from app.domain import D, Market, Match, Model, Price, Rules, Venue, fingerprint

LEAGUES = {
    "NFL": "football",
    "NCAAF": "football",
    "NBA": "basketball",
    "NCAAB": "basketball",
    "MLB": "baseball",
    "NHL": "hockey",
    "EPL": "soccer",
    "ATP": "tennis",
    "WTA": "tennis",
}


def normalized(value: str) -> str:
    ascii_value = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


class Alias(Model):
    league: str
    original: str
    canonical: str
    source: str
    version: str = "v1"


def normalize_team(value: str, league: str, aliases: list[Alias]) -> str:
    key = normalized(value)
    values = {
        normalized(a.canonical)
        for a in aliases
        if a.league == league and normalized(a.original) == key
    }
    if len(values) > 1:
        raise ValueError("AMBIGUOUS_ALIAS")
    return next(iter(values)) if values else key


def extract_rules(text: str, source: str | None = None) -> Rules:
    value = normalized(text)
    rules = Rules(settlement_source=normalized(source) if source else None)
    if source:
        rules.evidence["settlement_source"] = source
    patterns = {
        "overtime": [
            ("including overtime", "included"),
            ("includes overtime", "included"),
            ("overtime is included if played", "included"),
            ("including any overtime periods", "included"),
            ("regulation only", "excluded"),
            ("excluding overtime", "excluded"),
        ],
        "draw": [
            ("no draw is possible", "impossible"),
            ("tie the market will resolve to 0 50", "half_refund"),
            ("tie the market will settle to 0 50", "half_refund"),
            ("draw this market will resolve to no", "no"),
        ],
        "cancellation": [
            ("market will resolve 50 50", "half_refund"),
            ("market will settle to last fair market price", "fair_price"),
            ("market will resolve to a fair price", "fair_price"),
            ("all markets will resolve to a fair price", "fair_price"),
        ],
        "postponement": [
            ("within 48 hours", "within_48h"),
            ("within two weeks", "within_two_weeks"),
            ("within two calendar days", "within_two_calendar_days"),
            ("remain open until the game has been completed", "until_completed"),
            ("settle following the conclusion of the rescheduled match", "until_completed"),
        ],
        "period": [("full game", "full_game"), ("regulation only", "full_game")],
    }
    data = rules.model_dump()
    for field, candidates in patterns.items():
        found = [(pattern, outcome) for pattern, outcome in candidates if pattern in value]
        if field == "cancellation" and "cancel" not in value:
            found = []
        if field == "postponement" and "postpon" not in value:
            found = []
        if len({outcome for _, outcome in found}) == 1:
            data[field] = found[0][1]
            rules.evidence[field] = text
    data["evidence"] = rules.evidence
    if source is None:
        result = re.search(r"outcome sourced from ([^.]+)", text, flags=re.IGNORECASE)
        if result:
            data["settlement_source"] = normalized(result.group(1))
            rules.evidence["settlement_source"] = result.group(0)
    return Rules.model_validate(data)


def event_identity(market: Market) -> str:
    return fingerprint(
        {
            "league": market.league,
            "participants": sorted(market.participants),
            "date": market.start_time.date().isoformat() if market.start_time else market.event_id,
        }
    )[:24]


def match_markets(first: Market, second: Market, human_reviewed: bool = False) -> Match:
    reasons: list[str] = []
    unknown: list[str] = []
    checks: dict[str, Any] = {}
    if first.venue != Venue.KALSHI or second.venue == Venue.KALSHI:
        reasons.append("INVALID_VENUE_PAIR")
    if first.source != second.source:
        reasons.append("SOURCE_MODE_MISMATCH")
    for field in ("sport", "league", "market_type"):
        a, b = getattr(first, field), getattr(second, field)
        checks[field] = [a, b]
        if a in ("unknown", "UNKNOWN") or b in ("unknown", "UNKNOWN"):
            unknown.append(f"UNKNOWN_{field.upper()}")
        elif a != b:
            reasons.append(f"{field.upper()}_MISMATCH")
    if first.market_type not in ("moneyline", "winner"):
        reasons.append("UNSUPPORTED_MARKET_TYPE")
    if len(first.participants) != 2 or len(second.participants) != 2:
        unknown.append("UNKNOWN_PARTICIPANTS")
    elif set(first.participants) != set(second.participants):
        reasons.append("PARTICIPANTS_MISMATCH")
    if (
        not first.start_time
        or not second.start_time
        or not first.start_time_verified
        or not second.start_time_verified
    ):
        unknown.append("UNKNOWN_EVENT_START")
    elif abs(first.start_time - second.start_time) > timedelta(minutes=5):
        reasons.append("EVENT_TIME_MISMATCH")
    if not first.yes_team or not second.yes_team:
        unknown.append("UNKNOWN_OUTCOME")
    elif first.yes_team not in first.participants or second.yes_team not in second.participants:
        reasons.append("OUTCOME_MISMATCH")
    for field in (
        "period",
        "overtime",
        "draw",
        "cancellation",
        "postponement",
        "settlement_source",
    ):
        a, b = getattr(first.rules, field), getattr(second.rules, field)
        checks[field] = [a, b]
        if a is None or b is None:
            unknown.append(f"UNKNOWN_{field.upper()}")
        elif a != b:
            reasons.append(f"{field.upper()}_MISMATCH")
    if not first.rules_text.strip() or not second.rules_text.strip():
        unknown.append("MISSING_RULES")
    if normalized(first.rules_text) != normalized(second.rules_text) and not human_reviewed:
        unknown.append("RULE_TEXT_REVIEW_REQUIRED")
    if first.rules.cancellation not in (None, "half_refund") or second.rules.cancellation not in (
        None,
        "half_refund",
    ):
        reasons.append("NONCONSTANT_CANCELLATION_PAYOUT")
    if first.rules.payout != 1 or second.rules.payout != 1:
        reasons.append("PAYOUT_MISMATCH")
    for market in (first, second):
        if market.source == "public" and market.rules.discretionary_settlement is None:
            unknown.append("UNKNOWN_DISCRETIONARY_SETTLEMENT")
        if market.rules.discretionary_settlement == "excluded" and any(
            phrase in normalized(market.rules_text)
            for phrase in ("fair price", "fair market price")
        ):
            reasons.append("DISCRETIONARY_POLICY_CONTRADICTS_SOURCE")
    if first.rules.draw == "no" or second.rules.draw == "no":
        if first.yes_team != second.yes_team:
            reasons.append("DRAW_BREAKS_INVERTED_COMPLEMENT")
    status: Literal["approved", "review", "rejected"] = (
        "rejected" if reasons else ("review" if unknown else "approved")
    )
    identity = event_identity(first)
    return Match(
        id=fingerprint([first.id, second.id, first.rules_hash, second.rules_hash])[:32],
        event_id=identity,
        first_market_id=first.id,
        second_market_id=second.id,
        first_rules_hash=first.rules_hash,
        second_rules_hash=second.rules_hash,
        status=status,
        confidence=D("1") if status == "approved" else D("0"),
        inverted=bool(first.yes_team and second.yes_team and first.yes_team != second.yes_team),
        reasons=reasons + unknown,
        evidence=checks,
        human_reviewed=human_reviewed,
    )


class SemanticSuggestion(Model):
    candidate_match: bool
    confidence: Price
    uncertainties: list[str] = Field(default_factory=list)
    evidence: list[dict[str, str]] = Field(default_factory=list)
    model_name: str
    prompt_version: str
    rules_hash: str


def parse_semantic_result(text: str) -> SemanticSuggestion:
    if len(text) > 32768:
        raise ValueError("SEMANTIC_RESPONSE_TOO_LARGE")
    return SemanticSuggestion.model_validate_json(text)
