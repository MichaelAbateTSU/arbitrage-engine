import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { z } from "zod";
import { CostEvidence } from "./CostEvidence";
import { FocusedEvidence } from "./FocusedEvidence";
import { api, money, reason, time, useData } from "./api";
import {
  Badge,
  DataTable,
  ErrorBox,
  JsonEvidence,
  Loading,
  Pagination,
  Panel,
  Reasons,
} from "./components";
import {
  candidateSchema,
  executionEconomicsSchema,
  sizingAnalysisSchema,
  eligibilitySchema,
  objectSchema,
  page,
  validationSettingsSchema,
  validationSummarySchema,
} from "./schemas";

type Candidate = z.infer<typeof candidateSchema>;
type Eligibility = z.infer<typeof eligibilitySchema>;
type Settings = z.infer<typeof validationSettingsSchema>;

export function Validation({
  csrf,
  onReview,
}: {
  csrf?: string;
  onReview: (identifier: string) => void;
}) {
  const summary = useData("/validation/summary", validationSummarySchema);
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const query = useData(
    `/validation/candidates?limit=50&offset=${offset}${filter ? `&reason=${encodeURIComponent(filter)}` : ""}`,
    page(candidateSchema),
  );
  const columns: ColumnDef<Candidate>[] = [
    {
      id: "event",
      header: "Pair / direction",
      cell: ({ row }) => (
        <button
          className="event-link"
          onClick={() => setSelected(row.original)}
        >
          <strong>{row.original.event || row.original.match_id}</strong>
          <span>
            Kalshi {row.original.first_outcome} + {row.original.second_venue}{" "}
            {row.original.second_outcome}
          </span>
        </button>
      ),
    },
    {
      accessorKey: "approval_status",
      header: "Approval",
      cell: ({ row }) => (
        <Badge
          tone={
            row.original.approval_status === "approved" ? "success" : "warning"
          }
        >
          {row.original.approval_status}
        </Badge>
      ),
    },
    {
      id: "prices",
      header: "Depth-adjusted asks / BBO",
      cell: ({ row }) => (
        <span>
          {row.original.calculation
            ? `${row.original.calculation.price_one} / ${row.original.calculation.price_two}`
            : "unknown"}
          <small>
            Best asks: {row.original.first_ask ?? "missing"} /{" "}
            {row.original.second_ask ?? "missing"}
          </small>
        </span>
      ),
    },
    { accessorKey: "available_quantity", header: "Observed matched depth" },
    {
      id: "size",
      header: "Priced contracts",
      cell: ({ row }) => row.original.calculation?.quantity ?? "unknown",
    },
    {
      id: "age",
      header: "Book age ms",
      cell: ({ row }) => (
        <span>
          {row.original.first_book_age_ms ?? "missing"} /{" "}
          {row.original.second_book_age_ms ?? "missing"}
        </span>
      ),
    },
    {
      id: "profit",
      header: "Conditional net profit",
      cell: ({ row }) => (
        <span>{money(row.original.calculation?.net_profit)}</span>
      ),
    },
    {
      id: "cost-hurdle",
      header: "Gross profit needed / shortfall",
      cell: ({ row }) =>
        row.original.execution_economics ? (
          <span>
            {money(row.original.execution_economics.required_gross_profit)}
            <small>
              Shortfall:{" "}
              {money(row.original.execution_economics.gross_profit_shortfall)}
            </small>
          </span>
        ) : (
          "unknown"
        ),
    },
    {
      id: "reason",
      header: "Blocking reasons",
      cell: ({ row }) => (
        <Reasons
          reasons={[
            ...new Set([
              ...row.original.reasons,
              ...row.original.execution_reasons,
            ]),
          ]}
        />
      ),
    },
    {
      id: "worst-case",
      header: "All-scenario net floor",
      cell: ({ row }) =>
        row.original.settlement_adjusted_net_profit == null
          ? "Unproven"
          : money(row.original.settlement_adjusted_net_profit),
    },
  ];
  if (summary.isPending) return <Loading />;
  return (
    <>
      <ErrorBox error={summary.error ?? query.error} />
      {summary.data && (
        <FocusedEvidence
          value={summary.data.focused_validation}
          collectionStarted={summary.data.collection_started_at}
          onReview={onReview}
        />
      )}
      <Panel
        title="Why did the candidates not qualify?"
        subtitle="Counts are distinct pairs, not repeated observations. Price math never overrides missing settlement or eligibility evidence."
      >
        <div className="metrics">
          {[
            ["Candidate pairs", summary.data?.candidate_pairs],
            ["Fresh directional diagnostics", summary.data?.fresh_diagnostics],
            [
              "Shadow-qualified directions",
              summary.data?.shadow_qualified_directions,
            ],
            [
              "Operator-executable directions",
              summary.data?.operator_executable_directions,
            ],
          ].map(([label, value]) => (
            <div className="metric" key={String(label)}>
              <div className="metric-label">{label}</div>
              <div className="metric-value">{value ?? 0}</div>
            </div>
          ))}
        </div>
        <div className="actions">
          <select
            aria-label="Validation rejection reason"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All diagnostic directions</option>
            {Object.entries(summary.data?.rejection_counts ?? {}).map(
              ([code, count]) => (
                <option key={code} value={code}>
                  {reason(code)} ({count} pairs)
                </option>
              ),
            )}
          </select>
          <span className="panel-note">
            Updated {time(summary.data?.at)} UTC
          </span>
        </div>
        <DataTable
          rows={query.data?.items ?? []}
          columns={columns}
          empty="Diagnostics are collecting"
        />
        <Pagination
          total={query.data?.total ?? 0}
          offset={offset}
          onChange={setOffset}
        />
      </Panel>
      {selected && (
        <Panel
          title="Directional evidence"
          action={
            <button onClick={() => setSelected(null)}>Close detail</button>
          }
        >
          <p>{selected.price_basis}</p>
          <Reasons reasons={selected.reasons} />
          <h3>Separate account/execution blockers</h3>
          <Reasons reasons={selected.execution_reasons} />
          <ProfitabilityEvidence
            sizing={selected.calculation?.sizing_analysis}
            economics={selected.execution_economics}
          />
          <button
            className="primary"
            onClick={() => onReview(selected.match_id)}
          >
            Review settlement evidence
          </button>
          <JsonEvidence
            title="Matched-size calculation and all consumed depth"
            data={selected.calculation}
          />
          <JsonEvidence
            title="Market-specific fees and additional-cost provenance"
            data={selected.fee_evidence}
          />
          <SettlementMatrix value={selected.settlement_proof} />
          <p>
            All-scenario net floor:{" "}
            {selected.settlement_adjusted_net_profit == null
              ? "Unproven"
              : money(selected.settlement_adjusted_net_profit)}
            . Bounded-scenarios-only net floor:{" "}
            {money(selected.known_scenario_net_floor)}. The latter is not an
            all-scenario guarantee; missing costs and one-leg execution failures
            remain separate.
          </p>
        </Panel>
      )}
      <Panel
        title="Price access is not trading eligibility"
        subtitle="US operator jurisdiction is not inferred from Render's cloud IP. International Polymarket is a different, US-close-only product."
      >
        <div className="health-cards">
          {summary.data?.eligibility.map((value) => (
            <EligibilityCard key={value.venue} value={value} csrf={csrf} />
          ))}
        </div>
      </Panel>
      <Panel
        title="Focused individual settlement review"
        subtitle="Family-first selection is not approval. Historical shortlist IDs are retained in configuration; source/hash changes invalidate individual proofs."
      >
        {summary.data?.shortlist.map((value) => (
          <div className="actions" key={value.match_id}>
            <button
              className="event-link"
              onClick={() => onReview(value.match_id)}
            >
              <strong>{value.event || value.match_id}</strong>
              <span>
                {value.second_venue} · observed matched depth{" "}
                {value.available_quantity}
              </span>
            </button>
            <Badge tone={value.settlement_proof.proven ? "success" : "warning"}>
              {value.settlement_proof.proven
                ? "Scenario proof ready"
                : "Evidence incomplete"}
            </Badge>
          </div>
        ))}
      </Panel>
      {summary.data && (
        <>
          <Panel
            title="7–14-day shadow diagnostic window"
            subtitle="Never automatic permission to trade. Baseline and injected stress results are reported separately."
          >
            <div className="metrics">
              {[
                [
                  "Distinct opportunity episodes",
                  summary.data.distinct_opportunity_episodes,
                ],
                [
                  "Repeated qualified observations",
                  summary.data.qualified_observations,
                ],
                ["Baseline shadow trials", summary.data.shadow_baseline_trials],
                [
                  "Injected hedge-failure trials",
                  summary.data.shadow_stress_trials,
                ],
              ].map(([label, value]) => (
                <div className="metric" key={String(label)}>
                  <div className="metric-label">{label}</div>
                  <div className="metric-value">{value}</div>
                </div>
              ))}
            </div>
            <p>
              Collected {Number(summary.data.elapsed_days).toFixed(2)} of{" "}
              {summary.data.settings.diagnostic_days} days.
            </p>
            <p>
              Baseline hypothetical net P&amp;L:{" "}
              {money(summary.data.simulated_baseline_net_pnl)}. Stress
              worst-case P&amp;L: {money(summary.data.stress_worst_case_pnl)}.
            </p>
            <p>
              Capital lockup: {summary.data.capital_lockup_seconds} aggregate
              trial-seconds ({summary.data.capital_weighted_lockup_usd_seconds}{" "}
              USD-seconds). After operating costs:{" "}
              {money(summary.data.profit_after_operating_cost)} (
              {summary.data.operating_cost_status}).
            </p>
            <Reasons reasons={Object.keys(summary.data.shadow_states)} />
            <JsonEvidence
              title="Unsettled, observed-settlement and failed-hedge P&L separately"
              data={summary.data.baseline_pnl_by_basis}
            />
            <p className="panel-note">{summary.data.limitation}</p>
            <ShadowSettings
              initial={summary.data.settings}
              revision={summary.data.revision}
              csrf={csrf}
            />
          </Panel>
          <CostSettings csrf={csrf} />
        </>
      )}
    </>
  );
}

export function ProfitabilityEvidence({
  sizing,
  economics,
}: {
  sizing?: z.infer<typeof sizingAnalysisSchema> | null;
  economics?: z.infer<typeof executionEconomicsSchema> | null;
}) {
  if (!sizing && !economics)
    return <p>Profitability sizing evidence unavailable.</p>;
  return (
    <section className="evidence">
      <h3>Size and cost hurdle</h3>
      {sizing && (
        <p>
          Objective: {reason(sizing.objective)}. Evaluated{" "}
          {sizing.evaluated_sizes} sizes; {sizing.qualifying_sizes} passed the
          price thresholds. Best modeled size: {sizing.best_net_quantity}{" "}
          contracts, net {money(sizing.best_net_profit)}. Largest evaluated
          size: {sizing.largest_evaluated_quantity} contracts, net{" "}
          {money(sizing.largest_size_net_profit)}.
          {sizing.target_stopped_search &&
            " Search stopped at the requested profit target."}
        </p>
      )}
      {economics && (
        <>
          <p>
            Fees, buffers and modeled additional costs:{" "}
            {money(economics.cost_hurdle)}. Required gross profit:{" "}
            {money(economics.required_gross_profit)}. Gross profit shortfall:{" "}
            {money(economics.gross_profit_shortfall)}.
          </p>
          {!economics.cost_evidence_complete && (
            <p>
              Additional costs remain unverified; this hurdle is incomplete.
            </p>
          )}
          <p>{economics.basis}</p>
        </>
      )}
      <p>
        Price thresholds do not establish settlement coverage or account
        permission.
      </p>
    </section>
  );
}

export function SettlementMatrix({
  value,
}: {
  value: Candidate["settlement_proof"];
}) {
  return (
    <div>
      <h3>Combined payouts by scenario</h3>
      <Badge tone={value.proven ? "success" : "warning"}>
        {value.proven ? "Normalized coverage proven" : "Coverage not proven"}
      </Badge>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Kalshi direction</th>
              <th>Scenario</th>
              <th>Combined payout bounds</th>
              <th>Covered</th>
            </tr>
          </thead>
          <tbody>
            {value.scenarios.map((row) => (
              <tr key={`${row.direction}:${row.scenario}`}>
                <td>{row.direction}</td>
                <td>{row.scenario}</td>
                <td>
                  {row.minimum_payout === undefined
                    ? (row.combined_payout ?? "unknown")
                    : `${row.minimum_payout ?? "unknown"} to ${row.maximum_payout ?? "unknown"}`}
                </td>
                <td>{row.covered ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <JsonEvidence
        title="Deadlines, resolution policies and exact rule-version proof"
        data={value}
      />
    </div>
  );
}

function EligibilityCard({
  value,
  csrf,
}: {
  value: Eligibility;
  csrf?: string;
}) {
  const [evidence, setEvidence] = useState("");
  const [checks, setChecks] = useState({
    jurisdiction_confirmed: false,
    kyc_confirmed: false,
    order_permission_confirmed: false,
    market_restrictions_reviewed: false,
  });
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`/validation/eligibility/${value.venue}`, eligibilitySchema, {
        method: "PUT",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify({
          ...checks,
          evidence,
          expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
        }),
      });
      await client.invalidateQueries();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught
          : new Error("Eligibility attestation failed"),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rules-card">
      <h3>{value.product.name}</h3>
      <p>
        {value.product.market_api} · operator {value.operator_country}/
        {value.operator_region}
      </p>
      <p>
        Prices: {value.price_access}. Account read: {value.account_read_access}.
        Order permission: {value.order_permission}. Jurisdiction:{" "}
        {value.jurisdiction_status}.
      </p>
      <p>
        Available account buying power: {money(value.available_balance)}.
        Observed {time(value.balance_observed_at)} UTC. This is not
        KYC/order-permission proof.
      </p>
      <Reasons reasons={value.reasons} />
      <p>
        API key trading scope: {value.key_trading_scope ?? "unverified"}. Key
        location attestation: {value.key_region_status ?? "unknown"}. Key
        binding: {value.key_binding_status ?? "unverified"}.
        {value.permission_evidence_limitation}
      </p>
      {value.key_scope_probe_error && (
        <Reasons reasons={[value.key_scope_probe_error]} />
      )}
      <details>
        <summary>
          Independent account eligibility attestation (expires after 7 days)
        </summary>
        {Object.entries(checks).map(([key, checked]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={checked}
              onChange={(event) =>
                setChecks({
                  ...checks,
                  [key]: event.target.checked,
                })
              }
            />
            {reason(key)}
          </label>
        ))}
        <label>
          Evidence reference; never paste credentials
          <textarea
            value={evidence}
            onChange={(event) => setEvidence(event.target.value)}
          />
        </label>
        <ErrorBox error={error} />
        <button
          disabled={
            !csrf ||
            busy ||
            !evidence.trim() ||
            value.jurisdiction_status === "close_only"
          }
          onClick={() => void save()}
        >
          Save audited attestation
        </button>
      </details>
    </div>
  );
}

function ShadowSettings({
  initial,
  revision,
  csrf,
}: {
  initial: Settings;
  revision: number;
  csrf?: string;
}) {
  const [settings, setSettings] = useState(JSON.stringify(initial, null, 2));
  const [note, setNote] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const parsed: unknown = JSON.parse(settings);
      const value = validationSettingsSchema.parse(parsed);
      await api("/validation/configuration", objectSchema, {
        method: "PUT",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify({ revision, settings: value, reason: note }),
      });
      await client.invalidateQueries();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught
          : new Error("Shadow configuration failed"),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>
        Audited virtual-balance, failure-model and operating-cost settings
      </summary>
      <p>
        Changes affect hypothetical trials only. Keep the original
        profit/freshness thresholds.
      </p>
      <label>
        Shadow settings JSON
        <textarea
          rows={12}
          value={settings}
          onChange={(event) => setSettings(event.target.value)}
        />
      </label>
      <label>
        Change reason{" "}
        <input value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      <ErrorBox error={error} />
      <button
        disabled={!csrf || busy || !note.trim()}
        onClick={() => void save()}
      >
        Save shadow settings
      </button>
    </details>
  );
}

function CostSettings({ csrf }: { csrf?: string }) {
  return <CostEvidence csrf={csrf} />;
}
