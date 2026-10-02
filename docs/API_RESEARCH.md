# Official API research

Reviewed **2026-09-30**. Sources are first-party documentation and bounded public
HTTP probes, not the attached report's unresolvable generated citation markers.

## Kalshi

Documentation index: https://docs.kalshi.com/llms.txt

Reviewed environment/quick-start/auth/WebSocket, fixed-point migration, fee
rounding, rate limits, market/orderbook/series/event metadata and event fee-change
references. Current documented Trade API OpenAPI version: **3.32.0**.

- REST: `https://external-api.kalshi.com/trade-api/v2`
- Demo: `https://external-api.demo.kalshi.co/trade-api/v2`
- Streams: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
- Discovery: `/markets?series_ticker=...&status=open&mve_filter=exclude`, cursors.
- Books: `orderbook_fp.yes_dollars/no_dollars`, decimal-string quantities/prices.
- WS snapshot: `yes_dollars_fp/no_dollars_fp`; deltas `price_dollars/delta_fp`.
- Sequences are checked per subscription ID across markets.
- Event-creation `event_lifecycle` messages share the lifecycle subscription
  sequence and must advance it even when they are not market-book updates.
- Lifecycle subscription is separate and unfiltered (the official channel rejects
  ticker filters); changes invalidate the market until authoritative rediscovery.
- RSA-PSS SHA256 or Ed25519 headers sign timestamp + method + path, no query.
  Rechecked 2026-10-02: Kalshi now documents both key types and recommends
  Ed25519. The adapter chooses the algorithm from the parsed key, accepting PEM,
  escaped-newline PEM, or base64 PKCS#8 DER without generating/replacing key material.
- Price grid source: `price_ranges`, not legacy integer cents or structure labels.
- Fee source: series type/multiplier plus effective `/events/fee_changes`.
- Settlement: terminal market status and `settlement_value_dollars`, never last price.
- Rate budget: shared token bucket; 429 currently no Retry-After.

Probe: production public discovery, series, fee metadata and book endpoints
returned data. Observed `status=active` maps to internal `open`. Sample NFL
rules use 48-hour postponement plus fair-price fallback, which is not equivalent
to indefinite postponement/refund contracts. `occurrence_datetime` can represent
completion, not start; do not invent a scheduled kickoff.

Sources:
https://docs.kalshi.com/api-reference/market/get-market-orderbook
https://docs.kalshi.com/websockets/orderbook-updates
https://docs.kalshi.com/getting_started/fixed_point_migration
https://docs.kalshi.com/getting_started/fee_rounding
https://docs.kalshi.com/api-reference/market/get-series
https://docs.kalshi.com/api-reference/events/get-event-fee-changes
https://docs.kalshi.com/getting_started/rate_limits

## Polymarket International

Index: https://docs.polymarket.com/llms.txt
Reviewed API/wallet auth, discovery, market details, current fees, market WebSocket,
and geographic restrictions.

- Gamma metadata: `https://gamma-api.polymarket.com/markets/keyset`, documented
  `sports_market_types=moneyline` filtering and `next_cursor`/`after_cursor`.
  Observed legacy pages were capped below requested limits; length is not proof
  of discovery completion.
- CLOB: `https://clob.polymarket.com/book?token_id=...`.
- Public WS: `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- `outcomes` and `clobTokenIds` are JSON-encoded arrays; correlate by index.
- Market details now define `orderMinSize` in **USDC notional**, not shares.
- `feeSchedule.rate/exponent/takerOnly` is authoritative; current sports example
  is exponent-one rate 0.05. Do not reuse old sports fee curves.
- Stream raw wire is `event_type`, `asset_id`, `price_changes`; SDK examples use
  a different camelCase/topic wrapper. Adapter implements raw documented wire.
- Absolute price-change sizes replace level size; do not add duplicate messages.
- Settlement: public `/v2/resolutions?condition=...`, `status=resolved` and
  two-element `payouts` in micro-USDC. Proposals/expected times are not final payouts.
- PING every ten seconds, PONG handling; no documented sequence guarantee.
- Wallet L1 EIP-712 and L2 HMAC/order signing reviewed but **not implemented**
  because orders/account access are deliberately unavailable.

Public Gamma/CLOB requests returned data without a wallet. Listing timestamps
are not game start. Missing game/rule fields remain unknown. Published US
restrictions apply to order placement; this application never submits orders.

Sources:
https://docs.polymarket.com/getting-started/api
https://docs.polymarket.com/market-data/discover-markets
https://docs.polymarket.com/market-data/market-details
https://docs.polymarket.com/trading/fees
https://docs.polymarket.com/api-reference/wss/market
https://docs.polymarket.com/api-reference/geoblock

## Polymarket US

Index: https://docs.polymarket.us/llms.txt
Reviewed public markets/books, retail authentication/WebSockets, fee schedule and
rate limits. Retail gateway OpenAPI version **1.0.0**.

- Public: `https://gateway.polymarket.us/v1/markets`, offset pagination.
- Sports types are filtered locally: the observed uppercase server-side filter
  omitted supported leagues. Offset pages continue until an empty page.
- Book: `/v1/markets/{slug}/book`, `marketData.bids/offers`.
- Price is `px.value`, quantity `qty`; currency must be USD.
- Single long/short instrument: marketSides' `long=true` identifies YES; do not
  trust deprecated outcomes array order when typed long-side metadata exists.
- Market metadata gives team/league, `gameStartTime`, fee coefficient/tick/minimum.
- Settlement: `/v1/markets/{slug}/settlement`; documented 404 means absent/not settled.
- US stream: `wss://api.polymarket.us/v1/ws/markets`, authenticated Ed25519 header.
- Full market-data stream delivers snapshots; subscribe in batches of <=100.
- Current fee effective Sept 25: coefficient 0.0695; banker's cent rounding;
  no volume rebate assumed. Table-tennis change excluded with unsupported leagues.
- 20 requests/sec public limit; application defaults to shared 5/sec.

Public market/book endpoints returned data. International and US are different
venues, protocols, identifiers and eligibility boundaries. Institutional gRPC/FIX
and authenticated portfolio/orders were reviewed by index but are not claimed as
implemented. Retail read-only REST/WS is the supported surface.

Sources:
https://docs.polymarket.us/api-reference/markets/get-markets
https://docs.polymarket.us/api-reference/markets/get-market-book
https://docs.polymarket.us/api-reference/websocket/markets
https://docs.polymarket.us/api-reference/authentication
https://docs.polymarket.us/fees
https://docs.polymarket.us/api-reference/rate-limits

## Render

Reviewed https://render.com/docs/blueprint-spec and
https://render.com/docs/deploy-fastapi. Blueprint schema is validated against
https://render.com/schema/render.yaml.json.

Same-region managed PostgreSQL, Docker web/workers, predeploy migration, health
checks, protected environment injection, private networking, checks-pass
auto-deploy and disabled previews are specified. The optional frontend is a
same-origin proxy because cross-site cookie deployment introduces avoidable
browser/SameSite/CSRF complications. Render API access is not proof of a deployment.

## Ambiguities / safe exclusions

No automatic inference of game start from market expiry; no variable-price refund
complementarity; no unknown fee as zero; no fabricated account/trade success;
no claimed lossless international stream; no observed queue model without data;
no full commercial redistribution or legal eligibility assumption.
