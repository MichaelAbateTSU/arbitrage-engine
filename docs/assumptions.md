# Assumptions and external limitations

- USD binary unit contracts; equal contract quantity, not equal dollar allocation.
- International books/payouts are USDC. Cross-currency paper calculations require
  an explicit `allow_usdc_parity_assumption` risk acknowledgement (off by default).
  USD reports then assume parity; depeg, chain/redemption and transfer costs are
  not established by an order book. Synthetic demo prices use hypothetical USD.
- Automated match precision is prioritized over signal count. Unknown semantics
  or game starts make a pair review-only; matched variable fair prices are unsafe.
- One operator, no customer wallets/custody/billing or unlicensed data resale.
- Read-only venue endpoints can still be subject to terms, geographic restrictions
  and access changes. Do not circumvent them.
- NFL/NBA/MLB/NHL and configurable NCAA/soccer/tennis identities are modeled;
  discovery does not imply every venue exposes every league/type.
- Kalshi whitelist covers simple game series; broader series require documented
  configuration/integration, not inference from prop/parlay titles.
- Whole paired contracts are conservative. Wire fractional depth is retained.
- Fees are current-metadata estimates with explicit source/version/expiry; actual
  fill partitioning, rounding/rebates and future schedules are not guaranteed.
- $500-per-venue and five-per-day are experiment controls, not achievable returns.
- Capital may stay locked until settlement; paper bankroll does not regenerate
  from a displayed locked profit.
- Short-retention L2 lacks maker queue identity. Observed-fill inference and
  predictive backtesting are unavailable without adequate evidence.
- No production LLM provider: strict suggestion schema and deterministic fallback
  exist, but confidential data is not automatically sent to a third party.
- Alert records and health are in-app; no external email/webhook delivery is
  configured or claimed.
- Business-model/legal assertions in the attached report are planning hypotheses,
  not verified financial outcomes or legal advice.
