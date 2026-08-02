# PinForge Catalog and Render Vertical Slice Plan

> **Execution:** Use `executing-plans` inline. Do not dispatch subagents.

**Goal:** Let an authenticated Etsy seller create a manual shop listing with private
images, save a tenant-specific brand kit, queue a Pinterest creative, render it with
the existing Pillow engine, and download the result through an authorized endpoint.

**Architecture:** Keep the Django monolith and reuse `pinforge` domain/rendering code.
All catalog, creative, asset, and job rows carry a non-null organization foreign key.
Files are stored through Django's private storage API under UUID-only keys. A durable
PostgreSQL job row is claimed with `select_for_update(skip_locked=True)` and processed
by a management-command worker; tests may invoke one job directly. No Redis, Celery,
SPA, public media URL, or provider credentials are introduced.

**Tech stack:** Django 5.2, PostgreSQL 17, Pillow, Django storage, pytest-django,
server-rendered HTML.

## Global constraints

- Preserve every desktop and SaaS foundation test.
- Every organization-owned lookup is scoped by `request.organization`.
- Cross-tenant listing, creative, job, and asset access returns 404.
- Uploaded files are bounded by bytes, decoded pixels, dimensions, and accepted MIME.
- Storage keys contain organization/record UUIDs, never seller filenames.
- Downloads are authenticated streaming responses; `MEDIA_URL` is not public.
- Render payloads contain IDs only and never credentials or seller-provided paths.
- A render job is idempotent: an existing ready asset makes a retry succeed without
  producing a second asset.
- PostgreSQL is the production and CI queue implementation; SQLite remains an explicit
  local-test switch.

---

## Task 1: Add tenant-owned catalog and creative schema

**Files:**

- Modify: `src/pinforge_web/models.py`
- Create: `src/pinforge_web/migrations/0002_catalog_creatives_jobs.py`
- Test: `tests/saas/test_catalog_models.py`

1. Write model tests for non-null organization ownership, tenant uniqueness, money in
   minor units, template/status choices, job deduplication, and cascade behavior.
2. Add `Shop`, `Listing`, `ListingImage`, `BrandKit`, `Creative`, `CreativeAsset`, and
   `Job` with UUID primary keys and timestamps.
3. Add database checks for non-negative price/attempts/size and uniqueness for source
   IDs, image positions, one asset per creative, and organization job deduplication.
4. Generate the migration and run `makemigrations --check --dry-run`.
5. Commit: `feat: add tenant catalog and creative schema`.

## Task 2: Validate and store private listing images

**Files:**

- Create: `src/pinforge_web/uploads.py`
- Modify: `src/pinforge_saas/settings.py`
- Test: `tests/saas/test_uploads.py`

1. Write failing tests for valid PNG/JPEG, oversized bytes, excessive pixels,
   unsupported formats, truncated images, and UUID-only storage names.
2. Implement a pure validation function that preserves the upload cursor, calls
   Pillow verification and decode, and returns MIME, dimensions, size, and SHA-256.
3. Implement UUID-based `upload_to` functions under
   `organizations/<org-id>/listings/<image-id>/original.<ext>` and
   `organizations/<org-id>/creatives/<asset-id>/render.png`.
4. Configure a private filesystem root for development/tests. Production must require
   an explicitly configured private media root until the S3 backend is introduced;
   no public media route is added.
5. Commit: `feat: validate private catalog uploads`.

## Task 3: Add atomic manual catalog services

**Files:**

- Create: `src/pinforge_web/catalog.py`
- Test: `tests/saas/test_catalog_services.py`

1. Test an atomic listing creation with one to five images, normalized currency/tags,
   minor-unit price conversion, stable ordering, and organization ownership.
2. Test rollback and storage cleanup when any image is invalid or persistence fails.
3. Implement `get_or_create_manual_shop` and `create_manual_listing`; accept an
   organization explicitly and never trust an organization ID from form input.
4. Commit: `feat: add atomic manual catalog import`.

## Task 4: Add catalog and brand-kit product screens

**Files:**

- Modify: `src/pinforge_web/forms.py`
- Modify: `src/pinforge_web/views.py`
- Modify: `src/pinforge_web/urls.py`
- Modify: `src/pinforge_web/templates/base.html`
- Create: `src/pinforge_web/templates/pinforge_web/listing_list.html`
- Create: `src/pinforge_web/templates/pinforge_web/listing_form.html`
- Create: `src/pinforge_web/templates/pinforge_web/listing_detail.html`
- Create: `src/pinforge_web/templates/pinforge_web/brand_kit.html`
- Modify: `pyproject.toml`
- Test: `tests/saas/test_catalog_views.py`

1. Test login, CSRF, tenant scoping, validation errors, successful upload redirect,
   brand color/font validation, and cross-tenant 404s.
2. Add accessible multipart listing and brand-kit forms. The active organization is
   always injected by the view/service.
3. Add list/create/detail and brand-kit routes plus product navigation.
4. Extend package data so every new template is present in the wheel.
5. Commit: `feat: add manual catalog and brand kit screens`.

## Task 5: Add durable render queue semantics

**Files:**

- Create: `src/pinforge_web/jobs.py`
- Test: `tests/saas/test_jobs.py`

1. Test deduplicated enqueue, due-order claim, lease fields, expired-lease recovery,
   success, bounded retries, terminal failure, and organization consistency.
2. Implement short-transaction `enqueue_render`, `claim_due_job`, `finish_job`, and
   `fail_job` functions. Use `select_for_update(skip_locked=True)` when supported.
3. Keep error details sanitized and bounded; expose a stable error code.
4. Commit: `feat: add durable PostgreSQL render queue`.

## Task 6: Adapt the existing renderer and persist assets

**Files:**

- Create: `src/pinforge_web/rendering.py`
- Create: `src/pinforge_web/management/__init__.py`
- Create: `src/pinforge_web/management/commands/__init__.py`
- Create: `src/pinforge_web/management/commands/run_jobs.py`
- Test: `tests/saas/test_render_jobs.py`

1. Test conversion from database records to `SourceProduct`, `BrandKit`, and
   `PinCopy`, a 1000x1500 PNG result, storage checksum/size, idempotent retry, and a
   sanitized failure state.
2. Copy private source images to an isolated temporary directory, invoke the existing
   `RenderEngine`, validate output, save a `CreativeAsset`, and remove temporary files.
3. Implement `run_jobs --once` and bounded continuous polling; claim commits before
   rendering and result updates happen in a new transaction.
4. Commit: `feat: render queued SaaS creatives`.

## Task 7: Add creative queue and authorized download screens

**Files:**

- Modify: `src/pinforge_web/forms.py`
- Modify: `src/pinforge_web/views.py`
- Modify: `src/pinforge_web/urls.py`
- Modify: `src/pinforge_web/templates/pinforge_web/listing_detail.html`
- Create: `src/pinforge_web/templates/pinforge_web/creative_detail.html`
- Test: `tests/saas/test_creative_views.py`

1. Test template selection, copy bounds, queue deduplication, status display, ready
   PNG streaming headers, and cross-tenant asset denial.
2. Add POST-only creative creation and an authenticated status/detail page.
3. Stream downloads from private storage with attachment headers, no-store caching,
   nosniff, and a filename derived from internal UUIDs.
4. Commit: `feat: add creative workflow and private downloads`.

## Task 8: Verify production behavior and publish

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Test: all tests

1. Run PostgreSQL migrations and the full test suite in CI.
2. Add a CI management-command render smoke using a generated fixture image.
3. Run full pytest/coverage, Ruff, mypy, dependency audit, migration drift, Django
   deploy check, wheel-template inspection, and Windows package smoke.
4. Document `web`, `worker`, migration, private media, and local catalog workflow.
5. Commit: `ci: verify catalog and render vertical slice`.
6. Push the branch and update draft PR #2 with the new scope and verification.

