"""Remove obsolete Phase 2 Dashboard-authentication columns safely."""


_OBSOLETE_COLUMNS = (
    "dashboard_url",
    "dashboard_login",
    "encrypted_dashboard_password",
    "dashboard_password_configured",
    "credentials_updated_at",
    "credentials_updated_by",
    "encrypted_session_state",
    "session_configured",
    "session_status",
    "last_login_attempt_at",
    "last_successful_login_at",
    "last_session_validation_at",
    "last_auth_error",
    "browser_timeout_seconds",
)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'bosta_integration_config'
        """
    )
    existing = {row[0] for row in cr.fetchall()}
    removable = [column for column in _OBSOLETE_COLUMNS if column in existing]
    if not removable:
        return

    # Names are a fixed internal allow-list; no old values are selected or logged.
    for column in removable:
        cr.execute(f'ALTER TABLE "bosta_integration_config" DROP COLUMN IF EXISTS "{column}" CASCADE')
