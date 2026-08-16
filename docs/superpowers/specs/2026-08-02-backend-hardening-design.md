# PinForge Backend Hardening Design

## Goal

Make unattended Etsy import, AI generation, export, scheduling, and Pinterest
publishing safe against duplicate remote writes, concurrent workers, crashes,
corrupt local state, hostile inputs, expired credentials, and partial filesystem
operations while keeping PinForge a single-user local desktop application.

## Chosen approach

Retain Python, SQLite, httpx, keyring, Pillow, and the existing CLI/UI. Harden the
current boundaries instead of adding a server, broker, ORM, or new service. SQLite
remains the source of truth and gains explicit state transitions, UTC timestamps,
leases, audit events, schema constraints, backup-aware migrations, and atomic quota
claims. Filesystem exports use a staging directory and atomic promotion.

Pinterest publishing uses an at-least-once local worker with an explicit
`publish_unknown` terminal state for ambiguous POST outcomes. Ambiguous writes are
never automatically retried. A user may reconcile them by recording a remote pin ID
or explicitly requeueing the draft. This avoids pretending that exactly-once delivery
is possible without provider idempotency support.

## Data model

- Schema v3 adds product source kind, integer price units, active state, draft error
  codes/providers, lease owner/expiry, approval and generation provenance, remote URL,
  account identity, and audit events.
- All newly written timestamps are timezone-aware UTC ISO 8601 values.
- Status transitions are constrained to `draft`, `ready`, `queued`, `publishing`,
  `published`, `publish_unknown`, `failed`, and `dead_letter`.
- Published rows and remote IDs are protected from accidental upsert resets.
- Migration creates a backup and rebuilds legacy tables where constraints or foreign
  keys cannot be added safely in place.

## Queue and scheduling

- Claiming one due draft and reserving the daily quota happen in one immediate
  transaction.
- Workers use renewable leases and claim only one item per publish step.
- Stale jobs requeue until the maximum attempt count, then move to dead letter.
- Headless runs use bounded batches and no long sleep, fitting the Windows task limit.
- Scheduling converts configured local wall-clock slots through an explicit IANA time
  zone and stores UTC.

## API and OAuth boundaries

- HTTP responses and downloads have size limits, granular timeouts, bounded
  pagination, retry jitter, HTTP-date `Retry-After`, and authenticated redirect
  protection.
- Unsafe POST transport failures are marked ambiguous; safe requests may retry.
- Downloaded images are streamed, checked for allowed HTTPS hosts/IPs, validated by
  signature, and decoded under pixel limits.
- OAuth attempts are short-lived, provider-bound, one-use secrets stored through the
  secret store. Refresh is process-locked, preserves rotated tokens, records scopes,
  and retries one safe request after a 401 where supported.

## Export and content

- Drafts and AI requests are validated at their shared domain boundary.
- AI inputs are bounded and marked as untrusted; outputs store model/prompt provenance
  and require the export action as approval.
- Exports render inside a staging directory, produce an accurate CSV including board
  and schedule state, then atomically promote the complete bundle.
- Managed paths are relative to the PinForge data directory where practical and are
  checked before scheduling and publishing.

## Operations and tests

- Structured JSON logs redact credentials and include correlation IDs.
- An append-only audit table records auth, import, schedule, retry, reconciliation,
  publish, and configuration events.
- CLI failures and partial queue results return non-zero exit codes.
- Tests cover concurrent quota claims, stale leases, remote-success/local-failure,
  token rotation, migration rollback, corruption, DST, malicious images, response
  limits, repeated cursors, and retry semantics.
- CI runs lint, type checking, dependency audit, coverage, Linux backend tests, and a
  Windows GUI/package smoke path on pull requests and pushes.

## Deliberate limits

PinForge remains local and single-account per provider. It does not add cloud sync,
multi-user authorization, analytics ingestion, a message broker, or automated remote
pin deletion. Those require a separate product design and are outside this hardening
work.
