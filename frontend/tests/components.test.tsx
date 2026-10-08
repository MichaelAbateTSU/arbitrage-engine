import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge, DataTable, Empty, ErrorBox, Reasons } from "../src/components";
import {
  decimal,
  costComponentSchema,
  focusedValidationSchema,
  eligibilitySchema,
  opportunitySchema,
  riskSchema,
  marketSchema,
} from "../src/schemas";
import { money, percent, time } from "../src/api";
import { ProfitabilityEvidence, SettlementMatrix } from "../src/Validation";
import { FocusedEvidence } from "../src/FocusedEvidence";

describe("fail-closed client schemas", () => {
  it("preserves exact Bitcoin references and unresolved settlement policy", () => {
    const value = {
      id: "kalshi:BTC-fixture",
      venue: "kalshi",
      external_id: "BTC-fixture",
      title: "Synthetic BTC",
      rules_text: "Synthetic rules",
      league: "BTC",
      sport: "crypto",
      participants: ["up", "down"],
      yes_team: "up",
      start_time: "2030-01-01T14:00:00Z",
      status: "open",
      source: "demo",
      rules: {
        period: "15m",
        overtime: null,
        draw: null,
        cancellation: null,
        postponement: null,
        settlement_source: "BRTI",
        payout: "1",
        evidence: {},
      },
      bitcoin: {
        asset: "BTC",
        benchmark: "BRTI",
        window_start: "2030-01-01T14:00:00Z",
        window_end: "2030-01-01T14:15:00Z",
        opening_reference: "85000.00",
        policy: {
          sample_start_offset: null,
          sample_end_offset: null,
          rounding_mode: null,
          revision_deadline_seconds: null,
          missing_data: "no",
          discretionary_settlement: "independent",
          evidence: { missing_data: "Synthetic" },
        },
      },
    };
    expect(marketSchema.parse(value).bitcoin?.opening_reference).toBe(
      "85000.00",
    );
    expect(
      marketSchema.safeParse({
        ...value,
        bitcoin: { ...value.bitcoin, opening_reference: 85000 },
      }).success,
    ).toBe(false);
    expect(
      marketSchema.safeParse({
        ...value,
        bitcoin: { ...value.bitcoin, opening_reference: null },
      }).success,
    ).toBe(true);
  });
  it("rejects floating point financial responses", () => {
    expect(decimal.safeParse(0.5).success).toBe(false);
    expect(decimal.safeParse("0.50").success).toBe(true);
    expect(decimal.safeParse("1E-7").success).toBe(true);
    expect(
      opportunitySchema.safeParse({ calculation: { net_profit: "10" } })
        .success,
    ).toBe(false);
  });
  it("rejects an empty supported universe", () => {
    expect(riskSchema.safeParse({ supported_leagues: [] }).success).toBe(false);
  });
  it("keeps entry sizing distinct and exact with backward-compatible defaults", () => {
    expect(riskSchema.shape.entry_quantity_step.parse(undefined)).toBe("1");
    expect(riskSchema.shape.entry_quantity_step.parse("0.01")).toBe("0.01");
    expect(riskSchema.shape.entry_quantity_step.safeParse(0.01).success).toBe(
      false,
    );
  });
  it("rejects invalid eligibility, float buying power and enabled live execution", () => {
    const value = {
      venue: "polymarket_us",
      product: { name: "Polymarket US" },
      operator_country: "US",
      operator_region: "GA",
      price_access: "connected",
      account_read_access: "verified",
      order_permission: "unverified",
      jurisdiction_status: "unknown",
      open_order_eligible: false,
      available_balance: "17.123456789",
      balance_observed_at: "2026-10-03T03:00:00Z",
      reasons: ["ORDER_PERMISSION_UNVERIFIED"],
      probe_error: null,
      live_execution_available: false,
    };
    expect(eligibilitySchema.safeParse(value).success).toBe(true);
    expect(
      eligibilitySchema.safeParse({ ...value, available_balance: 17.12 })
        .success,
    ).toBe(false);
    expect(
      eligibilitySchema.safeParse({
        ...value,
        order_permission: "verified_by_prices",
      }).success,
    ).toBe(false);
    expect(
      eligibilitySchema.safeParse({ ...value, live_execution_available: true })
        .success,
    ).toBe(false);
  });
});
describe("dashboard evidence and states", () => {
  it("shows conditional size economics without claiming missing costs are verified", () => {
    render(
      <ProfitabilityEvidence
        sizing={{
          objective: "max_net_profit",
          evaluated_sizes: 200,
          qualifying_sizes: 190,
          best_net_quantity: "100",
          best_net_profit: "26.625",
          largest_evaluated_quantity: "200",
          largest_size_net_profit: "24.88",
          target_stopped_search: false,
        }}
        economics={{
          cost_hurdle: "3.375",
          required_net_profit: "1",
          required_gross_profit: "4.375",
          gross_profit_shortfall: "0",
          required_gross_spread_per_contract: "0.04375",
          cost_evidence_complete: false,
          basis: "At observed prices, not a fill.",
        }}
      />,
    );
    expect(screen.getByText(/Best modeled size: 100 contracts/)).toBeVisible();
    expect(
      screen.getByText(/Additional costs remain unverified/),
    ).toBeVisible();
    expect(
      screen.getByText(/do not establish settlement coverage/),
    ).toBeVisible();
  });
  it("shows zero without presenting null as a profit", () => {
    expect(money("0")).toBe("$0.00");
    expect(money(null)).toBe("\u2014");
    expect(percent("0.005")).toBe("0.50%");
    expect(time("2026-09-30T18:30:00Z")).toBe("18:30:00");
  });
  it("renders reason codes in understandable language", () => {
    render(<Reasons reasons={["BOOK_STALE", "UNKNOWN_FEE"]} />);
    expect(screen.getByText("Book stale")).toBeVisible();
    expect(screen.getByText("Unknown fee")).toBeVisible();
  });
  it("does not hide errors as empty success", () => {
    render(<ErrorBox error={new Error("API unavailable")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("API unavailable");
  });
  it("shows an honest empty table", () => {
    render(<DataTable rows={[]} columns={[]} />);
    expect(screen.getByText(/never creates a trade/)).toBeVisible();
  });
  it("encodes user text instead of injecting markup", () => {
    render(<Badge>{"<script>alert(1)</script>"}</Badge>);
    expect(screen.getByText("<script>alert(1)</script>")).toBeVisible();
    render(<Empty title="No signals" />);
    expect(screen.getByText("No signals")).toBeVisible();
  });
  it("does not label an unknown settlement scenario as covered", () => {
    render(
      <SettlementMatrix
        value={{
          proven: false,
          reasons: ["UNKNOWN_CANCELLATION"],
          scenarios: [
            {
              direction: "YES",
              scenario: "cancelled_or_void",
              combined_payout: null,
              covered: false,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Coverage not proven")).toBeVisible();
    expect(screen.getByText("unknown")).toBeVisible();
    expect(screen.getByText("No", { exact: true })).toBeVisible();
  });
  it("shows bounded and unknown scenario payouts without inventing a guarantee", () => {
    render(
      <SettlementMatrix
        value={{
          proven: false,
          reasons: ["UNKNOWN_DISCRETIONARY_SETTLEMENT"],
          scenarios: [
            {
              direction: "NO",
              scenario: "discretionary_settlement",
              combined_payout: null,
              minimum_payout: "0",
              maximum_payout: "2",
              covered: false,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("0 to 2")).toBeVisible();
    expect(screen.getByText("Coverage not proven")).toBeVisible();
  });
  it("preserves the original start and labels blocked coverage as inconclusive", () => {
    const value = focusedValidationSchema.parse({
      families: [],
      family_counts: {},
      family_review_limit: 20,
      focused_pairs: [],
      focus_scope: "diagnostic_only",
      screen_conclusion: "No compatible profile found.",
      cost_evidence: {},
      coverage: {
        tracking_started_at: "2026-10-03T16:30:00Z",
        pair_seconds: { fully_eligible_observed: "0", usable_books: "1.25" },
        pair_count: 1,
        verdict: "inconclusive_no_fully_evidenced_coverage",
        method: "Lower bound, not wall-clock seconds",
        historical_coverage_before_tracking: "unknown_not_reconstructed",
        by_pair: [],
      },
    });
    render(
      <FocusedEvidence
        value={value}
        collectionStarted="2026-10-03T08:03:46Z"
        onReview={() => undefined}
      />,
    );
    expect(screen.getByText(/2026-10-03T08:03:46Z/)).toBeVisible();
    expect(
      screen.getByText(/Inconclusive no fully evidenced coverage/),
    ).toBeVisible();
    expect(screen.getByText(/not tradable candidates/)).toBeVisible();
    expect(screen.getByText("0.0 pair-s")).toBeVisible();
    expect(screen.getByText(/No clock was restarted/)).toBeVisible();
  });
  it("accepts only explicit component statuses and exact decimal amounts", () => {
    const component = {
      status: "not_applicable",
      amount: "0",
      basis: "per_leg",
      execution_path: "Already funded USD test account",
      evidence: "Test source",
      observed_at: "2026-10-03T16:30:00Z",
      expires_at: "2026-10-04T16:30:00Z",
    };
    expect(costComponentSchema.safeParse(component).success).toBe(true);
    expect(
      costComponentSchema.safeParse({ ...component, amount: 0 }).success,
    ).toBe(false);
    expect(
      costComponentSchema.safeParse({ ...component, status: "assumed_free" })
        .success,
    ).toBe(false);
  });
});
