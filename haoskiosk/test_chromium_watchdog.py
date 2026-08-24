import importlib.util
import pathlib
import sys
import types
import unittest

MODULE_PATH = pathlib.Path(__file__).with_name("chromium_watchdog.py")
browser_ctl = types.ModuleType("browser_ctl")
browser_ctl.ChromiumController = object
sys.modules["browser_ctl"] = browser_ctl
SPEC = importlib.util.spec_from_file_location("chromium_watchdog", MODULE_PATH)
WATCHDOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCHDOG)


class AutoLoginScriptTests(unittest.TestCase):
    def test_probe_walks_open_shadow_roots(self):
        script = WATCHDOG.build_login_probe_script()
        self.assertIn("node.shadowRoot", script)
        self.assertIn('autocomplete="username"', script)
        self.assertIn('autocomplete="current-password"', script)

    def test_polling_is_bounded_to_fifteen_seconds(self):
        self.assertEqual(WATCHDOG.AUTO_LOGIN_TIMEOUT_SECONDS, 15.0)
        self.assertLess(WATCHDOG.AUTO_LOGIN_POLL_SECONDS, 1.0)

    def test_credentials_are_not_logged(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("logger.info(HA_USERNAME", source)
        self.assertNotIn("logger.info(HA_PASSWORD", source)


if __name__ == "__main__":
    unittest.main()
