"""Playwright browser lifecycle management for Bosta Dashboard services."""

from contextlib import contextmanager

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .exceptions import BostaBrowserUnavailableError


class BrowserResources:
    """Own Playwright resources and close them safely in reverse order."""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        for resource in (self.page, self.context, self.browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                # Cleanup must never replace the original safe result.
                pass
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass


class BrowserFactory:
    """Create isolated Chromium sessions with deterministic timeouts."""

    def __init__(self, playwright_factory=sync_playwright):
        self._playwright_factory = playwright_factory

    @contextmanager
    def open(self, timeout_seconds, storage_state=None):
        resources = BrowserResources()
        try:
            resources.playwright = self._playwright_factory().start()
            resources.browser = resources.playwright.chromium.launch(headless=True)
            context_values = {}
            if storage_state is not None:
                context_values["storage_state"] = storage_state
            resources.context = resources.browser.new_context(**context_values)
            resources.page = resources.context.new_page()
            timeout_ms = int(timeout_seconds * 1000)
            resources.page.set_default_timeout(timeout_ms)
            resources.page.set_default_navigation_timeout(timeout_ms)
        except BostaBrowserUnavailableError:
            resources.close()
            raise
        except PlaywrightError:
            resources.close()
            raise BostaBrowserUnavailableError() from None
        except Exception:
            resources.close()
            raise BostaBrowserUnavailableError() from None

        try:
            yield resources
        finally:
            resources.close()
