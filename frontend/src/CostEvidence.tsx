import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { z } from "zod";
import { api, reason, useData } from "./api";
import { ErrorBox, JsonEvidence, Panel } from "./components";
import {
  additionalCostsSchema,
  costComponentNames,
  costComponentSchema,
  riskResponseSchema,
} from "./schemas";

type Component = z.infer<typeof costComponentSchema>;
type Costs = z.infer<typeof additionalCostsSchema>;
type Response = z.infer<typeof riskResponseSchema>;
const venues = ["kalshi", "polymarket_us", "polymarket_international"] as const;

function unknown(): Component {
  return {
    status: "unknown",
    amount: "0",
    basis: "per_leg",
    applies_to: "opening",
    execution_path: "",
    evidence: "",
    observed_at: new Date().toISOString(),
    expires_at: null,
  };
}

export function CostEvidence({ csrf }: { csrf?: string }) {
  const risk = useData("/settings/risk", riskResponseSchema);
  return (
    <Panel
      title="Component-level cost evidence"
      subtitle="Verified amount, verified zero, not applicable, or unknown. A balance read cannot identify your funding method."
    >
      <ErrorBox error={risk.error} />
      {risk.data && (
        <CostForm key={risk.data.revision} initial={risk.data} csrf={csrf} />
      )}
    </Panel>
  );
}

function CostForm({ initial, csrf }: { initial: Response; csrf?: string }) {
  const [costs, setCosts] = useState<Record<string, Costs>>(() =>
    Object.fromEntries(
      venues.map((venue) => {
        const stored =
          initial.settings.additional_costs[venue] ??
          additionalCostsSchema.parse({});
        return [
          venue,
          {
            ...stored,
            components: Object.fromEntries(
              costComponentNames.map((name) => [
                name,
                stored.components[name] ?? unknown(),
              ]),
            ),
          },
        ];
      }),
    ),
  );
  const [replaceLegacy, setReplaceLegacy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  const legacyAmounts = Object.values(costs).some((value) =>
    [
      value.fixed_per_leg,
      value.settlement_per_contract,
      value.rebalancing_per_contract,
    ].some((amount) => Number(amount) !== 0),
  );

  function update(venue: string, name: string, patch: Partial<Component>) {
    const previous = costs[venue];
    const old = previous.components[name];
    const component = {
      ...old,
      ...patch,
      observed_at: new Date().toISOString(),
    };
    if (patch.status !== undefined && patch.status !== "verified_amount")
      component.amount = "0";
    if (component.status !== "unknown" && component.expires_at === null) {
      component.expires_at = new Date(
        new Date(component.observed_at).getTime() + 7 * 86400000,
      ).toISOString();
    }
    setCosts({
      ...costs,
      [venue]: {
        ...previous,
        components: { ...previous.components, [name]: component },
      },
    });
  }

  async function save() {
    setError(null);
    setBusy(true);
    try {
      const values: Record<string, Costs> = {};
      for (const venue of venues) {
        const existing = costs[venue];
        values[venue] = additionalCostsSchema.parse({
          ...existing,
          ...(replaceLegacy
            ? {
                fixed_per_leg: "0",
                settlement_per_contract: "0",
                rebalancing_per_contract: "0",
              }
            : {}),
          components: Object.fromEntries(
            costComponentNames.map((name) => [name, existing.components[name]]),
          ),
        });
      }
      await api("/settings/risk", riskResponseSchema, {
        method: "PUT",
        headers: { "X-CSRF-Token": csrf ?? "" },
        body: JSON.stringify({
          revision: initial.revision,
          settings: { ...initial.settings, additional_costs: values },
          reason: note,
        }),
      });
      await client.invalidateQueries();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught
          : new Error("Cost evidence update failed"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p>
        Amounts are USD (USDC for the international venue), allocated per filled
        contract or once per applicable executed leg. Specify opening, unwind or
        both; settlement expenses are planned opening allocations, not charged
        again on a sale. Trading commission is priced separately. These are
        execution-path assumptions, not generic venue promises; specify
        transfer/allocation scope, provenance and expiry. Unknown charges are
        not treated as verified zero.
      </p>
      {venues.map((venue) => (
        <details key={venue}>
          <summary>{venue}: review all six components</summary>
          <JsonEvidence
            title="Stored model, including retained legacy amounts"
            data={initial.settings.additional_costs[venue]}
          />
          {costComponentNames.map((name) => {
            const component = costs[venue].components[name];
            const prefix = `${venue} ${name}`;
            return (
              <fieldset key={name}>
                <legend>{reason(name)}</legend>
                <label>
                  Status
                  <select
                    aria-label={`${prefix} status`}
                    value={component.status}
                    onChange={(e) =>
                      update(venue, name, {
                        status: costComponentSchema.shape.status.parse(
                          e.target.value,
                        ),
                      })
                    }
                  >
                    <option value="unknown">Unknown</option>
                    <option value="verified_amount">Verified amount</option>
                    <option value="verified_zero">Verified zero</option>
                    <option value="not_applicable">
                      Not applicable to specified path
                    </option>
                  </select>
                </label>
                <label>
                  Exact decimal amount
                  <input
                    aria-label={`${prefix} amount`}
                    value={component.amount}
                    disabled={component.status !== "verified_amount"}
                    onChange={(e) =>
                      update(venue, name, { amount: e.target.value })
                    }
                  />
                </label>
                <label>
                  Allocation basis
                  <select
                    aria-label={`${prefix} basis`}
                    value={component.basis}
                    onChange={(e) =>
                      update(venue, name, {
                        basis: costComponentSchema.shape.basis.parse(
                          e.target.value,
                        ),
                      })
                    }
                  >
                    <option value="per_leg">Once per executed leg</option>
                    <option value="per_contract">Per filled contract</option>
                  </select>
                </label>
                <label>
                  Applicable operation
                  <select
                    aria-label={`${prefix} operation`}
                    value={component.applies_to}
                    onChange={(e) =>
                      update(venue, name, {
                        applies_to: costComponentSchema.shape.applies_to.parse(
                          e.target.value,
                        ),
                      })
                    }
                  >
                    <option value="opening">
                      Opening leg / planned holding costs
                    </option>
                    <option value="unwind">Emergency unwind only</option>
                    <option value="both">Both opening and unwind</option>
                  </select>
                </label>
                <label>
                  Applicable execution/funding/withdrawal path
                  <input
                    aria-label={`${prefix} path`}
                    value={component.execution_path}
                    onChange={(e) =>
                      update(venue, name, { execution_path: e.target.value })
                    }
                  />
                </label>
                <label>
                  Evidence reference and applicability explanation; never
                  credentials
                  <textarea
                    aria-label={`${prefix} evidence`}
                    value={component.evidence}
                    onChange={(e) =>
                      update(venue, name, { evidence: e.target.value })
                    }
                  />
                </label>
                <label>
                  Expiry (timezone-aware ISO timestamp)
                  <input
                    aria-label={`${prefix} expiry`}
                    value={component.expires_at ?? ""}
                    onChange={(e) =>
                      update(venue, name, {
                        expires_at: e.target.value || null,
                      })
                    }
                  />
                </label>
              </fieldset>
            );
          })}
        </details>
      ))}
      {legacyAmounts && (
        <label>
          <input
            type="checkbox"
            checked={replaceLegacy}
            onChange={(e) => setReplaceLegacy(e.target.checked)}
          />
          Replace existing nonzero legacy aggregate charges with this component
          model. Re-enter applicable charges; do not double count.
        </label>
      )}
      <label>
        Audited change reason
        <input value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <ErrorBox error={error} />
      <button
        disabled={
          !csrf || busy || !note.trim() || (legacyAmounts && !replaceLegacy)
        }
        onClick={() => void save()}
      >
        Save component cost evidence
      </button>
    </>
  );
}
