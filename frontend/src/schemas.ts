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
