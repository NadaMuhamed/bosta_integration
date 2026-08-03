from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..services import crypto_service


@tagged("post_install", "-at_install")
class TestBostaCryptoService(TransactionCase):

    def test_encrypt_and_decrypt_round_trip(self):
        secret = "phase1-test-password"
        key = Fernet.generate_key().decode("ascii")

        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: key},
            clear=True,
        ):
            encrypted = crypto_service.encrypt_secret(secret)
            decrypted = crypto_service.decrypt_secret(encrypted)

        self.assertNotEqual(encrypted, secret)
        self.assertEqual(decrypted, secret)
        self.assertNotIn(secret, encrypted)

    def test_missing_encryption_key_is_safe(self):
        secret = "must-not-leak"
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(UserError) as caught:
                crypto_service.encrypt_secret(secret)

        message = str(caught.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn(crypto_service.ENCRYPTION_KEY_ENV, message)

    def test_invalid_encryption_key_is_rejected(self):
        secret = "must-not-leak"
        invalid_key = "not-a-fernet-key"
        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: invalid_key},
            clear=True,
        ):
            with self.assertRaises(UserError) as caught:
                crypto_service.encrypt_secret(secret)

        message = str(caught.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn(invalid_key, message)


    def test_crypto_methods_do_not_emit_logs(self):
        secret = "log-redaction-secret"
        key = Fernet.generate_key().decode("ascii")
        with patch("logging.Logger._log") as log_call:
            with patch.dict(
                "os.environ",
                {crypto_service.ENCRYPTION_KEY_ENV: key},
                clear=True,
            ):
                encrypted = crypto_service.encrypt_secret(secret)
                self.assertEqual(
                    crypto_service.decrypt_secret(encrypted),
                    secret,
                )

        self.assertFalse(log_call.called)

    def test_is_encryption_configured_validates_key(self):
        valid_key = Fernet.generate_key().decode("ascii")
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(crypto_service.is_encryption_configured())
        with patch.dict(
            "os.environ",
            {crypto_service.ENCRYPTION_KEY_ENV: valid_key},
            clear=True,
        ):
            self.assertTrue(crypto_service.is_encryption_configured())
