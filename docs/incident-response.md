# Incident response

Treat incorrect contract equivalence, fee calculation or execution-quality claims
as incidents even in paper mode.

1. Activate the paper kill switch. Record incident time, source mode, build,
   affected market/rule hashes and opportunity/trade IDs.
2. Preserve database/audit evidence and relevant retained snapshots. Do not replay
   into the production portfolio, overwrite rules, or delete erroneous observations.
3. Classify: venue data outage, sequence corruption, matching error, math/fee
   error, database/worker failure or credential compromise.
4. Invalidate affected books/matches and document customer-facing uncertainty.
   Existing paper exposure remains visible until reconciled/settled.
5. Repair the root cause, add a regression test, verify retained-input replay and
   bounded staging recovery. Rotate/revoke secrets for a compromise.
6. Resume only after new snapshots, compatible rules/fees and healthy workers.
   Document impacted reports, known missing intervals and corrective action.

This repository sends no real orders. If later execution is introduced, order
state reconciliation and venue-native cancel/hedge policy must be an isolated,
approved incident procedure; never retry ambiguous submissions blindly.
