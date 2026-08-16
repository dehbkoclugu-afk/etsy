# PinForge SaaS Design

## Goal

Turn the proven PinForge 0.2.0 Python desktop workflow into a sellable web SaaS
for Etsy sellers. A seller connects one or more Etsy shops, imports listings,
creates branded Pinterest creatives, schedules or exports them, and learns which
templates and publishing times perform best.

The first commercial release must remain useful before broad Etsy and Pinterest
API approval. Local upload, CSV import, PNG download, and schedule CSV export are
therefore first-class product paths rather than temporary fallbacks.

## Product position

PinForge is a focused Etsy-to-Pinterest growth workspace, not a general social
media suite. Its initial promise is:

> Turn an Etsy listing into a month of on-brand Pinterest content in minutes.

The primary customer is a solo Etsy seller with 10-500 active listings. Small
agencies managing several shops are supported by the data model, but agency-only
features are deferred until solo-seller activation and retention are proven.

## External constraints

- Etsy classifies apps used by other sellers at scale as Commercial Access apps.
  The product must support manual import while the application is being reviewed:
  <https://developers.etsy.com/>.
- Pinterest Trial access creates sandbox entities visible only to their creator;
  production multi-user publishing requires Standard access and an OAuth demo:
  <https://developers.pinterest.com/docs/key-concepts/access-tiers/>.
- Paddle is the initial merchant of record. It supports software suppliers outside
  its explicitly blocked countries and handles tax calculation and remittance:
  <https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle>
  and <https://developer.paddle.com/concepts/sell/supported-countries-locales>.
- Django 5.2 is the selected long-term-support line and receives security updates
  for at least three years from its April 2025 release:
  <https://docs.djangoproject.com/en/6.0/releases/5.2/>.

## Architecture decision

Build one Django 5.2 monolith on Python 3.12 with PostgreSQL, server-rendered pages,
small progressive-enhancement JavaScript modules, and a separate process running a
Django management-command worker. Keep the existing rendering, scheduling, provider,
and validation code as framework-independent Python packages inside the same repo.

This gives the product four deployable process roles from one codebase:

1. `web`: Django HTTP application.
2. `worker`: claims durable PostgreSQL jobs and executes imports, renders, and
   provider writes.
3. `scheduler`: promotes due scheduled work into the same job table.
4. `release`: migrations and static asset collection.

The first deployment does not add Celery, Redis, Kafka, a separate API service, a
frontend SPA, Kubernetes, or microservices. PostgreSQL is already required and can
safely provide the initial durable queue with row locks and `SKIP LOCKED`.

## Repository layout

The desktop package remains importable while SaaS code is introduced alongside it:

```text
src/pinforge/              reusable domain, rendering, provider, and scheduling core
src/pinforge_saas/         Django project settings, URL routing, and ASGI/WSGI entry
src/pinforge_web/          accounts, organizations, shops, catalog, creatives, jobs
templates/                 server-rendered product UI
static/                    versioned CSS and progressive-enhancement JavaScript
tests/                     core unit tests and SaaS integration tests
```

The web layer may adapt core models but may not duplicate rendering or provider
business rules. Core functions receive explicit inputs and never reach into the
current request, session, or global Django settings.

## Tenancy and authorization

`Organization` is the tenant boundary. Every shop, listing, creative, schedule,
provider connection, job, usage record, audit event, and subscription belongs to an
organization. Users access organizations through `Membership` with `owner`, `admin`,
or `member` roles.

MVP creates one organization for every new user. Invitations and multiple human
members are not exposed initially, but the membership relation prevents a later
schema rewrite.

Tenant safety rules:

- No organization-owned model has a nullable `organization_id`.
- Request code resolves the active membership once, then scopes every queryset.
- Service functions require an organization argument; they do not accept arbitrary
  object IDs without checking ownership.
- Object-level mutations use organization-scoped lookups and return 404 across
  tenant boundaries.
- Background jobs store `organization_id` and re-check ownership when claimed.
- Tests attempt cross-tenant reads, writes, exports, job claims, and signed downloads.

## Identity and sessions

Django authentication uses email as the login identifier. The initial flow supports
password login, password reset, email verification, secure cookie sessions, and
optional social login later. Staff administration uses Django admin behind separate
staff authorization and mandatory MFA at the identity-provider or reverse-proxy layer.

Session cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`. Login, reset, OAuth,
and webhook endpoints have explicit rate limits. CSRF protection remains enabled for
all browser writes.

## Core data model

### Account and commerce

- `User`: email identity and profile preferences.
- `Organization`: tenant, display name, locale, timezone, and lifecycle state.
- `Membership`: user, organization, role, and invitation state.
- `Subscription`: Paddle customer/subscription IDs, plan, status, renewal date, and
  last accepted webhook timestamp.
- `UsageLedger`: append-only monthly counters for renders, AI generations, connected
  shops, scheduled publishes, and storage bytes.

### Provider and catalog

- `ProviderConnection`: encrypted OAuth tokens, scopes, provider account ID, token
  expiry, connection health, and revocation state.
- `Shop`: Etsy shop identity and organization-specific display settings.
- `Listing`: normalized title, URL, price in minor units, currency, tags, description,
  status, source revision, and last synchronization timestamp.
- `ListingImage`: remote source metadata and immutable stored original.
- `SyncCursor`: provider-specific bounded pagination state.

### Creative and publishing

- `BrandKit`: colors, typography, logo, shop name, and default call to action.
- `Creative`: listing, template, copy, approval state, provenance, and immutable input
  revision.
- `CreativeAsset`: original and rendered object-storage keys, dimensions, media type,
  checksum, and lifecycle state.
- `PublishSchedule`: creative, provider destination, due time in UTC, and local-time
  explanation metadata.
- `PublishReceipt`: local idempotency key, remote ID/URL, attempt, and outcome including
  `publish_unknown`.
- `MetricSnapshot`, `Experiment`, `ExperimentVariant`, and `TrendTerm`: SaaS versions
  of the tested desktop growth model, all tenant-scoped.

### Operations

- `Job`: kind, JSON payload, status, priority, due time, lease owner/expiry, attempts,
  deduplication key, result, and sanitized failure information.
- `AuditEvent`: append-only actor, organization, event type, target, correlation ID,
  redacted metadata, and timestamp.
- `WebhookEvent`: provider event ID, verified raw checksum, processing state, attempts,
  and timestamps for replay-safe ingestion.

## Durable job execution

Workers claim due jobs in a short transaction using `select_for_update(skip_locked=True)`.
A claim sets a lease and commits before any network or rendering work. Success and
failure are recorded in a new transaction. Expired leases are recoverable.

Job types include listing synchronization, source-image download, AI copy generation,
creative rendering, metric synchronization, scheduled publication, and storage cleanup.

Provider writes retain the desktop application's conservative semantics:

- A local deduplication key prevents two workers from intentionally sending the same
  creative to the same destination.
- Safe reads may retry with bounded exponential backoff and jitter.
- A remote write whose result is ambiguous becomes `publish_unknown` and is never
  automatically resent.
- Reconciliation records a discovered remote object or requires an explicit operator
  decision before requeueing.

PostgreSQL is sufficient for the expected alpha load. Queue depth, lease latency, or
database contention must be measured before adding a broker.

## Rendering and storage

The existing Pillow render engine remains the source of truth for 1000 x 1500 output.
The web adapter converts database records into existing `SourceProduct`, `BrandKit`,
and template inputs, renders in an isolated temporary directory, validates output,
then uploads it to S3-compatible object storage.

Object keys contain organization and immutable asset IDs but no seller-provided names.
The database stores keys, never public URLs. Downloads use short-lived signed URLs or
an authorized streaming view. Uploads and downloaded provider images enforce byte,
pixel, MIME, host, and redirect limits before decoding.

Original listing images and final creative assets are retained while referenced.
Temporary previews and failed render artifacts have short lifecycle rules. Deletion of
an organization schedules object cleanup after a recoverable grace period.

## Etsy connection and synchronization

OAuth uses Authorization Code with PKCE, short-lived single-use state, an exact HTTPS
redirect URI, and encrypted token storage. The callback binds the provider account to
the initiating organization and rejects replay, scope mismatch, or account collision.

Synchronization is incremental and bounded. It records a cursor/checkpoint, upserts by
provider ID, stores a source revision, and deactivates missing listings only after a
complete successful scan. Etsy webhooks may later trigger targeted refreshes; polling
remains the recovery path.

Commercial-access delay must not block launch. CSV, JSON, and image-folder import use
the same validation and normalized catalog tables as API synchronization.

## Pinterest connection and publishing

Pinterest OAuth, board reads, Pin creation, and analytics reuse the tested provider
client behind organization-specific connections. Trial access is used only for demos
and approval evidence. General customers see direct publishing only after Standard
access is granted; before then, they download PNG/CSV bundles.

The approval package must include a screen recording of OAuth, board selection,
creative approval, publishing, token handling explanation, privacy policy, and account
disconnect/delete behavior.

## Billing and entitlements

Paddle hosted checkout is the only card boundary. PinForge does not receive or store
card data. Webhooks are verified against the raw request body, stored by event ID, and
processed idempotently.

Initial commercial packaging is intentionally small:

- `trial`: one shop, 30 renders, 10 AI generations, exports enabled, 14 days.
- `pro`: up to three shops, 500 renders per month, 200 AI generations, scheduling,
  analytics, experiments, and priority processing.

An agency tier is deferred until real customers demonstrate the need. Entitlements are
checked inside service methods and job creation, not only hidden in the interface.
Webhook delays do not instantly lock a paying customer; a short grace state allows
reconciliation.

## Security and privacy

- Provider secrets and refresh tokens are encrypted at the application layer with a
  versioned key ID; production keys live outside the database.
- Passwords use Django's current supported hashers. Secrets never appear in job JSON,
  audit metadata, analytics, exception messages, or logs.
- Host allowlists, DNS/IP checks, response limits, timeouts, and image verification
  preserve the desktop SSRF and decompression-bomb protections.
- Content Security Policy, trusted-host checks, HTTPS redirects, HSTS, secure cookies,
  CSRF, clickjacking protection, and strict referrer policy are production defaults.
- Provider disconnect revokes when possible, deletes local tokens, and records an audit
  event. Account deletion has a grace period and documented data-erasure workflow.
- Database backups are encrypted and restoration is exercised before launch.

## Product surfaces

The MVP contains seven primary screens:

1. Sign up, verification, and onboarding checklist.
2. Shop connection or manual catalog import.
3. Listing library with synchronization state.
4. Brand kit editor.
5. Creative studio with template/copy selection and preview.
6. Calendar with export or direct-publish status.
7. Analytics/experiments plus billing and account settings.

The interface is server-rendered and keyboard accessible. Expensive actions become
jobs and expose queued, running, succeeded, recoverable failure, and ambiguous outcome
states. Empty states lead to one concrete next action.

## Observability and support

Structured logs include correlation, organization, job, and provider names but exclude
content and credentials by default. Metrics cover request latency/errors, job queue age,
job outcomes, provider error classes, render duration, storage growth, webhook lag,
activation, trial conversion, and retained weekly usage.

Customer-visible failures use stable error codes. Staff can inspect redacted job and
audit timelines without opening a database console. Provider status incidents can pause
new publishing jobs without disabling downloads or editing.

## Testing strategy

- Preserve all desktop core tests as fast unit tests.
- Run SaaS integration tests against PostgreSQL, not SQLite.
- Test tenant isolation for every organization-owned route and service.
- Test OAuth state expiry/replay, token rotation, account collision, and disconnect.
- Test concurrent job claims, expired leases, deduplication, ambiguous writes, and
  webhook replay/out-of-order delivery.
- Test Paddle signature verification and every subscription state transition.
- Test signed asset authorization, storage cleanup, malicious image inputs, and quotas.
- Run a browser smoke path for signup, import, render, download, and billing sandbox.
- CI must run formatting/lint, types, unit/integration tests, dependency audit, migrations,
  static-file collection, and container build before merge.

## Delivery sequence

1. Add Django project, PostgreSQL settings, health endpoint, accounts, organization,
   membership, and tenant-isolation tests.
2. Extract explicit adapters around the existing render core and add object storage.
3. Add catalog/manual import, listing library, brand kit, creative jobs, and downloads.
4. Add Etsy OAuth/synchronization behind a feature flag and prepare Commercial Access.
5. Add calendar/export, then Pinterest OAuth/publishing behind an access flag.
6. Add Paddle checkout/webhooks, usage ledger, plan enforcement, and grace handling.
7. Add analytics/experiments, operational tooling, backup/restore, staging, and alpha.

Each step must leave a usable vertical slice and must not require a future service to
make its current behavior safe.

## Launch gates

- Ten invited Etsy sellers complete onboarding without developer assistance.
- At least seven create and download a useful creative in their first session.
- No unresolved cross-tenant, secret-handling, webhook, queue, or restore findings.
- Paddle sandbox lifecycle and production account verification are complete.
- Etsy Commercial Access is submitted; Pinterest Standard Access is submitted with a
  working sandbox recording.
- Privacy policy, terms, support, deletion, incident response, backups, and monitoring
  are live before accepting payment.

## Explicit non-goals for the first release

- Mobile apps, public API, browser extension, marketplace, team invitations, white-label
  domains, custom templates, video generation, multi-region active-active deployment,
  Redis/Celery, microservices, and automated Etsy listing mutation.
- Supporting every social network. Pinterest is the only publishing target.
- Replacing Etsy or Pinterest analytics with unverifiable AI estimates.
