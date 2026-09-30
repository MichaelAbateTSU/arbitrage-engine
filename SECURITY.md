# Security and financial risk boundaries

Report vulnerabilities privately to the repository owner through GitHub private
vulnerability reporting when enabled, or a private owner channel. Do not include
working credentials or trading private keys in an issue.

This is a read-only scanner/paper system. It neither holds customer funds nor
places actual orders. Cross-venue trading is non-atomic: a fill on one exchange
cannot guarantee a complementary fill elsewhere.

## Controls

- Backend-only environment credentials; redacted structured logs; bounded HTTP
  requests, retries, WebSocket frames and request bodies.
- Argon2id operator password hashes; random opaque HttpOnly/SameSite-strict session
  cookies, Secure in production, database revocation and expiry; session-secret
  rotation invalidates existing sessions.
- Exact origin checks and CSRF token on administrative mutations; persistent
  login rate limits; default authenticated production reads.
- Parameterized ORM operations, React text encoding, CSP, anti-framing headers,
  no frontend private keys, no credentials in API configuration responses.
- Audited settings revisions, version-bound matches, persisted intent,
  transactional event/bankroll reservations and durable single-role worker leases.
- Non-root images, hash-locked Python requirements, npm lockfile and dependency/
  secret scanning in CI.

## Rotation

Enable the paper kill switch. Revoke compromised venue data credentials at the
venue; replace environment secrets and restart affected workers. Change the
operator hash and rotate `SESSION_SECRET` to revoke sessions. Audit related
settings/matches/intents; invalidate books; re-establish snapshots before enabling
paper simulation. Never paste private keys into the dashboard.

## Known boundaries

The single-operator application is not a multi-tenant execution/custody product.
Venue terms, commercial data rights and jurisdiction eligibility require owner
legal review. In-app alerts do not guarantee external paging. Unknown rule fields,
variable refunds and unknown fees are rejected, but operator evidence itself must
be truthful. International WebSocket loss cannot be proven absent without exchange
sequence/checksum guarantees. Paper-fill inference does not reproduce queue priority.
