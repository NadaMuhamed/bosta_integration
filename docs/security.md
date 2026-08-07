# Bosta Integration Security — Phase 2R

## API key boundary

The Bosta API key is never persisted by Odoo.

```text
BOSTA_API_KEY
    ↓ environment only
BostaApiClient
    ↓
Authorization header
    ↓
Bosta API
```

The configuration stores only the environment variable name (default
`BOSTA_API_KEY`). Multi-company installations may configure a different
non-secret variable name for another company.

The key must never be stored in PostgreSQL, `ir.config_parameter`, model fields,
notifications, logs, exceptions, raw response storage, fixtures, screenshots, or
documentation. The non-stored `api_key_configured` field exposes only whether a
non-empty value currently exists.

## URL and configuration validation

The base URL is restricted to the official HTTPS origin `https://app.bosta.co`.
HTTP downgrade, embedded credentials, unexpected ports, paths, query strings,
fragments, unrelated hosts, and deceptive subdomains are rejected.

Environment-variable names are restricted to uppercase letters, digits, and
underscores and may not start with a digit.

Request timeout, page size, and maximum page count are bounded to safe values.
The integration cannot be enabled unless the environment key currently exists.
If the key later disappears, API calls fail closed with a configuration error.

## Error redaction

The API exception hierarchy contains only safe categories/messages. User-facing
errors and stored `last_api_error` values never contain raw response bodies,
request headers, the Authorization header, or environment secrets.

HTTP errors are mapped to safe categories for authentication failure, permission
denial, rate limiting, timeout, connection failure, temporary server errors,
contract errors, and unknown failures.

## Retry safety

Only temporary failures are retried: HTTP 429, 500, 502, 503, 504, and bounded
connection failures. Authentication, permission, malformed-contract, and invalid
configuration failures are not retried blindly.

Retry count and backoff are bounded. `Retry-After` is accepted only when numeric
and is capped. Sleep is injectable for deterministic tests.

## Access control and companies

Configuration remains manager-only. The existing allowed-company record rule is
preserved. Manager actions explicitly verify group membership and write access.
Ordinary Bosta Integration Users cannot access the configuration model through
ACLs and cannot invoke Test API Connection.

## Removed browser architecture

Phase 2R contains no active Playwright, Chromium, Dashboard password, password
encryption, browser storage-state, browser-session, CAPTCHA/OTP automation, or
Dashboard login service code. Existing stored Dashboard columns are removed by
the version migration rather than left behind in PostgreSQL.
