# PinForge Growth Suite Design

## Goal

Add five usable growth features to the existing local Python desktop application:
performance learning, A/B tests, a visual content calendar, isolated account
profiles, and SEO/trend recommendations. Keep SQLite as the source of truth and
avoid adding a cloud backend or new runtime dependency.

## Decisions

The suite is local-first. Pinterest remains the authority for remote Pin metrics;
PinForge stores dated snapshots and also accepts CSV imports so analytics remains
usable when an app lacks the required Pinterest analytics access. Trend terms use
the same CSV boundary because Pinterest does not expose a general public Trends
catalog through API v5. Suggestions are deterministic and explain their scores;
AI remains optional.

Profiles isolate settings, OAuth tokens, queues, experiments, and metrics with a
separate data directory and SQLite database per profile. The existing installation
remains the `default` profile without moving data. New profiles start as copies of
the active non-secret settings, while their credentials and OAuth tokens use
profile-qualified secret names. Switching profile closes the current runtime and
opens the selected profile before any further provider operation.

## Data model

Schema v4 inside each profile database adds:

- `pin_metrics`: draft/remote Pin, capture day, impressions, saves, Pin
  clicks and outbound clicks; repeated imports upsert the same daily snapshot.
- `experiments`: product, name, status and winner variant.
- `experiment_variants`: experiment, draft and variant label.
- `trend_terms`: term, optional vertical, score, source and observation day.

The root `profiles.json` registry stores stable profile IDs, display names and the
active selection; it contains no secrets. Aggregate reports use
weighted engagement `(saves*4 + outbound_clicks*5 + pin_clicks*2) / impressions`
with a small-sample confidence factor. Template, hour, weekday and keyword reports
share this calculation. The winner of an A/B test requires at least two variants
with impressions and is selected by confidence-adjusted engagement.

## Feature flows

### Performance learning

The Pinterest client requests organic metrics for published Pins over a bounded date
range and normalizes supported metric names. CSV import uses a documented header and
strict non-negative integer validation. The Insights screen ranks templates, hours,
weekdays and variants, showing impressions and engagement rather than opaque advice.

### A/B tests

For a selected product, PinForge creates two or three variants from existing
templates/copy generation, records one experiment, exports normal drafts, and spaces
them across different schedule slots. No variant bypasses the existing approval,
rendering, queue or publish safety path. The experiment can calculate and persist its
winner after metrics arrive.

### Content calendar

A month view presents queued and published drafts by local calendar day. Moving an
item changes only its scheduled day while retaining the configured local slot and
converting through the existing DST-safe planner. An auto-fill action distributes
ready drafts over open slots while respecting the daily limit and profile scope.

### Multi-account profiles

The header exposes the active profile and create/switch actions. A profile contains
brand, Etsy/Pinterest application fields, board mappings, limits and timezone.
Secrets are never copied. Existing data and tokens stay attached to `default`.
Deleting profiles is deliberately excluded to avoid accidental loss; it can be added
later with an explicit archive design.

### SEO and trends

Suggestions combine product tags/title tokens, imported trend scores, and keyword
performance from published Pin titles/descriptions. Results include a score and
reason: trend strength, historical engagement, product relevance and saturation
penalty. The user can copy recommended keywords into the AI generation target list.

## Interfaces

- GUI: new `Büyüme` tab containing Insights, A/B test controls, trend import/SEO
  recommendations and a month calendar. Profile selection stays in the header.
- CLI: `metrics-sync`, `metrics-import`, `insights`, `experiment-create`,
  `experiment-winner`, `calendar-fill`, `profiles`, `profile-create`,
  `profile-switch`, `trends-import`, and `seo-suggest`.
- CSV metrics columns: `pin_id,date,impressions,saves,pin_clicks,outbound_clicks`.
- CSV trends columns: `term,score,vertical,date`.

## Safety and errors

All imports are size/row bounded and atomic. Profile IDs, terms, dates and numeric
values are validated. Analytics GET requests may safely retry, but their failure
never affects publishing. Profile switching is process-local and auditable. Existing
schema migration uses the established backup-before-migrate path.

## Verification

Tests cover v3-to-v4 migration, profile isolation and token names, metrics upsert and
ranking, experiment winner selection, CSV rejection/rollback, SEO scoring, calendar
daily limits and DST behavior, API normalization, and GUI construction. CI continues
to run lint, mypy, dependency audit, coverage and Windows packaging.

## Deliberate limits

This release does not scrape Pinterest Trends, buy ads, claim causal significance,
or create a hosted analytics service. Metrics are observational. Calendar drag/drop
changes dates but not arbitrary minutes; configured schedule slots remain the single
source of truth.
