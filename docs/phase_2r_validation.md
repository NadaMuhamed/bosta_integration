# Phase 2R Validation Notes — hardened regression suite

## Completed in the supplied workspace

The Phase 2R implementation and tests were reviewed against the hardening
requirements. No Phase 3 models or business behavior were added.

### Pure API-client tests

A standalone unittest harness loaded only the HTTP client, exceptions, and test
helpers so it did not require an Odoo installation. It executed:

- API request construction and secret handling
- strict success-contract validation
- search/details contract failures
- HTTP mappings and bounded retry behavior
- retry-then-success behavior
- Retry-After edge cases
- API-key whitespace handling
- direct page-size/max-pages validation
- pagination boundaries
- dual-identity deduplication using `_id` and `trackingNumber`
- reordered repeated-page detection
- zero-progress pagination protection
- live-network guards

Result:

```text
32 tests run
0 failures
0 errors
```

No live Bosta request was made.

### Python compilation

Executed against the complete module tree:

```bash
python -m compileall -q /mnt/data/phase2r_hardening/bosta_integration
```

Result: PASS.

### XML validation

All module XML files were parsed with `lxml.etree`.

Result:

```text
3 XML files valid
```

### Cleanup regression checks

Static checks confirmed:

- no active runtime Playwright/Chromium references;
- no Dashboard authentication/session service imports;
- obsolete browser service files are absent;
- obsolete browser test modules are absent;
- no active Playwright or cryptography requirement;
- the configuration view contains the API action/state fields and none of the
  old Dashboard password/session fields/actions.

The complete test tree currently contains 62 `test_*` methods. Odoo-dependent
methods were syntax-compiled here but cannot be executed without the real Odoo
runtime/database.

## Implementation defects fixed by hardening

1. `BostaApiClient` no longer silently clamps `page_size` to 1500. Invalid
   values fail safely.
2. The client itself now enforces `1 <= max_pages <= 10000`.
3. Pagination deduplication checks both `_id` and non-empty `trackingNumber`;
   a match on either identifies a duplicate logical delivery.
4. Page fingerprints are order-insensitive when stable identifiers are
   available.
5. Any non-empty pagination page that contributes zero new logical deliveries
   fails with `BostaApiPaginationError` instead of continuing indefinitely.
6. The documented `success` marker is now intentionally fail-closed: it must be
   the boolean value `True` for successful search/details responses.

## Added/expanded regression coverage

The suite now explicitly covers:

- same tracking number with different `_id` values;
- same `_id` with changed tracking payload;
- same full page returned in a different order;
- changed duplicate payloads that produce zero pagination progress;
- invalid direct `page_size` and `max_pages` constructor/override values;
- blank and whitespace-only API keys;
- actual safe logging for expected and unexpected action failures;
- unexpected exception redaction;
- failed connection test preserving the previous successful-request timestamp;
- failure followed by success state reset;
- protected API-state fields on create as well as write;
- manager unlink and ordinary-user unlink denial;
- unauthorized cross-company create;
- 429/500/503 followed by success;
- connection failure followed by success;
- negative/invalid/huge Retry-After values;
- search HTTP 404 as a safe non-retry failure;
- separate details contract failures;
- strict search success marker behavior;
- obsolete file existence checks;
- requirements/view cleanup checks;
- current-schema obsolete-column verification;
- live-network guards in pure API tests.

## Still required in the real project environment

The supplied archive does not include the repository's Odoo runtime, Docker
Compose services, service names, database names, or an existing Phase 2
database. Therefore these acceptance gates cannot truthfully be marked PASS in
this workspace:

1. Phase 2 database (`18.0.3.0.0`) -> Phase 2R (`18.0.4.0.0`) Odoo upgrade;
2. post-upgrade migration verification on that existing database;
3. focused Phase 2R Odoo test execution;
4. complete `bosta_integration` Odoo suite;
5. final Odoo `0 failed / 0 errors` result;
6. real Bosta API acceptance test.

Use the repository's actual Docker/Odoo service and database names. Do not
invent substitutes. After the upgrade, a safe schema-only verification query is:

```sql
SELECT column_name
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND table_name = 'bosta_integration_config'
  AND column_name IN (
    'dashboard_url',
    'dashboard_login',
    'encrypted_dashboard_password',
    'dashboard_password_configured',
    'credentials_updated_at',
    'credentials_updated_by',
    'encrypted_session_state',
    'session_configured',
    'session_status',
    'last_login_attempt_at',
    'last_successful_login_at',
    'last_session_validation_at',
    'last_auth_error',
    'browser_timeout_seconds'
  );
```

Expected: zero rows. This query checks only column names and does not expose old
secret/session values.

## Manual Bosta API acceptance test

Configure the secret only in the Odoo server environment:

```text
BOSTA_API_KEY=<configured securely>
```

Then restart/recreate the Odoo service, open **Bosta Integration ->
Configuration**, verify **API Key Configured = True**, run **Test API
Connection**, verify connected timestamps/status, and confirm no key or
Authorization value appears in logs. Perform one controlled paginated search
and one controlled details request. Do not create Odoo orders in Phase 2R.

If POST delivery search returns HTTP 403, verify Bosta API-key permissions/scope
before treating it as an application defect.

## Git

No commit was created.

Proposed commit message:

```text
refactor(bosta): replace dashboard auth with secure API client
```
