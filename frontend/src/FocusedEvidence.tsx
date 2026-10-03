import type { z } from "zod";
import { Badge, JsonEvidence, Panel, Reasons } from "./components";
import { reason } from "./api";
import { focusedValidationSchema } from "./schemas";

export function FocusedEvidence({
  value,
  collectionStarted,
  onReview,
}: {
  value: z.infer<typeof focusedValidationSchema>;
  collectionStarted: string;
  onReview: (id: string) => void;
}) {
  return (
    <>
      <Panel
        title="Contract families before more games"
        subtitle="Bounded policy screening never substitutes for independent, individual hash-bound approval."
      >
        <p>{value.screen_conclusion}</p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Family</th>
                <th>Pairs</th>
                <th>Disposition</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {value.families.map((family) => (
                <tr key={family.id}>
                  <td>
                    {family.league} / {family.second_venue}
                  </td>
                  <td>{family.pair_count}</td>
                  <td>
                    <Badge
                      tone={
                        family.disposition === "compatible_profile"
                          ? "success"
                          : "warning"
                      }
                    >
                      {reason(family.disposition)}
                    </Badge>
                    <Reasons reasons={family.reasons} />
                  </td>
                  <td>
                    {family.representative_match_id && (
                      <button
                        onClick={() => {
                          if (family.representative_match_id)
                            onReview(family.representative_match_id);
                        }}
                      >
                        Review representative
                      </button>
                    )}
                    <p>{family.review_scope}</p>
                    <JsonEvidence
                      title="Rule profiles, exact source hashes and scenario bounds"
                      data={family}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="panel-note">
          Review limit: {value.family_review_limit} families.{" "}
          {value.families.length} currently screened.
        </p>
      </Panel>
      <Panel
        title="Focused book diagnosis"
        subtitle="Missing snapshots are not empty markets. REST probes do not replace sequenced streams or refresh venue timestamps."
      >
        <Badge tone="warning">{reason(value.focus_scope)}</Badge>
        {value.focus_scope === "diagnostic_only" && (
          <p>
            No promising compatible family exists in the reviewed scope. These
            are representative diagnostic pairs, not tradable candidates.
          </p>
        )}
        {value.focused_pairs.map((pair) => (
          <div className="rules-card" key={pair.match_id}>
            <button
              className="event-link"
              onClick={() => onReview(pair.match_id)}
            >
              <strong>
                {pair.league}: {pair.event}
              </strong>
            </button>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Instrument / outcome</th>
                    <th>Current cause</th>
                    <th>Age ms</th>
                    <th>Ask levels</th>
                    <th>Monitoring evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {pair.books.map((book) => (
                    <tr key={`${book.market_id}:${book.outcome}`}>
                      <td>
                        {book.instrument} / {book.outcome}
                      </td>
                      <td>{reason(book.cause)}</td>
                      <td>{book.age_ms ?? "missing"}</td>
                      <td>{book.asks ?? "not observed"}</td>
                      <td>
                        <JsonEvidence
                          title="Selection, subscription, mapping and public REST probe"
                          data={book.monitoring}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Panel>
      <Panel
        title="Usable observation coverage, not just elapsed days"
        subtitle="Aggregate pair-seconds are not wall-clock seconds. Zero blocked observation time cannot establish absence of an edge."
      >
        <p>
          Distinct observed funded-size spread windows:{" "}
          {value.coverage.distinct_funded_spread_windows}. Repeated observations
          within the configured episode gap are deduplicated; these are not
          fills.
        </p>
        <p>
          Original diagnostic start: {collectionStarted}. Coverage
          instrumentation start:{" "}
          {value.coverage.tracking_started_at ?? "not yet sampled"}.
        </p>
        <Badge
          tone={
            value.coverage.verdict.startsWith("inconclusive")
              ? "warning"
              : "success"
          }
        >
          {reason(value.coverage.verdict)}
        </Badge>
        <div className="metrics">
          {Object.entries(value.coverage.pair_seconds).map(
            ([name, seconds]) => (
              <div className="metric" key={name}>
                <div className="metric-label">{reason(name)}</div>
                <div className="metric-value">
                  {Number(seconds).toFixed(1)} pair-s
                </div>
              </div>
            ),
          )}
        </div>
        <p className="panel-note">{value.coverage.method}</p>
        <p>
          Historical coverage before instrumentation:{" "}
          {reason(value.coverage.historical_coverage_before_tracking)}. No clock
          was restarted.
        </p>
        <JsonEvidence
          title="Persistent daily, hash-bound pair coverage"
          data={value.coverage.by_pair}
        />
      </Panel>
      <Panel title="Which additional cost evidence is missing?">
        {Object.entries(value.cost_evidence).map(([venue, costs]) => (
          <div className="actions" key={venue}>
            <strong>{venue}</strong>
            <Badge tone={costs.complete ? "success" : "warning"}>
              {costs.complete ? "Complete and unexpired" : "Incomplete"}
            </Badge>
            <span>
              {costs.unknown_components.length
                ? costs.unknown_components.join(", ")
                : "All six components evidenced"}
            </span>
          </div>
        ))}
        <p>
          Current market trading fees remain separate. Funding method, payment
          conversion and withdrawal costs are never inferred to be zero from USD
          quotes.
        </p>
      </Panel>
    </>
  );
}
