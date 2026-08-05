# Bosta Integration Security

## Encryption boundary

Dashboard passwords and Playwright storage state are encrypted with
`cryptography.fernet.Fernet`. The key is read only from:

`BOSTA_DASHBOARD_ENCRYPTION_KEY`

The key is not stored in PostgreSQL, Odoo system parameters, source control,
logs, screenshots, or browser traces.

## Password lifecycle

- `dashboard_password_input` is non-stored and masked.
- Non-empty input is encrypted before record persistence.
- Blank input keeps the current encrypted password.
- Plaintext exists only as a temporary local variable immediately before form
  filling.
- Plaintext is not returned by a service, stored in a field, logged, added to an
  exception, traced, or screenshotted.
- **Clear Saved Password** removes the password and disables the integration.

## Session lifecycle

- `context.storage_state()` is validated as a JSON-compatible dictionary.
- Deterministic JSON is encrypted before storage.
- Only ciphertext is persisted in `encrypted_session_state`.
- Decrypted storage state is used only to initialize an isolated browser context.
- Cookies, tokens, authorization headers, and raw storage-state JSON are never
  included in views, notifications, chatter, or errors.
- **Reset Dashboard Session** clears only session ciphertext and safe status data;
  it keeps the configured login, password, and integration-enabled value.

## Protected internal fields

Normal callers cannot directly write:

- `encrypted_dashboard_password`
- `credentials_updated_at`
- `credentials_updated_by`
- `encrypted_session_state`
- `session_status`
- `last_login_attempt_at`
- `last_successful_login_at`
- `last_session_validation_at`
- `last_auth_error`

Caller-controlled context values do not bypass this protection. Authorized model
actions use controlled `super().write()` calls only after manager and company
access checks.

## Browser safety

- Browser automation is outside Odoo model methods.
- Chromium runs headless in an isolated non-persistent context.
- Page, context, browser, and Playwright are closed on success and failure.
- No fixed sleeps are used.
- Generated classes and arbitrary first-input/button selection are forbidden.
- OTP and CAPTCHA are detected and reported, never bypassed.
- The implementation fails closed with `contract_changed` when the login page is
  not recognized.

## Repository exclusions

`.gitignore` excludes local environments, storage-state files, Playwright auth
profiles, reports, traces, Python caches, and operating-system metadata.
