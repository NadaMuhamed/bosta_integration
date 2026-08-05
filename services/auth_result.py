"""Safe authentication results exchanged between services and the Odoo model."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuthResult:
    """A sanitized result that never contains plaintext credentials or cookies."""

    success: bool
    status: str
    message: str
    encrypted_session_state: Optional[str] = None
    used_existing_session: bool = False
    attempted_fresh_login: bool = False
    clear_session: bool = False
