from unittest.mock import MagicMock

from playwright.sync_api import Error as PlaywrightError

from odoo.tests import TransactionCase, tagged

from ..services.browser_factory import BrowserFactory
from ..services.exceptions import BostaBrowserUnavailableError


@tagged("post_install", "-at_install")
class TestBrowserFactory(TransactionCase):

    def _factory_fixture(self):
        manager = MagicMock()
        playwright = manager.start.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        factory = BrowserFactory(playwright_factory=lambda: manager)
        return factory, manager, playwright, browser, context, page

    def test_browser_factory_applies_timeouts_and_headless_launch(self):
        factory, _manager, playwright, _browser, _context, page = self._factory_fixture()
        with factory.open(30):
            pass
        playwright.chromium.launch.assert_called_once_with(headless=True)
        page.set_default_timeout.assert_called_once_with(30000)
        page.set_default_navigation_timeout.assert_called_once_with(30000)

    def test_browser_factory_loads_optional_storage_state(self):
        factory, _manager, _playwright, browser, _context, _page = self._factory_fixture()
        state = {"cookies": [], "origins": []}
        with factory.open(15, storage_state=state):
            pass
        browser.new_context.assert_called_once_with(storage_state=state)

    def test_browser_factory_omits_storage_state_when_absent(self):
        factory, _manager, _playwright, browser, _context, _page = self._factory_fixture()
        with factory.open(15):
            pass
        browser.new_context.assert_called_once_with()

    def test_browser_factory_closes_every_resource_after_success(self):
        factory, _manager, playwright, browser, context, page = self._factory_fixture()
        with factory.open(30):
            pass
        page.close.assert_called_once_with()
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

    def test_browser_factory_closes_every_resource_after_body_failure(self):
        factory, _manager, playwright, browser, context, page = self._factory_fixture()
        with self.assertRaisesRegex(RuntimeError, "service failure"):
            with factory.open(30):
                raise RuntimeError("service failure")
        page.close.assert_called_once_with()
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

    def test_missing_chromium_maps_to_browser_unavailable(self):
        manager = MagicMock()
        playwright = manager.start.return_value
        playwright.chromium.launch.side_effect = PlaywrightError("missing executable")
        factory = BrowserFactory(playwright_factory=lambda: manager)
        with self.assertRaises(BostaBrowserUnavailableError):
            with factory.open(30):
                pass
        playwright.stop.assert_called_once_with()

    def test_cleanup_errors_do_not_replace_original_service_error(self):
        factory, _manager, _playwright, _browser, context, page = self._factory_fixture()
        page.close.side_effect = RuntimeError("cleanup page")
        context.close.side_effect = RuntimeError("cleanup context")
        with self.assertRaisesRegex(ValueError, "original"):
            with factory.open(30):
                raise ValueError("original")
