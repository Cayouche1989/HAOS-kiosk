#!/usr/bin/env python3
"""Chromium kiosk watchdog.

Handles the HA-specific browser behavior previously implemented in Luakit:
auto-login, persistent kiosk browser id, HA theme/sidebar storage, and timed
reloads. This keeps the Chromium runtime thin while preserving the HA-focused
workflow expected by the add-on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

from browser_ctl import ChromiumController


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: [%(filename)s:%(funcName)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HA_LOGIN_URL = (os.getenv("HA_LOGIN_URL") or "http://127.0.0.1:8123").rstrip("/")
HA_LOGIN_URL_BASE = re.match(r"^(https?://[%w%.%-]+(?::\d+)?)", HA_LOGIN_URL)
HA_LOGIN_URL_BASE = HA_LOGIN_URL_BASE.group(1).rstrip("/") if HA_LOGIN_URL_BASE else HA_LOGIN_URL
HA_USERNAME = os.getenv("HA_USERNAME") or ""
HA_PASSWORD = os.getenv("HA_PASSWORD") or ""
LOGIN_DELAY_MS = int(float(os.getenv("LOGIN_DELAY") or "1") * 1000)
BROWSER_REFRESH = max(0, int(os.getenv("BROWSER_REFRESH") or "600"))
DARK_MODE = (os.getenv("DARK_MODE") or "true").strip().lower() == "true"
RAW_SIDEBAR = (os.getenv("HA_SIDEBAR") or "").strip().lower()
RAW_THEME = (os.getenv("HA_THEME") or "").strip()
POLL_INTERVAL = 2.0
HARD_RELOAD_FREQ = 10

SIDEBAR_MAP = {
    "full": "",
    "none": '"always_hidden"',
    "narrow": '"auto"',
    "": "",
}


def normalize_sidebar(raw_sidebar: str) -> str:
    """Return the HA localStorage representation for dockedSidebar."""
    return SIDEBAR_MAP.get(raw_sidebar, "")


def normalize_theme(raw_theme: str, dark_mode: bool) -> str:
    """Return the HA localStorage representation for selectedTheme."""
    theme = raw_theme.strip()
    if theme in {"", "{}", "Home Assistant"}:
        return '{"dark":true}' if dark_mode else '{"dark":false}'
    if theme[0] not in {'"', "'", "{"}:
        return json.dumps(theme)
    return theme


def build_auto_login_script() -> str:
    """Return JS that fills HA auth fields and submits the form."""
    return f"""
(async () => {{
  const username = {json.dumps(HA_USERNAME)};
  const password = {json.dumps(HA_PASSWORD)};
  const deadline = Date.now() + 15000;
  await new Promise(resolve => window.setTimeout(resolve, {LOGIN_DELAY_MS}));
  while (Date.now() < deadline) {{
    try {{
      const usernameField = document.querySelector('input[autocomplete="username"]');
      const passwordField = document.querySelector('input[autocomplete="current-password"]');
      const checkbox = document.querySelector('ha-checkbox');
      const submitButton = document.querySelector('ha-button[type="submit"], mwc-button[type="submit"], button[type="submit"], ha-button, mwc-button');
      if (!usernameField || !passwordField || !submitButton) {{
        await new Promise(resolve => window.setTimeout(resolve, 500));
        continue;
      }}
      usernameField.value = username;
      usernameField.dispatchEvent(new Event('input', {{ bubbles: true }}));
      passwordField.value = password;
      passwordField.dispatchEvent(new Event('input', {{ bubbles: true }}));
      if (checkbox) {{
        checkbox.setAttribute('checked', '');
        checkbox.dispatchEvent(new Event('change', {{ bubbles: true }}));
      }}
      submitButton.click();
      return {{ ok: true, status: 'submitted' }};
    }} catch (error) {{
      return {{ ok: false, status: 'error' }};
    }}
  }}
  return {{ ok: false, status: 'timeout' }};
}}()
"""


def build_settings_script(sidebar: str, theme: str) -> str:
    """Return JS that applies HA localStorage settings."""
    return f"""
(() => {{
  try {{
    let changed = false;
    localStorage.setItem('browser_mod-browser-id', 'haos_kiosk');

    const sidebar = {json.dumps(sidebar)};
    const currentSidebar = localStorage.getItem('dockedSidebar') || '';
    if (sidebar !== currentSidebar) {{
      if (sidebar) {{
        localStorage.setItem('dockedSidebar', sidebar);
      }} else {{
        localStorage.removeItem('dockedSidebar');
      }}
      changed = true;
    }}

    const theme = {json.dumps(theme)};
    const currentTheme = localStorage.getItem('selectedTheme') || '';
    if (theme !== currentTheme) {{
      if (theme) {{
        localStorage.setItem('selectedTheme', theme);
      }} else {{
        localStorage.removeItem('selectedTheme');
      }}
      changed = true;
    }}

    return {{
      ok: true,
      changed,
      sidebar: localStorage.getItem('dockedSidebar') || '',
      theme: localStorage.getItem('selectedTheme') || '',
    }};
  }} catch (error) {{
    return {{ ok: false, changed: false, error: String(error) }};
  }}
}})();
"""


def is_auth_page(url: str) -> bool:
    """Return True if URL looks like HA auth page."""
    return bool(re.match(rf"^{re.escape(HA_LOGIN_URL_BASE)}/auth/authorize\?response_type=code", url))


def is_ha_page(url: str) -> bool:
    """Return True if URL belongs to the current HA instance."""
    return bool(url and (url + "/").startswith(HA_LOGIN_URL_BASE + "/"))


def extract_evaluate_value(result: dict[str, Any]) -> Any:
    """Unwrap Runtime.evaluate returnByValue payload."""
    runtime_result = result.get("result")
    if isinstance(runtime_result, dict) and "value" in runtime_result:
        return runtime_result["value"]
    return runtime_result


async def main() -> None:
    """Start watchdog loop."""
    controller = ChromiumController()
    sidebar = normalize_sidebar(RAW_SIDEBAR)
    theme = normalize_theme(RAW_THEME, DARK_MODE)
    auto_login_script = build_auto_login_script()
    settings_script = build_settings_script(sidebar, theme)

    logger.info(
        "Chromium watchdog started: HA_LOGIN_URL=%s LOGIN_DELAY=%.1fs REFRESH=%ss SIDEBAR=%s THEME=%s",
        HA_LOGIN_URL,
        LOGIN_DELAY_MS / 1000,
        BROWSER_REFRESH,
        sidebar,
        theme,
    )

    last_url = ""
    last_auth_url = ""
    last_settings_url = ""
    last_reload_at = time.monotonic()
    reload_count = 0

    while True:
        try:
            target = await controller.get_page_target()
            url = str(target.get("url") or "")
            if url and url != last_url:
                logger.info("URL: %s", url)
                last_url = url

            if url and is_auth_page(url) and url != last_auth_url:
                logger.info("HA auto-login detected; waiting for login form")
                result = extract_evaluate_value(await controller.evaluate(auto_login_script, await_promise=True))
                last_auth_url = url
                status = result.get("status") if isinstance(result, dict) else "unknown"
                logger.info("HA auto-login form found; credentials injected; submission requested") if status == "submitted" else logger.warning("HA auto-login %s", status)

            if url and is_ha_page(url) and not is_auth_page(url) and url != last_settings_url:
                result = extract_evaluate_value(await controller.evaluate(settings_script))
                if isinstance(result, dict) and result.get("ok"):
                    logger.info(
                        "Applied HA settings: sidebar=%s theme=%s changed=%s",
                        result.get("sidebar"),
                        result.get("theme"),
                        result.get("changed"),
                    )
                    if result.get("changed"):
                        await controller.reload(ignore_cache=False)
                        last_reload_at = time.monotonic()
                        logger.info("Reloaded page to apply updated HA localStorage")
                else:
                    logger.warning("Failed to apply HA settings: %s", result)
                last_settings_url = url

            if BROWSER_REFRESH > 0 and url and url != "about:blank":
                now = time.monotonic()
                if now - last_reload_at >= BROWSER_REFRESH:
                    reload_count += 1
                    ignore_cache = reload_count % HARD_RELOAD_FREQ == 0
                    await controller.reload(ignore_cache=ignore_cache)
                    last_reload_at = now
                    logger.info("Reloading%s: %s", " [HARD]" if ignore_cache else "", url)

        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Watchdog loop error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
