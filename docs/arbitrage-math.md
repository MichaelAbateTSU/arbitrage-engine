# Financial arithmetic and sizing

All financial inputs are Decimal or JSON decimal strings. Quantity is normally
whole contracts for this conservative release, satisfying US whole-contract
constraints and common lot increments. Kalshi wire fractional quantities remain
exact in book depth. Paired sizing uses the compatible maximum lot step; incompatible
steps fail rather than leave unnoticed exposure.

## Calculation

For the same quantity on complementary legs:

```text
gross_cost = sum(price * quantity on leg 1) + sum(price * quantity on leg 2)
conditional_payout = quantity * 1 USD
gross_profit = conditional_payout - gross_cost
net_profit = gross_profit - venue_fees - modeled_slippage - latency_buffer
net_return = net_profit / gross_cost
```

Buy-side pricing walks ascending asks, never midpoint/last trade. Kalshi YES asks
are `1 - NO bid`, and NO asks `1 - YES bid`, with quantities unchanged. US's
single-instrument offers are YES asks and YES bids imply NO asks. International
YES and NO token books are independent.

The search evaluates valid matched lot quantities up to the explicit risk
`max_contracts`, both available depths and fee-inclusive capital caps, retaining
the largest quantity meeting net-dollar/return thresholds. Target-profit selects
the smallest eligible size reaching its target. Other modes provide per-venue,
total-notional, fixed-quantity, maximum eligible depth and bankroll-fraction bounds.
Per-event/league/day/bankroll limits apply again transactionally at reservation.

$500 per side means a maximum **including** fees and allocated safety allowances.
Unequal prices consume unequal dollars for equal quantities; unused allocations
are displayed. There is no equal-dollar claim or five-trades-per-day guarantee.

## Fees and rounding

- Kalshi supported quadratic taker curve: `0.07 * series/event multiplier * Q * p * (1-p)`.
  Unsupported flat fees remain unknown. Per-event effective overrides are queried.
  The estimator bounds six-decimal per-fill ceiling at 0.01-contract granularity
  and adds a cent for balance alignment; it does not assume favorable rebates.
- US: market `feeCoefficient * Q * p * (1-p)`, banker's rounding to a cent.
  Combo contracts are excluded. No volume rebates assumed.
- International: current market `feeSchedule.rate * Q * p * (1-p)`, upward
  five-decimal rounding. Only the documented exponent-one taker schedule is enabled.
  `feesEnabled=false` is explicitly zero; absent parameters remain unknown.

Models retain source/version/observed/valid-until provenance and expire after 15
minutes until refreshed. Fee parameters are estimated from current public metadata;
actual fill partitioning and changing schedules remain paper limitations.

Prices use venue-provided ranges/ticks (including tapered grids); do not snap an
invalid displayed ask into a fictitious executable price. Weighted averages can
retain Decimal division precision and need not themselves lie on a tick.
Storage query columns are Numeric(24,8), while immutable JSON retains exact
decision inputs/results; display rounding never feeds decisions.

Worked demo: 975 matched units consume $393.75 and $481.50, conservative Kalshi
fees $16.537375, US fees $16.94, slippage $1.312875 and safety buffer $0.875250.
Conditional payout is $975 and estimated net result is $64.084500 if those exact
levels/inputs apply. The authoritative result is the saved Decimal calculation,
not this illustrative arithmetic; demo observations vary with scripted movement.
