import { z } from "zod";

export const decimal = z
  .string()
  .regex(/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/, "Expected an exact decimal string");
const timestamp = z.string().datetime({ offset: true });
export const source = z.enum(["demo", "public"]);
export const side = z.enum(["YES", "NO"]);
export const venue = z.enum([
  "kalshi",
  "polymarket_us",
  "polymarket_international",
]);
export const level = z.object({ price: decimal, quantity: decimal });
export const bookSchema = z
  .object({
    market_id: z.string(),
    outcome: side,
    bids: z.array(level),
    asks: z.array(level),
    received_at: timestamp,
    exchange_at: timestamp.nullable(),
    synchronized: z.boolean(),
    connected: z.boolean(),
    source,
    transport: z.string(),
  })
  .passthrough();
export const rulesSchema = z.object({
  period: z.string().nullable(),
  overtime: z.string().nullable(),
  draw: z.string().nullable(),
  cancellation: z.string().nullable(),
  postponement: z.string().nullable(),
  settlement_source: z.string().nullable(),
  payout: decimal,
  evidence: z.record(z.string(), z.string()),
});
export const marketSchema = z
  .object({
    id: z.string(),
    venue,
    external_id: z.string(),
    title: z.string(),
    rules_text: z.string(),
    league: z.string(),
    sport: z.string(),
    participants: z.array(z.string()),
    yes_team: z.string().nullable(),
    start_time: timestamp.nullable(),
    rules: rulesSchema,
    status: z.string(),
    source,
  })
  .passthrough();
export const matchSchema = z
  .object({
    id: z.string(),
    event_id: z.string(),
    first_market_id: z.string(),
    second_market_id: z.string(),
    first_rules_hash: z.string(),
    second_rules_hash: z.string(),
    status: z.enum(["approved", "review", "rejected"]),
    confidence: decimal,
    reasons: z.array(z.string()),
    inverted: z.boolean(),
    human_reviewed: z.boolean(),
  })
  .passthrough();
export const matchDetailSchema = matchSchema.extend({
  first_market: marketSchema,
  second_market: marketSchema,
  reviews: z.array(
    z
      .object({ action: z.string(), note: z.string(), at: timestamp })
      .passthrough(),
  ),
});
export const calculationSchema = z.object({
  quantity: decimal,
  cost_one: decimal,
  cost_two: decimal,
  price_one: decimal,
  price_two: decimal,
  limit_one: decimal,
  limit_two: decimal,
  fee_one: decimal,
  fee_two: decimal,
  slippage: decimal,
  safety_buffer: decimal,
  additional_cost_one: decimal.default("0"),
  additional_cost_two: decimal.default("0"),
  payout: decimal,
  gross_profit: decimal,
  net_profit: decimal,
  net_return: decimal,
  unused_one: decimal,
  unused_two: decimal,
  binding_constraint: z.string(),
  consumed_one: z.array(level),
  consumed_two: z.array(level),
  version: z.string(),
});
export const opportunitySchema = z.object({
  id: z.string(),
  match_id: z.string(),
  event_id: z.string(),
  event: z.string(),
  sport: z.string(),
  league: z.string(),
  first_market_id: z.string(),
  second_market_id: z.string(),
  first_outcome: side,
  second_outcome: side,
  second_venue: venue,
  detected_at: timestamp,
  expires_at: timestamp,
  calculation: calculationSchema,
  confidence: decimal,
  quote_age_ms: z.number(),
  risk_status: z.enum(["qualified", "rejected"]),
  reasons: z.array(z.string()),
  source,
  inputs: z
    .object({
      first_book: bookSchema,
      second_book: bookSchema,
      first_market: marketSchema,
      second_market: marketSchema,
    })
    .passthrough(),
});
const fill = z.object({
  quantity: decimal,
  cost: decimal,
  fee: decimal,
  price: decimal.nullable(),
  consumed: z.array(level),
});
export const tradeSchema = z
  .object({
    id: z.string(),
    opportunity_id: z.string(),
    state: z.string(),
    quantity: decimal,
    first: fill,
    second: fill,
    expected_profit: decimal,
    locked_profit: decimal.nullable(),
    settlement_profit: decimal.nullable(),
    unhedged_quantity: decimal,
    model: z.string(),
    decision_at: timestamp,
    due_at: timestamp,
    fill_at: timestamp.nullable(),
    failure_reason: z.string().nullable(),
    source,
    league: z.string(),
    transitions: z.array(
      z.object({ from: z.string(), to: z.string(), at: timestamp }),
    ),
  })
  .passthrough();
export function page<T extends z.ZodType>(item: T) {
  return z.object({
    items: z.array(item),
    total: z.number(),
    limit: z.number(),
    offset: z.number(),
  });
}
export const riskSchema = z
  .object({
    max_per_venue: decimal,
    max_total_notional: decimal,
    bankroll: decimal,
    max_daily_capital: decimal,
    min_edge: decimal,
    min_profit: decimal,
    max_quote_age_ms: z.number().int().positive(),
    latency_ms: z.number().int().nonnegative(),
    slippage_bps: decimal,
    kill_switch: z.boolean(),
    supported_leagues: z.array(z.string()).min(1),
    fill_model: z.enum(["conservative", "optimistic", "observed"]),
    sizing_mode: z.string(),
  })
  .passthrough();
export const riskResponseSchema = z.object({
  revision: z.number(),
  settings: riskSchema,
});
export const healthSchema = z.object({
  venues: z.array(
    z
      .object({
        venue: z.string(),
        feed: z.string().optional(),
        discovery: z.string().optional(),
        last_book: timestamp.optional(),
        error: z.string().nullable().optional(),
        reconnects: z.number().optional(),
      })
      .passthrough(),
  ),
  workers: z.array(
    z
      .object({
        role: z.string(),
        healthy: z.boolean(),
        heartbeat_age_seconds: z.number(),
      })
      .passthrough(),
  ),
  dependencies: z.object({
    ready: z.boolean(),
    database: z.string(),
    schema: z.string(),
    redis: z.string(),
  }),
});
export const configSchema = z
  .object({
    environment: z.string(),
    data_mode: source,
    trading_mode: z.literal("paper"),
    live_execution_available: z.literal(false),
    build_version: z.string(),
    admin_configured: z.boolean(),
    kalshi_websocket_configured: z.boolean(),
    polymarket_us_websocket_configured: z.boolean(),
    max_monitored_markets_per_venue: z.number(),
  })
  .passthrough();
export const sessionSchema = z
  .object({
    authenticated: z.boolean(),
    csrf: z.string().optional(),
    actor: z.string().optional(),
  })
  .passthrough();
export const statsSchema = z
  .object({
    day: z.string(),
    source,
    label: z.string(),
    active_opportunities: z.number(),
    detected: z.number(),
    qualified: z.number(),
    paper_trades: z.number(),
    fully_hedged: z.number(),
    partial_or_failed: z.number(),
    unhedged: z.number(),
    expected_profit: decimal,
    simulated_locked_profit: decimal,
    settlement_profit: decimal,
    actual_capital: decimal,
    committed_capital: decimal,
    fees: decimal,
    slippage: decimal,
    fill_success_rate: decimal,
    partial_fill_rate: decimal,
    average_net_edge: decimal,
    rejections: z.number(),
    rejection_reasons: z.record(z.string(), z.number()),
    series: z.array(
      z.object({
        hour: z.string(),
        count: z.number(),
        expected: decimal,
        simulated: decimal,
      }),
    ),
    by_league: z.record(
      z.string(),
      z.object({ count: z.number(), profit: decimal }),
    ),
    quote_ages_ms: z.array(z.number()),
    sizes: z.array(decimal),
    data_completeness: z.string(),
    kill_switch: z.boolean(),
    reporting_target: z.number(),
  })
  .passthrough();
export type Opportunity = z.infer<typeof opportunitySchema>;
export type Market = z.infer<typeof marketSchema>;
export type Risk = z.infer<typeof riskSchema>;
export type Stats = z.infer<typeof statsSchema>;
export const objectSchema = z.record(z.string(), z.unknown());
export const eligibilitySchema = z
  .object({
    venue,
    product: z.record(z.string(), z.string()),
    operator_country: z.string(),
    operator_region: z.string(),
    price_access: z.enum(["connected", "disconnected", "unknown"]),
    account_read_access: z.enum(["verified", "unverified", "failed"]),
    order_permission: z.enum(["unverified", "operator_attested", "restricted"]),
    jurisdiction_status: z.enum(["unknown", "operator_attested", "close_only"]),
    open_order_eligible: z.boolean(),
    available_balance: decimal.nullable(),
    balance_observed_at: timestamp.nullable(),
    reasons: z.array(z.string()),
    probe_error: z.string().nullable(),
    live_execution_available: z.literal(false),
  })
  .passthrough();
export const candidateSchema = z
  .object({
    id: z.string(),
    match_id: z.string(),
    event_id: z.string(),
    event: z.string(),
    league: z.string(),
    second_venue: venue,
    first_market_id: z.string(),
    second_market_id: z.string(),
    first_outcome: side,
    second_outcome: side,
    approval_status: z.string(),
    human_reviewed: z.boolean(),
    at: timestamp,
    reasons: z.array(z.string()),
    execution_reasons: z.array(z.string()),
    shadow_reasons: z.array(z.string()),
    shadow_qualified: z.boolean(),
    executable_for_operator: z.boolean(),
    price_basis: z.string(),
    first_ask: decimal.nullable(),
    second_ask: decimal.nullable(),
    available_quantity: decimal,
    first_book_age_ms: z.number().nullable(),
    second_book_age_ms: z.number().nullable(),
    calculation: calculationSchema.nullable(),
    settlement_proof: z
      .object({
        proven: z.boolean(),
        reasons: z.array(z.string()),
        scenarios: z.array(
          z.object({
            direction: side,
            scenario: z.string(),
            combined_payout: decimal.nullable(),
            covered: z.boolean(),
          }),
        ),
      })
      .passthrough(),
    fee_evidence: objectSchema,
    source,
  })
  .passthrough();
export const validationSettingsSchema = z.object({
  enabled: z.boolean(),
  shadow_enabled: z.boolean(),
  shortlist_size: z.number().int(),
  diagnostic_days: z.number().int(),
  selected_match_ids: z.array(z.string()),
  virtual_balance_per_venue: decimal,
  max_shadow_per_leg: decimal,
  max_shadow_daily_trials: z.number().int(),
  max_shadow_daily_loss: decimal,
  unwind_latency_ms: z.number().int(),
  episode_gap_seconds: z.number().int(),
  daily_operating_cost: decimal,
  operating_cost_verified: z.boolean(),
  operating_cost_evidence: z.string(),
});
export const validationSummarySchema = z.object({
  at: timestamp,
  source,
  revision: z.number(),
  settings: validationSettingsSchema,
  collection_started_at: timestamp,
  elapsed_days: decimal,
  diagnostic_window_complete: z.boolean(),
  automatic_live_permission: z.literal(false),
  candidate_pairs: z.number(),
  directions: z.number(),
  fresh_diagnostics: z.number(),
  shadow_qualified_directions: z.number(),
  operator_executable_directions: z.number(),
  rejection_counts: z.record(z.string(), z.number()),
  approval_counts: z.record(z.string(), z.number()),
  shortlist: z.array(candidateSchema),
  distinct_opportunity_episodes: z.number(),
  qualified_observations: z.number(),
  shadow_baseline_trials: z.number(),
  shadow_stress_trials: z.number(),
  shadow_states: z.record(z.string(), z.number()),
  simulated_baseline_net_pnl: decimal,
  baseline_pnl_by_basis: z.record(z.string(), decimal),
  stress_worst_case_pnl: decimal,
  modeled_operating_cost: decimal,
  profit_after_operating_cost: decimal.nullable(),
  operating_cost_status: z.string(),
  capital_lockup_seconds: decimal,
  capital_weighted_lockup_usd_seconds: decimal,
  eligibility: z.array(eligibilitySchema),
  limitation: z.string(),
});
