"""Authenticated encryption helpers for Bosta Dashboard secrets.

The encryption key is supplied only through the server environment. This module
never logs keys, plaintext secrets, or encrypted tokens.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from odoo import _
from odoo.exceptions import UserError


ENCRYPTION_KEY_ENV = "BOSTA_DASHBOARD_ENCRYPTION_KEY"


def get_encryption_key():
    """Return a validated Fernet key from the server environment."""
    raw_key = os.environ.get(ENCRYPTION_KEY_ENV)
    if not raw_key:
        raise UserError(
            _("Bosta Dashboard encryption is not configured on the server.")
        )

    try:
        key = raw_key.encode("ascii")
        Fernet(key)
    except (UnicodeEncodeError, ValueError, TypeError):
        raise UserError(
            _("Bosta Dashboard encryption is configured incorrectly on the server.")
        ) from None

    return key


def encrypt_secret(plaintext):
    """Encrypt a non-empty text secret with authenticated encryption."""
    if not isinstance(plaintext, str) or plaintext == "":
        raise UserError(_("A non-empty Dashboard password is required."))

    token = Fernet(get_encryption_key()).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(encrypted_value):
    """Decrypt a token while keeping failures free from secret material."""
    if not encrypted_value:
        raise UserError(_("No Bosta Dashboard password is configured."))

    try:
        plaintext = Fernet(get_encryption_key()).decrypt(
            encrypted_value.encode("ascii")
        )
        return plaintext.decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, ValueError, TypeError):
        raise UserError(
            _("The saved Bosta Dashboard password could not be decrypted safely.")
        ) from None


def is_encryption_configured():
    """Return whether a valid server-side Fernet key is available."""
    try:
        get_encryption_key()
    except UserError:
        return False
    return True
