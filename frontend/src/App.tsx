import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import {
  Activity,
  BarChart3,
  CircleHelp,
  FileSearch,
  Gauge,
  Layers3,
  LockKeyhole,
  Radio,
  Settings2,
  ShieldCheck,
  TerminalSquare,
  X,
  Menu,
  ArrowUpRight,
  Download,
} from "lucide-react";
import { z } from "zod";
import { Validation } from "./Validation";
import { api, money, percent, time, useData } from "./api";
import {
  Badge,
  BookView,
  Chart,
  DataTable,
  Empty,
  ErrorBox,
  JsonEvidence,
  Loading,
  Pagination,
  Panel,
  Reasons,
} from "./components";
import {
  configSchema,
  healthSchema,
  matchDetailSchema,
  matchSchema,
  objectSchema,
  opportunitySchema,
  page,
  riskResponseSchema,
  riskSchema,
  sessionSchema,
  statsSchema,
  tradeSchema,
  type Opportunity,
  type Stats,
  type Risk,
  type Market,
} from "./schemas";

const pages = [
  ["Overview", Gauge],
  ["Opportunities", Radio],
  ["Opportunity validation", ShieldCheck],
  ["Market matching", Layers3],
  ["Paper trading", FileSearch],
  ["Analytics", BarChart3],
  ["System health", Activity],
  ["Settings", Settings2],
] as const;
type PageName = (typeof pages)[number][0];

function Dialog({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} onCancel={close} className="dialog">
      <div className="dialog-heading">
        <h2>{title}</h2>
        <button aria-label="Close dialog" onClick={close}>
          <X size={18} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function App() {
  const [current, setCurrent] = useState<PageName>("Overview");
  const [mobileNav, setMobileNav] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState<Error | null>(null);
  const [connection, setConnection] = useState("connecting");
  const [busy, setBusy] = useState(false);
  const [reviewMatch, setReviewMatch] = useState<string | null>(null);
  const config = useData("/system/configuration", configSchema);
  const session = useData("/auth/session", sessionSchema);
  const client = useQueryClient();
  const csrf = session.data?.csrf;
  useEffect(() => {
    const stream = new EventSource("/api/v1/stream");
    stream.onopen = () => setConnection("connected");
    stream.onerror = () => setConnection("reconnecting");
    stream.addEventListener("update", () => {
      setConnection("connected");
      void client.invalidateQueries({
        predicate: (q) => !String(q.queryKey[0]).startsWith("/auth"),
      });
    });
    stream.addEventListener("auth_required", () =>
      setConnection("authentication required"),
    );
    return () => stream.close();
  }, [client, session.data?.authenticated]);
  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setLoginError(null);
    try {
      await api("/auth/login", sessionSchema, {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setPassword("");
      setShowLogin(false);
      await client.invalidateQueries();
    } catch (error) {
      setLoginError(error instanceof Error ? error : new Error("Login failed"));
    } finally {
      setBusy(false);
    }
  }
  async function signOut() {
    setLoginError(null);
    try {
      await api("/auth/logout", sessionSchema, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf ?? "" },
      });
      await client.invalidateQueries();
    } catch (error) {
      setLoginError(
        error instanceof Error ? error : new Error("Logout failed"),
      );
    }
  }
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "show" : ""}`}>
        <a
          className="brand"
          href="#"
          onClick={(event) => event.preventDefault()}
        >
          <div className="brand-mark">
            <Layers3 size={21} />
          </div>
          <div>
            ARBITRAGE<span>INTELLIGENCE</span>
          </div>
        </a>
        <div className="workspace-label">RESEARCH WORKSPACE</div>
        <nav aria-label="Main navigation">
          {pages.map(([name, Icon]) => (
            <button
              key={name}
              className={`nav-item ${current === name ? "active" : ""}`}
              onClick={() => {
                setCurrent(name);
                setMobileNav(false);
              }}
            >
              <Icon size={18} />
              {name}
              {current === name && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="safety-seal">
            <ShieldCheck size={20} />
            <div>
              Read-only by design<span>No real-money orders</span>
            </div>
          </div>
          <div className="build">
            <TerminalSquare size={13} />
            {config.data?.build_version ?? "Connecting to API"}
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobileNav(!mobileNav)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <span className="separator">/</span>
            <strong>{current}</strong>
          </div>
          <div className="topbar-right">
            <span
              className={`connection ${connection === "connected" ? "online" : ""}`}
            >
              <i />
              {connection === "connected" ? "Stream connected" : connection}
            </span>
            <Badge
              tone={config.data?.data_mode === "demo" ? "warning" : "info"}
            >
              {config.data?.data_mode === "demo"
                ? "DEMO DATA"
                : config.data
                  ? "PUBLIC DATA"
                  : "OFFLINE"}
            </Badge>
            <button
              className="operator"
              onClick={() =>
                session.data?.authenticated
                  ? void signOut()
                  : setShowLogin(true)
              }
            >
              <LockKeyhole size={14} />
              {session.data?.authenticated ? "Sign out" : "Operator login"}
            </button>
          </div>
        </header>
        <main id="main-content">
          <div className="page-heading">
            <div className="eyebrow">SPORTS PREDICTION MARKETS</div>
            <h1>{current}</h1>
            <p>
              {
                {
                  Overview:
                    "From apparent price gaps to evidence-backed opportunities.",
                  Opportunities:
                    "Executable depth, verified rules, and conservative costs.",
                  "Opportunity validation":
                    "Explain every blocker, prove scenario coverage, and measure distinct shadow episodes.",
                  "Market matching":
                    "Economic equivalence is a requirement, never an assumption.",
                  "Paper trading":
                    "Two independent legs. Hypothetical execution. Explicit leg risk.",
                  Analytics: "Measure execution quality, not promised returns.",
                  "System health":
                    "Application readiness and market-data health are separate.",
                  Settings:
                    "Audited, server-enforced risk limits. Live execution is unavailable.",
                }[current]
              }
            </p>
          </div>
          <div className="disclaimer">
            <ShieldCheck size={16} />
            <span>
              <strong>Paper only.</strong>{" "}
              {config.data?.data_mode === "demo"
                ? "Synthetic demonstration data. Not observed exchange opportunities."
                : "Theoretical and paper results are not actual earnings or guaranteed profit."}
            </span>
          </div>
          <ErrorBox error={config.error ?? loginError} />
          {current === "Overview" && <Overview />}
          {current === "Opportunities" && <Opportunities />}
          {current === "Opportunity validation" && (
            <Validation
              csrf={csrf}
              onReview={(identifier) => {
                setReviewMatch(identifier);
                setCurrent("Market matching");
              }}
            />
          )}
          {current === "Market matching" && (
            <Matching csrf={csrf} initialMatch={reviewMatch} />
          )}
          {current === "Paper trading" && <Paper csrf={csrf} />}
          {current === "Analytics" && <Analytics />}
          {current === "System health" && <Health />}
          {current === "Settings" && <RiskSettings csrf={csrf} />}
          <footer>
            <CircleHelp size={13} />
            Prices use executable asks. Fees and modeled costs are included. No
            annualized projections.<span>UTC reporting · USD</span>
          </footer>
        </main>
      </div>
      {showLogin && (
        <Dialog
          title="Operator authentication"
          close={() => setShowLogin(false)}
        >
          <form onSubmit={(event) => void signIn(event)} className="form">
            <p>
              Administrative actions require your configured operator password.
              There is no default production password.
            </p>
            <label>
              Password
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoFocus
                required
              />
            </label>
            <ErrorBox error={loginError} />
            <button className="primary" disabled={busy}>
              {busy ? "Authenticating..." : "Sign in"}
            </button>
          </form>
        </Dialog>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {label}
        <ArrowUpRight size={15} />
      </div>
      <div className={`metric-value ${tone ?? ""}`}>{value}</div>
      <div className="metric-detail">{detail}</div>
    </div>
  );
}
function Metrics({ data }: { data: Stats }) {
  return (
    <div className="metrics">
      <Metric
        label="Active qualified signals"
        value={String(data.active_opportunities)}
        detail={`${data.qualified} qualified observations today`}
      />
      <Metric
        label="Paper locked result"
        value={money(data.simulated_locked_profit)}
        detail="Hypothetical · not settled earnings"
        tone={Number(data.simulated_locked_profit) > 0 ? "positive" : ""}
      />
      <Metric
        label="Hedged paper trades"
        value={`${data.fully_hedged} / ${data.paper_trades}`}
        detail={`${percent(data.fill_success_rate)} fully hedged`}
      />
      <Metric
        label="Capital committed"
        value={money(data.committed_capital)}
        detail="Held until observed settlement"
      />
    </div>
  );
}
function Overview() {
  const stats = useData("/opportunities/stats", statsSchema);
  const health = useData("/system/health", healthSchema);
  const opportunities = useData(
    "/opportunities?limit=5",
    page(opportunitySchema),
  );
  const [selected, setSelected] = useState<Opportunity | null>(null);
  if (stats.isPending) return <Loading />;
  if (!stats.data) return <ErrorBox error={stats.error} />;
  const data = stats.data;
  return (
    <>
      <ErrorBox error={stats.error} />
      <Metrics data={data} />
      <div className="two-column">
        <Panel
          title="Qualified observations"
          subtitle={`${data.day} · UTC · observations can repeat a signal`}
          action={<Badge tone="info">TODAY</Badge>}
        >
          <Chart series={data.series} mode="count" />
        </Panel>
        <Panel
          title="Theory vs. paper"
          subtitle="Dashed: expected · Solid: simulated locked result"
        >
          <Chart series={data.series} mode="profit" />
        </Panel>
      </div>
      <Panel
        title="Latest calculation evidence"
        subtitle="Select a row to inspect both books, rules, costs and rejection reasons."
      >
        <DataTable
          rows={opportunities.data?.items ?? []}
          columns={opportunityColumns(setSelected)}
        />
      </Panel>
      <div className="two-column">
        <Panel
          title="Venue connectivity"
          subtitle="REST polling is explicitly distinguished from streaming"
        >
          <ErrorBox error={health.error} />
          <div className="health-list">
            {health.data?.venues.map((x) => (
              <div key={x.venue}>
                <strong>{venueName(x.venue)}</strong>
                <span>{x.feed?.replaceAll("_", " ") ?? "No feed"}</span>
                <Badge tone={x.error ? "danger" : "info"}>
                  {x.error ?? x.discovery ?? "unknown"}
                </Badge>
              </div>
            ))}
          </div>
        </Panel>
        <Panel
          title="Validation funnel"
          subtitle="No forced trades to reach the five-per-day experiment"
        >
          <div className="funnel">
            {[
              ["Calculated observations", data.detected],
              ["Qualified observations", data.qualified],
              ["Paper intents", data.paper_trades],
              ["Fully hedged", data.fully_hedged],
              ["Rejected decisions", data.rejections],
            ].map(([label, number]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{number}</strong>
              </div>
            ))}
          </div>
          <p className="panel-note">Coverage: {data.data_completeness}</p>
          {data.kill_switch && (
            <div className="warning-strip">
              Paper kill switch active. Scanner continues; new trades are
              blocked.
            </div>
          )}
        </Panel>
      </div>
      {selected && (
        <Dialog title="Calculation evidence" close={() => setSelected(null)}>
          <OpportunityDetail value={selected} />
        </Dialog>
      )}
    </>
  );
}
function venueName(value: string) {
  return (
    {
      kalshi: "Kalshi",
      polymarket_us: "Polymarket US",
      polymarket_international: "Polymarket International",
    }[value] ?? value
  );
}

export function opportunityColumns(
  select: (op: Opportunity) => void,
): ColumnDef<Opportunity>[] {
  return [
    {
      accessorKey: "league",
      header: "League",
      cell: ({ row }) => <Badge>{row.original.league}</Badge>,
    },
    {
      accessorKey: "event",
      header: "Event / observed",
      cell: ({ row }) => (
        <button className="event-link" onClick={() => select(row.original)}>
          <strong>{row.original.event}</strong>
          <span>
            {time(row.original.detected_at)} UTC data ·{" "}
            {venueName(row.original.second_venue)}
          </span>
        </button>
      ),
    },
    {
      id: "direction",
      header: "Complementary legs",
      cell: ({ row }) => (
        <div className="leg-prices">
          <span>
            K {row.original.first_outcome}{" "}
            <b>{money(row.original.calculation.price_one)}</b>
          </span>
          <span>
            P {row.original.second_outcome}{" "}
            <b>{money(row.original.calculation.price_two)}</b>
          </span>
        </div>
      ),
    },
    {
      id: "size",
      header: "Contracts",
      accessorFn: (row) => Number(row.calculation.quantity),
      cell: ({ row }) =>
        Number(row.original.calculation.quantity).toLocaleString(),
    },
    {
      id: "profit",
      header: "Est. net",
      accessorFn: (row) => Number(row.calculation.net_profit),
      cell: ({ row }) => (
        <strong
          className={
            row.original.risk_status === "qualified" ? "positive" : "muted"
          }
        >
          {money(row.original.calculation.net_profit)}
        </strong>
      ),
    },
    {
      id: "edge",
      header: "Net return",
      accessorFn: (row) => Number(row.calculation.net_return),
      cell: ({ row }) => percent(row.original.calculation.net_return),
    },
    {
      accessorKey: "quote_age_ms",
      header: "Quote age",
      cell: ({ row }) => `${row.original.quote_age_ms} ms`,
    },
    {
      accessorKey: "risk_status",
      header: "Decision",
      cell: ({ row }) => (
        <Badge
          tone={
            row.original.risk_status === "qualified" ? "success" : "warning"
          }
        >
          {row.original.risk_status}
        </Badge>
      ),
    },
  ];
}
function Opportunities() {
  const [league, setLeague] = useState("");
  const [minProfit, setMinProfit] = useState("");
  const [active, setActive] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Opportunity | null>(null);
  const params = new URLSearchParams({
    limit: "50",
    offset: String(offset),
    active: String(active),
  });
  if (league) params.set("league", league);
  if (minProfit) params.set("min_profit", minProfit);
  const query = useData(`/opportunities?${params}`, page(opportunitySchema));
  return (
    <>
      <Panel
        title="Opportunity observations"
        subtitle="Qualified means all gates passed at detection. Expiry is not proof of a fill."
        action={<Badge>{query.data?.total ?? 0} records</Badge>}
      >
        <div className="filters">
          <label>
            League
            <select
              value={league}
              onChange={(e) => {
                setLeague(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All leagues</option>
              {[
                "NFL",
                "NBA",
                "MLB",
                "NHL",
                "NCAAF",
                "NCAAB",
                "EPL",
                "ATP",
                "WTA",
                "BTC",
              ].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            Minimum net profit
            <input
              type="number"
              min="0"
              step="0.1"
              placeholder="USD"
              value={minProfit}
              onChange={(e) => {
                setMinProfit(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => {
                setActive(e.target.checked);
                setOffset(0);
              }}
            />
            Unexpired qualified only
          </label>
        </div>
        <ErrorBox error={query.error} />
        {query.isPending ? (
          <Loading />
        ) : (
          <DataTable
            rows={query.data?.items ?? []}
            columns={opportunityColumns(setSelected)}
            empty="No qualifying opportunity observations"
          />
        )}
        <Pagination
          total={query.data?.total ?? 0}
          offset={offset}
          onChange={setOffset}
        />
      </Panel>
      {selected && (
        <Dialog title="Opportunity calculation" close={() => setSelected(null)}>
          <OpportunityDetail value={selected} />
        </Dialog>
      )}
    </>
  );
}
function OpportunityDetail({ value }: { value: Opportunity }) {
  const c = value.calculation;
  return (
    <div className="detail-content">
      <div className="detail-title">
        <h3>{value.event}</h3>
        <Badge tone={value.risk_status === "qualified" ? "success" : "warning"}>
          {value.risk_status}
        </Badge>
      </div>
      <Reasons reasons={value.reasons} />
      <div className="detail-grid">
        {[
          ["Matched quantity", c.quantity],
          ["Kalshi capital", money(c.cost_one)],
          ["Polymarket capital", money(c.cost_two)],
          ["Fees / Kalshi", money(c.fee_one)],
          ["Fees / Polymarket", money(c.fee_two)],
          ["Slippage estimate", money(c.slippage)],
          ["Latency safety buffer", money(c.safety_buffer)],
          ["Conditional gross payout", money(c.payout)],
          ["Expected net profit", money(c.net_profit)],
          ["Return on gross cost", percent(c.net_return)],
          ["Unused Kalshi allocation", money(c.unused_one)],
          ["Unused Poly allocation", money(c.unused_two)],
          ["Binding constraint", c.binding_constraint],
          ["Match confidence", percent(value.confidence)],
          ["Detected", time(value.detected_at)],
          ["Expires", time(value.expires_at)],
        ].map(([label, content]) => (
          <div key={label}>
            <small>{label}</small>
            <strong>{content}</strong>
          </div>
        ))}
      </div>
      <p className="panel-note">
        Equal contract quantity hedges the outcome. Exact equal-dollar
        allocations generally do not. Gross payout is conditional on validated
        contract settlement semantics.
      </p>
      <div className="two-column">
        <BookView
          book={value.inputs.first_book}
          title={`Kalshi ${value.first_outcome}`}
        />
        <BookView
          book={value.inputs.second_book}
          title={`${venueName(value.second_venue)} ${value.second_outcome}`}
        />
      </div>
      <div className="two-column">
        <RulesView market={value.inputs.first_market} />
        <RulesView market={value.inputs.second_market} />
      </div>
      <JsonEvidence
        data={{ calculation: c, inputs: value.inputs }}
        title="Depth consumed, exact decimal math and immutable inputs"
      />
    </div>
  );
}
function RulesView({ market }: { market: Market }) {
  return (
    <div className="rules-card">
      <h3>{venueName(market.venue)}</h3>
      <small>{market.external_id}</small>
      <p>{market.rules_text || "No official rules were retrieved."}</p>
      {market.bitcoin && (
        <>
          <p>
            BTC 15-minute BRTI window: {time(market.bitcoin.window_start)} to{" "}
            {time(market.bitcoin.window_end)} UTC. Opening reference:{" "}
            {market.bitcoin.opening_reference ?? "not yet published"}. YES means
            Up, including an equal closing price. Normal agreement is not
            settlement approval.
          </p>
          <JsonEvidence
            title="Bitcoin window and exceptional settlement evidence"
            data={market.bitcoin}
          />
        </>
      )}
      <JsonEvidence
        title="Normalized settlement evidence"
        data={market.rules}
      />
    </div>
  );
}

function Matching({
  csrf,
  initialMatch,
}: {
  csrf?: string;
  initialMatch?: string | null;
}) {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(initialMatch ?? null);
  const query = useData(
    `/matches?limit=50&offset=${offset}${status ? `&status=${status}` : ""}`,
    page(matchSchema),
  );
  const columns: ColumnDef<z.infer<typeof matchSchema>>[] = [
    {
      accessorKey: "first_market_id",
      header: "Kalshi contract",
      cell: ({ row }) => (
        <button
          className="event-link"
          onClick={() => setSelected(row.original.id)}
        >
          <strong>{row.original.first_market_id.split(":")[1]}</strong>
          <span>Inspect contract and rule evidence</span>
        </button>
      ),
    },
    { accessorKey: "second_market_id", header: "Polymarket contract" },
    {
      accessorKey: "status",
      header: "Match decision",
      cell: ({ row }) => (
        <Badge
          tone={row.original.status === "approved" ? "success" : "warning"}
        >
          {row.original.status}
        </Badge>
      ),
    },
    {
      accessorKey: "confidence",
      header: "Confidence",
      cell: ({ row }) => percent(row.original.confidence),
    },
    {
      id: "reasons",
      header: "Deterministic findings",
      cell: ({ row }) => <Reasons reasons={row.original.reasons} />,
    },
  ];
  return (
    <>
      <Panel
        title="Contract graph"
        subtitle="Approval binds exact rule versions. AI cannot override deterministic checks."
        action={
          <select
            aria-label="Match status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All decisions</option>
            <option value="review">Needs review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        }
      >
        <ErrorBox error={query.error} />
        {query.isPending ? (
          <Loading />
        ) : (
          <DataTable rows={query.data?.items ?? []} columns={columns} />
        )}
        <Pagination
          total={query.data?.total ?? 0}
          offset={offset}
          onChange={setOffset}
        />
      </Panel>
      {selected && (
        <Dialog
          title="Contract equivalence review"
          close={() => setSelected(null)}
        >
          <MatchReview identifier={selected} csrf={csrf} />
        </Dialog>
      )}
    </>
  );
}
function MatchReview({
  identifier,
  csrf,
}: {
  identifier: string;
  csrf?: string;
}) {
  const data = useData(`/matches/${identifier}`, matchDetailSchema);
  const [note, setNote] = useState("");
  const [scenarioAcknowledged, setScenarioAcknowledged] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  async function review(action: string) {
    if (!data.data) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/matches/${identifier}/${action}`, matchSchema, {
        method: "POST",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify({
          expected_first_rules_hash: data.data.first_rules_hash,
          expected_second_rules_hash: data.data.second_rules_hash,
          note,
          scenario_coverage_acknowledged: scenarioAcknowledged,
        }),
      });
      await client.invalidateQueries();
    } catch (value) {
      setError(value instanceof Error ? value : new Error("Review failed"));
    } finally {
      setBusy(false);
    }
  }
  if (!data.data)
    return data.isPending ? <Loading /> : <ErrorBox error={data.error} />;
  return (
    <div className="detail-content">
      <Reasons reasons={data.data.reasons} />
      <div className="two-column">
        <RulesView market={data.data.first_market} />
        <RulesView market={data.data.second_market} />
      </div>
      <p className="panel-note">
        Unknown fields must be documented before approval. Known
        incompatibilities cannot be overridden by this button.
      </p>
      <JsonEvidence
        title="Scenario payout coverage, deadlines and resolution proof"
        data={data.data.settlement_proof}
      />
      <label>
        <input
          type="checkbox"
          checked={scenarioAcknowledged}
          onChange={(event) => setScenarioAcknowledged(event.target.checked)}
        />
        I independently verified the combined payout for every relevant scenario
        and the source evidence.
      </label>
      <label>
        Review note
        <textarea value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <ErrorBox error={error} />
      <div className="actions">
        <button
          className="primary"
          disabled={!csrf || busy || !scenarioAcknowledged || !note.trim()}
          onClick={() => void review("approve")}
        >
          Approve verified pair
        </button>
        <button disabled={!csrf || busy} onClick={() => void review("reject")}>
          Reject
        </button>
        <button disabled={!csrf || busy} onClick={() => void review("rematch")}>
          Re-run gates
        </button>
      </div>
      <JsonEvidence title="Review audit history" data={data.data.reviews} />
      <NormalizationEditor
        market={data.data.first_market}
        hash={data.data.first_rules_hash}
        csrf={csrf}
      />
      <NormalizationEditor
        market={data.data.second_market}
        hash={data.data.second_rules_hash}
        csrf={csrf}
      />
    </div>
  );
}
function NormalizationEditor({
  market,
  hash,
  csrf,
}: {
  market: Market;
  hash: string;
  csrf?: string;
}) {
  const [text, setText] = useState(
    JSON.stringify(
      {
        expected_rules_hash: hash,
        participants: market.participants,
        yes_team: market.yes_team,
        start_time: market.start_time,
        rules: market.rules,
        ...(market.bitcoin ? { bitcoin_policy: market.bitcoin.policy } : {}),
        evidence: market.rules.evidence,
        note: "Cite official contract terms and scheduled start source for every required field.",
      },
      null,
      2,
    ),
  );
  const [error, setError] = useState<Error | null>(null);
  const [saved, setSaved] = useState(false);
  const client = useQueryClient();
  async function save() {
    setError(null);
    setSaved(false);
    try {
      const body: unknown = JSON.parse(text);
      await api(
        `/markets/${encodeURIComponent(market.id)}/normalization`,
        objectSchema,
        {
          method: "PUT",
          body: JSON.stringify(body),
          headers: { "X-CSRF-Token": csrf ?? "" },
        },
      );
      setSaved(true);
      await client.invalidateQueries();
    } catch (value) {
      setError(
        value instanceof Error ? value : new Error("Normalization failed"),
      );
    }
  }
  return (
    <details className="evidence">
      <summary>Document missing fields: {venueName(market.venue)}</summary>
      <p className="panel-note">
        Advanced operator review. Evidence must cite each identity/rule field;
        do not infer missing policies. Saving creates a new rule version and
        re-runs matching.
      </p>
      <textarea
        className="json-editor"
        aria-label={`${venueName(market.venue)} normalization evidence`}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <ErrorBox error={error} />
      {saved && <Badge tone="success">New specification saved</Badge>}
      <button disabled={!csrf} onClick={() => void save()}>
        Save documented specification
      </button>
    </details>
  );
}

function Paper({ csrf }: { csrf?: string }) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<z.infer<typeof tradeSchema> | null>(
    null,
  );
  const [replay, setReplay] = useState<unknown>(null);
  const [error, setError] = useState<Error | null>(null);
  const query = useData(
    `/paper-trades?limit=50&offset=${offset}`,
    page(tradeSchema),
  );
  const stats = useData("/paper-trades/stats", statsSchema);
  const columns: ColumnDef<z.infer<typeof tradeSchema>>[] = [
    { accessorKey: "league", header: "League" },
    {
      accessorKey: "id",
      header: "Paper intent",
      cell: ({ row }) => (
        <button
          className="event-link"
          onClick={() => {
            setSelected(row.original);
            setReplay(null);
            setError(null);
          }}
        >
          <strong>{row.original.id.slice(0, 12)}</strong>
          <span>
            {time(row.original.decision_at)} · {row.original.model}
          </span>
        </button>
      ),
    },
    {
      accessorKey: "state",
      header: "State",
      cell: ({ row }) => (
        <Badge
          tone={
            row.original.state === "HEDGED"
              ? "success"
              : row.original.state === "UNHEDGED"
                ? "danger"
                : "neutral"
          }
        >
          {row.original.state}
        </Badge>
      ),
    },
    {
      id: "fills",
      header: "Matched / intended",
      cell: ({ row }) =>
        `${row.original.first.quantity} / ${row.original.second.quantity} of ${row.original.quantity}`,
    },
    {
      accessorKey: "expected_profit",
      header: "Expected",
      cell: ({ row }) => money(row.original.expected_profit),
    },
    {
      accessorKey: "locked_profit",
      header: "Simulated locked",
      cell: ({ row }) => money(row.original.locked_profit),
    },
    {
      accessorKey: "unhedged_quantity",
      header: "Unhedged",
      cell: ({ row }) => (
        <span
          className={Number(row.original.unhedged_quantity) ? "negative" : ""}
        >
          {row.original.unhedged_quantity}
        </span>
      ),
    },
  ];
  async function runReplay() {
    if (!selected) return;
    setError(null);
    try {
      setReplay(
        await api(`/paper-trades/${selected.id}/replay`, objectSchema, {
          method: "POST",
          headers: { "X-CSRF-Token": csrf ?? "" },
        }),
      );
    } catch (value) {
      setError(value instanceof Error ? value : new Error("Replay failed"));
    }
  }
  return (
    <>
      {stats.data && <Metrics data={stats.data} />}
      <Panel
        title="Persisted paper execution"
        subtitle="Conservative fills require fresh post-latency observations and protected limit prices."
      >
        <ErrorBox error={query.error} />
        {query.isPending ? (
          <Loading />
        ) : (
          <DataTable
            rows={query.data?.items ?? []}
            columns={columns}
            empty="No paper trades yet"
          />
        )}
        <Pagination
          total={query.data?.total ?? 0}
          offset={offset}
          onChange={setOffset}
        />
      </Panel>
      {selected && (
        <Dialog
          title="Paper lifecycle evidence"
          close={() => setSelected(null)}
        >
          <div className="detail-content">
            <Badge
              tone={selected.unhedged_quantity !== "0" ? "warning" : "info"}
            >
              {selected.state}
            </Badge>
            <div className="detail-grid">
              {[
                ["Kalshi fill", selected.first.quantity],
                ["Poly fill", selected.second.quantity],
                ["Kalshi fill price", money(selected.first.price)],
                ["Poly fill price", money(selected.second.price)],
                ["Settlement result", money(selected.settlement_profit)],
                ["Submission due", time(selected.due_at)],
              ].map(([label, value]) => (
                <div key={label}>
                  <small>{label}</small>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            {selected.failure_reason && (
              <Reasons reasons={[selected.failure_reason]} />
            )}
            <JsonEvidence
              title="Append-only transition evidence and fills"
              data={selected}
            />
            <button disabled={!csrf} onClick={() => void runReplay()}>
              Replay retained book observations
            </button>
            <ErrorBox error={error} />
            {replay !== null && (
              <JsonEvidence
                title="Replay result (not a new paper position)"
                data={replay}
              />
            )}
          </div>
        </Dialog>
      )}
    </>
  );
}

function Analytics() {
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10));
  const query = useData(`/analytics?day=${day}`, statsSchema);
  function exportReport() {
    if (!query.data) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(query.data, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `paper-report-${day}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <>
      <div className="filters">
        <label>
          Reporting day (UTC)
          <input
            type="date"
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
        </label>
        <button disabled={!query.data} onClick={exportReport}>
          <Download size={14} />
          Export daily evidence
        </button>
      </div>
      <ErrorBox error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <>
            <Metrics data={query.data} />
            <div className="two-column">
              <Panel
                title="Expected vs. simulated result"
                subtitle="A theoretical signal is not a completed arbitrage"
              >
                <Chart series={query.data.series} mode="profit" />
              </Panel>
              <Panel title="Cost and execution quality">
                <div className="funnel">
                  {[
                    ["Fee impact", money(query.data.fees)],
                    ["Modeled slippage", money(query.data.slippage)],
                    [
                      "Fully hedged rate",
                      percent(query.data.fill_success_rate),
                    ],
                    [
                      "Partial-fill rate",
                      percent(query.data.partial_fill_rate),
                    ],
                    [
                      "Average qualified net edge",
                      percent(query.data.average_net_edge),
                    ],
                    [
                      "Actual simulated capital",
                      money(query.data.actual_capital),
                    ],
                    [
                      "Observed settlement result",
                      money(query.data.settlement_profit),
                    ],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
            <div className="two-column">
              <Panel title="By league">
                {Object.keys(query.data.by_league).length ? (
                  <div className="funnel">
                    {Object.entries(query.data.by_league).map(
                      ([league, value]) => (
                        <div key={league}>
                          <Badge>{league}</Badge>
                          <span>{value.count} observations</span>
                          <strong>{money(value.profit)}</strong>
                        </div>
                      ),
                    )}
                  </div>
                ) : (
                  <Empty title="No observations on this day" />
                )}
              </Panel>
              <Panel title="Why signals were rejected">
                <div className="reason-breakdown">
                  {Object.entries(query.data.rejection_reasons)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 12)
                    .map(([code, value]) => (
                      <div key={code}>
                        <Reasons reasons={[code]} />
                        <strong>{value}</strong>
                      </div>
                    ))}
                </div>
              </Panel>
            </div>
            <JsonEvidence
              title="Data-age and executable-size distributions"
              data={{
                ages_ms: query.data.quote_ages_ms,
                contracts: query.data.sizes,
                completeness: query.data.data_completeness,
              }}
            />
          </>
        )
      )}
    </>
  );
}
function Health() {
  const health = useData("/system/health", healthSchema);
  const config = useData("/system/configuration", configSchema);
  const alerts = useData("/alerts", page(objectSchema));
  return (
    <>
      <ErrorBox error={health.error} />
      {health.isPending ? (
        <Loading />
      ) : (
        health.data && (
          <>
            <div className="metrics">
              {Object.entries(health.data.dependencies).map(([name, value]) => (
                <Metric
                  key={name}
                  label={name}
                  value={String(value)}
                  detail="Required internal dependency"
                />
              ))}
            </div>
            <Panel
              title="Venue health"
              subtitle="Connected does not imply fresh, synchronized or economically equivalent"
            >
              <div className="health-cards">
                {health.data.venues.map((x) => (
                  <div className="health-card" key={x.venue}>
                    <h3>{venueName(x.venue)}</h3>
                    <Badge tone={x.error ? "danger" : "info"}>
                      {x.feed ?? "No feed"}
                    </Badge>
                    <dl>
                      <dt>Last book</dt>
                      <dd>{time(x.last_book)}</dd>
                      <dt>Discovery</dt>
                      <dd>{x.discovery ?? "unknown"}</dd>
                      <dt>Reconnects</dt>
                      <dd>{x.reconnects ?? 0}</dd>
                    </dl>
                    {x.error && <Reasons reasons={[x.error]} />}
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="Worker leases and heartbeats">
              <div className="health-list">
                {health.data.workers.map((x) => (
                  <div key={x.role}>
                    <strong>{x.role}</strong>
                    <span>{x.heartbeat_age_seconds}s since heartbeat</span>
                    <Badge tone={x.healthy ? "success" : "danger"}>
                      {x.healthy ? "running" : "missing / stopped"}
                    </Badge>
                  </div>
                ))}
              </div>
            </Panel>
          </>
        )
      )}
      <Panel title="Operational alerts">
        <ErrorBox error={alerts.error} />
        {alerts.data?.items.length ? (
          alerts.data.items.map((x) => (
            <JsonEvidence
              key={String(x.id)}
              title={String(x.code ?? x.id)}
              data={x}
            />
          ))
        ) : (
          <Empty title="No operational alerts" />
        )}
      </Panel>
      <JsonEvidence
        title="Sanitized runtime configuration"
        data={config.data}
      />
    </>
  );
}
function RiskSettings({ csrf }: { csrf?: string }) {
  const query = useData("/settings/risk", riskResponseSchema);
  return (
    <>
      <ErrorBox error={query.error} />
      {query.data ? (
        <RiskForm
          key={query.data.revision}
          initial={query.data.settings}
          revision={query.data.revision}
          csrf={csrf}
        />
      ) : (
        query.isPending && <Loading />
      )}
    </>
  );
}
function RiskForm({
  initial,
  revision,
  csrf,
}: {
  initial: Risk;
  revision: number;
  csrf?: string;
}) {
  const [risk, setRisk] = useState(initial);
  const [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2));
  const [error, setError] = useState<Error | null>(null);
  const [notice, setNotice] = useState("");
  const [confirmKill, setConfirmKill] = useState(false);
  const [busy, setBusy] = useState(false);
  const [alias, setAlias] = useState({
    league: "NFL",
    original: "",
    canonical: "",
    source: "operator-reviewed",
    version: "v1",
  });
  const client = useQueryClient();
  const fields = [
    ["bankroll", "Paper bankroll (USD)"],
    ["max_per_venue", "Maximum per venue (USD)"],
    ["max_total_notional", "Maximum total deployment (USD)"],
    ["max_daily_capital", "Daily deployment limit (USD)"],
    ["min_edge", "Minimum return fraction (0.005 = 0.5%)"],
    ["min_profit", "Minimum net profit (USD)"],
    ["max_quote_age_ms", "Maximum quote age (ms)"],
    ["latency_ms", "Paper decision / submission latency (ms)"],
    ["slippage_bps", "Extra slippage allowance (bps)"],
  ] as const;
  function change(key: string, value: unknown) {
    const next = { ...risk, [key]: value };
    setRisk(next);
    setAdvanced(JSON.stringify(next, null, 2));
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const parsed = riskSchema.parse(JSON.parse(advanced));
      await api("/settings/risk", riskResponseSchema, {
        method: "PUT",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify({
          settings: parsed,
          revision,
          reason: "Operator risk configuration",
        }),
      });
      await client.invalidateQueries();
    } catch (value) {
      setError(value instanceof Error ? value : new Error("Settings failed"));
    } finally {
      setBusy(false);
    }
  }
  async function kill() {
    setError(null);
    setBusy(true);
    try {
      await api(
        `/settings/kill-switch/${risk.kill_switch ? "deactivate" : "activate"}`,
        riskResponseSchema,
        { method: "POST", headers: { "X-CSRF-Token": csrf ?? "" } },
      );
      setConfirmKill(false);
      await client.invalidateQueries();
    } catch (value) {
      setError(
        value instanceof Error ? value : new Error("Kill switch failed"),
      );
    } finally {
      setBusy(false);
    }
  }
  async function saveAlias() {
    setError(null);
    try {
      await api("/settings/aliases", objectSchema, {
        method: "PUT",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify(alias),
      });
      setNotice("Alias saved with provenance; next discovery applies it.");
      await client.invalidateQueries({ queryKey: ["/settings/aliases"] });
    } catch (value) {
      setError(value instanceof Error ? value : new Error("Alias failed"));
    }
  }
  return (
    <>
      <Panel
        title="Paper execution circuit breaker"
        subtitle={`Audited configuration revision ${revision}`}
        action={
          <Badge tone={risk.kill_switch ? "warning" : "success"}>
            {risk.kill_switch ? "STOPPED" : "PAPER ENABLED"}
          </Badge>
        }
      >
        <p className="panel-note">
          Activating stops new paper intents and cancels pending simulations.
          Existing hypothetical exposure stays visible. This never enables live
          trading.
        </p>
        <button
          className={risk.kill_switch ? "primary" : "danger-button"}
          disabled={!csrf || busy}
          onClick={() =>
            risk.kill_switch ? setConfirmKill(true) : void kill()
          }
        >
          {risk.kill_switch
            ? "Enable paper simulation"
            : "Activate paper kill switch"}
        </button>
        {!csrf && (
          <p className="panel-note">
            Operator login is required to change settings.
          </p>
        )}
      </Panel>
      <ErrorBox error={error} />
      {notice && <Badge tone="info">{notice}</Badge>}
      <Panel
        title="Risk and paper assumptions"
        subtitle="Backend validation and optimistic revision checks protect against concurrent changes."
      >
        <form className="form" onSubmit={(event) => void save(event)}>
          <div className="settings-grid">
            {fields.map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  step="any"
                  value={String(risk[key])}
                  onChange={(e) =>
                    change(
                      key,
                      ["max_quote_age_ms", "latency_ms"].includes(key)
                        ? Number(e.target.value)
                        : e.target.value,
                    )
                  }
                />
              </label>
            ))}
            <label>
              Supported leagues
              <input
                value={risk.supported_leagues.join(", ")}
                onChange={(e) =>
                  change(
                    "supported_leagues",
                    e.target.value
                      .split(",")
                      .map((x) => x.trim().toUpperCase())
                      .filter(Boolean),
                  )
                }
              />
            </label>
            <label>
              Fill model
              <select
                value={risk.fill_model}
                onChange={(e) => change("fill_model", e.target.value)}
              >
                <option value="conservative">Conservative (recommended)</option>
                <option value="optimistic">Optimistic (detection-time)</option>
                <option value="observed">
                  Observed (requires queue evidence; unavailable)
                </option>
              </select>
            </label>
            <label>
              Sizing mode
              <select
                value={risk.sizing_mode}
                onChange={(e) => change("sizing_mode", e.target.value)}
              >
                {[
                  "per_venue",
                  "total_notional",
                  "fixed_quantity",
                  "max_depth",
                  "target_profit",
                  "bankroll",
                ].map((x) => (
                  <option value={x} key={x}>
                    {x.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Entry quantity increment (contracts)
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={risk.entry_quantity_step}
                onChange={(e) => change("entry_quantity_step", e.target.value)}
              />
            </label>
          </div>
          <details className="evidence">
            <summary>All risk controls and advanced settings</summary>
            <textarea
              className="json-editor"
              aria-label="Advanced risk settings"
              value={advanced}
              onChange={(e) => setAdvanced(e.target.value)}
            />
          </details>
          <button className="primary" disabled={!csrf || busy}>
            Save audited risk settings
          </button>
        </form>
      </Panel>
      <Panel
        title="Team aliases"
        subtitle="Exact league-scoped mappings with operator provenance; never fuzzy autoapproval."
      >
        <div className="settings-grid">
          {Object.entries(alias).map(([key, value]) => (
            <label key={key}>
              {key}
              <input
                value={value}
                onChange={(e) => setAlias({ ...alias, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <button
          disabled={!csrf || !alias.original || !alias.canonical}
          onClick={() => void saveAlias()}
        >
          Save alias
        </button>
      </Panel>
      <div className="warning-strip">
        <LockKeyhole size={15} />
        Live execution is intentionally unavailable in this release. No UI
        action can unlock it.
      </div>
      {confirmKill && (
        <Dialog
          title="Enable hypothetical paper execution?"
          close={() => setConfirmKill(false)}
        >
          <p>
            New simulated positions can be opened within the configured limits.
            No actual orders will be sent. Existing settings and the daily
            target do not guarantee trades or profits.
          </p>
          <div className="actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() => void kill()}
            >
              Enable paper only
            </button>
            <button onClick={() => setConfirmKill(false)}>Cancel</button>
          </div>
        </Dialog>
      )}
    </>
  );
}
