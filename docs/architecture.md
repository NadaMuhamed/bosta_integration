# Bosta Integration Architecture — Phase 2R

## Scope

`bosta_integration` is an independent Odoo 18 module. Phase 2R replaces the old
browser-based Bosta Dashboard authentication architecture with direct Bosta API
access.

Runtime flow:

```text
bosta.integration.config
        ↓
BostaApiClient
        ↓
https://app.bosta.co
        ↓
Bosta API
```

Phase 2R intentionally does not create Odoo orders, customers, sale orders,
stock movements, returns, profit records, or scheduled synchronization jobs.

## Configuration

The configuration model remains `bosta.integration.config` and remains scoped to
one record per company. Only Bosta Integration Managers can access configuration
records, and the existing company record rule continues to isolate records by
allowed companies.

API configuration fields:

- `api_base_url` — restricted to `https://app.bosta.co`.
- `api_key_env_var` — stores only the environment variable name.
- `api_key_configured` — non-stored boolean indicating whether that variable is
  currently populated.
- `request_timeout_seconds` — bounded request timeout.
- `page_size` — bounded to 1..1500.
- `max_pages` — pagination safety cap.

## API client

`services/bosta_api_client.py` owns HTTP transport responsibilities only:

- environment-only API key lookup at request time;
- request headers and URLs;
- POST delivery search;
- GET delivery details;
- JSON and basic contract validation;
- bounded retries for temporary failures;
- safe exception mapping;
- full-delivery pagination with loop protection and de-duplication.

The client supports injected HTTP transport and sleep functions so automated
tests never require the live Bosta API and never wait for real retry backoff.

## Pagination

Search starts at page 1 and uses the configured page size, up to 1500. Pagination
stops when any reliable completion condition is reached:

1. the API returns an empty delivery list;
2. the returned delivery count is less than the requested limit; or
3. reliable pagination metadata explicitly indicates the final page.

A full page is never treated as the historical end. If page 1 returns exactly
1500 deliveries, page 2 is requested, and so on.

Safety controls include:

- configured maximum pages;
- repeated-page fingerprint detection;
- de-duplication by `_id`, then `trackingNumber`;
- deterministic preservation of the first occurrence seen.

If pagination clearly stops progressing, the client raises a safe pagination
error instead of silently returning a falsely complete result.

## Delivery details

Delivery details use:

```text
GET /api/v2/deliveries/business/{tracking_number}
```

Tracking numbers are treated as text and safely URL-encoded. Phase 2R validates
only the basic response envelope and returns the delivery payload; full Bosta
business-field normalization belongs to a later phase.

## Upgrade

Version `18.0.4.0.0` includes a post-migration that removes obsolete stored
Dashboard credential/session columns using a fixed allow-list. The migration is
idempotent and does not read or print old encrypted values.
