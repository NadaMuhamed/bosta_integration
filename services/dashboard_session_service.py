"""Validation and encrypted persistence helpers for Playwright storage state."""

import json

from odoo.exceptions import UserError

from . import crypto_service
from .exceptions import BostaSessionStateError


class DashboardSessionService:
    """Serialize, validate, encrypt, and decrypt browser storage state."""

    _TOP_LEVEL_KEYS = {"cookies", "origins"}
    _COOKIE_REQUIRED_KEYS = {"name", "value", "domain", "path"}

    @classmethod
    def validate_storage_state(cls, storage_state):
        if not isinstance(storage_state, dict):
            raise BostaSessionStateError()

        unexpected = set(storage_state) - cls._TOP_LEVEL_KEYS
        if unexpected:
            raise BostaSessionStateError()

        cookies = storage_state.get("cookies", [])
        origins = storage_state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise BostaSessionStateError()

        for cookie in cookies:
            if not isinstance(cookie, dict):
                raise BostaSessionStateError()
            if not cls._COOKIE_REQUIRED_KEYS.issubset(cookie):
                raise BostaSessionStateError()
            if not all(isinstance(cookie[key], str) for key in cls._COOKIE_REQUIRED_KEYS):
                raise BostaSessionStateError()

        for origin in origins:
            if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
                raise BostaSessionStateError()
            local_storage = origin.get("localStorage", [])
            if not isinstance(local_storage, list):
                raise BostaSessionStateError()
            for item in local_storage:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("value"), str)
                ):
                    raise BostaSessionStateError()
            if "indexedDB" in origin and not isinstance(origin["indexedDB"], list):
                raise BostaSessionStateError()

        try:
            json.dumps(storage_state, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raise BostaSessionStateError() from None
        return storage_state

    @classmethod
    def serialize_storage_state(cls, storage_state):
        cls.validate_storage_state(storage_state)
        return json.dumps(
            storage_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def encrypt_storage_state(cls, storage_state):
        serialized = cls.serialize_storage_state(storage_state)
        try:
            return crypto_service.encrypt_secret(serialized)
        except UserError:
            raise BostaSessionStateError(
                "The browser session could not be encrypted safely."
            ) from None

    @classmethod
    def decrypt_storage_state(cls, encrypted_value):
        try:
            serialized = crypto_service.decrypt_secret(encrypted_value)
            storage_state = json.loads(serialized)
        except (UserError, json.JSONDecodeError, TypeError, ValueError):
            raise BostaSessionStateError() from None
        return cls.validate_storage_state(storage_state)
