# Bosta Dashboard Authentication — Phase 2

## Scope

Phase 2 authenticates an authorized Odoo manager to the Bosta Business Dashboard,
validates or creates a browser session, and stores only encrypted Playwright
storage state. It does not read, parse, normalize, or import orders.

## Runtime architecture

```text
bosta.integration.config
        ↓
DashboardAuthService
        ↓
DashboardSessionService
        ↓
BrowserFactory
        ↓
Playwright Chromium
```

Odoo model actions enforce manager and company access. Browser navigation,
locator selection, authentication detection, and resource cleanup stay in the
service layer.

## Configuration requirements

A login test requires:

- an active configuration;
- an HTTPS `business.bosta.co/orders` URL;
- a saved Dashboard login;
- a saved encrypted password;
- a valid `BOSTA_DASHBOARD_ENCRYPTION_KEY`; and
- a browser timeout between 5 and 120 seconds.

## Session lifecycle

1. If encrypted storage state exists, decrypt and validate it.
2. Create an isolated browser context using that state.
3. Navigate to the configured Dashboard URL and validate authenticated access.
4. If valid, update only the session-validation timestamp.
5. If invalid or expired, close the context and make one fresh credential login.
6. On success, call `context.storage_state()`, validate the dictionary, serialize
   it deterministically, encrypt it with Fernet, and store only ciphertext.
7. On failure, save only a safe status and sanitized message.

No storage-state JSON file is used by the normal implementation.

## Locator contract

Generated Ant Design classes and generated IDs are not used. Locator priority is:

1. accessible role and visible English/Arabic name;
2. associated label;
3. input type;
4. autocomplete attribute;
5. stable name attribute;
6. confirmed test ID;
7. stable Bosta-specific structure.

If the login contract is not recognized, authentication stops with
`contract_changed`. The implementation never fills the first arbitrary input or
clicks the first arbitrary button.

## Safe statuses

- `not_configured`
- `authenticated`
- `expired`
- `invalid_credentials`
- `otp_required`
- `captcha_required`
- `blocked`
- `connection_failed`
- `browser_unavailable`
- `contract_changed`
- `unknown_error`

OTP and CAPTCHA are detected and reported, never bypassed.

## Docker requirements

The Odoo web image must install both the Python package and Chromium browser
bundle reproducibly. A typical project-root Dockerfile step is:

```dockerfile
RUN pip3 install --no-cache-dir -r /path/to/requirements.txt \
    && playwright install --with-deps chromium
```

Do not install browsers manually in an already-running production container as
the permanent deployment solution.

## Manual acceptance

1. Confirm `import playwright` inside the web container.
2. Confirm `p.chromium.launch(headless=True)` succeeds.
3. Upgrade `bosta_integration`.
4. Log in as a Bosta Integration Manager.
5. Save a real Dashboard login and a new password.
6. Reopen the record and confirm the password input is empty.
7. Press **Test Login** and confirm `Authenticated` and `Session Configured`.
8. Press **Test Login** again and confirm saved-session reuse.
9. Press **Reset Dashboard Session** and confirm the password remains configured.
10. Press **Test Login** and confirm a new session is created.

Stop if Bosta requires OTP or CAPTCHA. Do not read or import any order during the
acceptance test.
