# Market matching

Identity requires sport, league, exactly two participants, reliably sourced UTC
game start within five minutes, full-period market type and explicit YES outcome.
Market listing/closing/expected completion times are **not** game start times.
Kalshi `occurrence_datetime` observed in a sample NFL response referred to expected
completion; it is deliberately not mapped to start.

Exact league-scoped alias records store original/canonical values, source, version
and timestamps. Fuzzy similarity never autoapproves. Candidate generation compares
normalized participant sets, league and event date before hard settlement gates.

Required rule fields: period, overtime, draw, cancellation, postponement,
settlement source and unit payout. Unknowns queue review. Mismatches reject.
Variable cancellation fair prices reject even when both policies have the same
name: independently observed fair prices need not sum to a dollar.

Opposite YES-team identities are supported through an inverted mapping only where
draw rules preserve complementarity. Soccer-style both-NO draws cannot be silently
inverted. Binary unit payouts with equal half-refunds preserve a fixed combined
payout; other payout assumptions are not established by string matching.

Public text extraction is intentionally narrow. Missing policies do not become
defaults. Different raw rule texts require an additional human review even when
all extracted fields agree, preventing unmodeled clauses from becoming automatic
proof. Draw refunds are not inferred as cancellation policy. Operators can
document canonical participants/start/rules with evidence
for every field using the normalization endpoint/editor. Annotation validity
binds the raw public specification hash (text, identity, tokens and published
schedule); changed source text or a reschedule invalidates it. Mutable prices,
fee observations and discovery timestamps do not invalidate contract identity.

Each match ID binds both rule/identity hashes. Approve/reject/rematch requires both
expected hashes and an authenticated CSRF-protected review; incompatible/unknown
pairs cannot be approved by clicking. New normalized specs produce new match IDs
and retire old current pairs. Audits and review notes are retained.

Semantic suggestions have a strict parser with model/prompt/rule provenance,
but no LLM provider is enabled: external AI is optional review assistance, never
required and never on the pricing path.
