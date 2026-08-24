#!/usr/bin/env python3
"""Chromium watchdog with bounded, Shadow-DOM-aware HA auto-login."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from browser_ctl import ChromiumController

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: [%(filename)s:%(funcName)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

HA_LOGIN_URL = (os.getenv("HA_LOGIN_URL") or "http://127.0.0.1:8123").rstrip("/")
_origin = re.match(r"^(https?://[\w.\-]+(?::\d+)?)", HA_LOGIN_URL)
HA_LOGIN_URL_BASE = _origin.group(1).rstrip("/") if _origin else HA_LOGIN_URL
DESKOS_URL = (os.getenv("DESKOS_URL") or "http://127.0.0.1:4173/").rstrip("/")
HA_USERNAME = os.getenv("HA_USERNAME") or ""
HA_PASSWORD = os.getenv("HA_PASSWORD") or ""
BROWSER_REFRESH = max(0, int(os.getenv("BROWSER_REFRESH") or "600"))
DARK_MODE = (os.getenv("DARK_MODE") or "true").strip().lower() == "true"
RAW_SIDEBAR = (os.getenv("HA_SIDEBAR") or "").strip().lower()
RAW_THEME = (os.getenv("HA_THEME") or "").strip()
POLL_INTERVAL = 2.0
HARD_RELOAD_FREQ = 10
AUTO_LOGIN_TIMEOUT_SECONDS = 15.0
AUTO_LOGIN_POLL_SECONDS = 0.5
SIDEBAR_MAP = {"full": "", "none": '"always_hidden"', "narrow": '"auto"', "": ""}


def normalize_sidebar(raw_sidebar: str) -> str:
    return SIDEBAR_MAP.get(raw_sidebar, "")


def normalize_theme(raw_theme: str, dark_mode: bool) -> str:
    theme = raw_theme.strip()
    if theme in {"", "{}", "Home Assistant"}:
        return '{"dark":true}' if dark_mode else '{"dark":false}'
    return json.dumps(theme) if theme[0] not in {'"', "'", "{"} else theme


def build_login_probe_script() -> str:
    """A short evaluation: recursively find form controls in open shadow roots."""
    return """
(() => {
  const all = [];
  const visit = (root) => { if (!root || !root.querySelectorAll) return; for (const node of root.querySelectorAll('*')) { all.push(node); if (node.shadowRoot) visit(node.shadowRoot); } };
  visit(document);
  const find = (selector) => all.find((node) => node.matches && node.matches(selector));
  return Boolean(find('input[autocomplete="username"]') && find('input[autocomplete="current-password"]') && all.find((node) => node.matches && node.matches('button, ha-button, mwc-button')));
})()
"""


def build_auto_login_script() -> str:
    """A short evaluation: inject credentials and submit without exposing them."""
    return f"""
(() => {{
  const username = {json.dumps(HA_USERNAME)};
  const password = {json.dumps(HA_PASSWORD)};
  const all = [];
  const visit = (root) => {{ if (!root || !root.querySelectorAll) return; for (const node of root.querySelectorAll('*')) {{ all.push(node); if (node.shadowRoot) visit(node.shadowRoot); }} }};
  visit(document);
  const find = (selector) => all.find((node) => node.matches && node.matches(selector));
  const usernameField = find('input[autocomplete="username"]');
  const passwordField = find('input[autocomplete="current-password"]');
  const submitButton = all.find((node) => node.matches && node.matches('button[type="submit"], ha-button[type="submit"], mwc-button[type="submit"], button, ha-button, mwc-button') && /log ?in|connexion|se connecter/i.test(`${{node.innerText || node.textContent || node.getAttribute('aria-label') || ''}}`));
  if (!usernameField || !passwordField || !submitButton) return {{ status: 'missing-elements' }};
  const setValue = (field, value) => {{ const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(field, value); field.dispatchEvent(new Event('input', {{ bubbles: true, composed: true }})); field.dispatchEvent(new Event('change', {{ bubbles: true, composed: true }})); }};
  setValue(usernameField, username); setValue(passwordField, password); submitButton.click();
  return {{ status: 'submitted' }};
}})()
"""


def build_settings_script(sidebar: str, theme: str) -> str:
    return f"""(() => {{ try {{ let changed=false; localStorage.setItem('browser_mod-browser-id','haos_kiosk'); const sidebar={json.dumps(sidebar)}; if (sidebar !== (localStorage.getItem('dockedSidebar') || '')) {{ sidebar ? localStorage.setItem('dockedSidebar', sidebar) : localStorage.removeItem('dockedSidebar'); changed=true; }} const theme={json.dumps(theme)}; if (theme !== (localStorage.getItem('selectedTheme') || '')) {{ theme ? localStorage.setItem('selectedTheme', theme) : localStorage.removeItem('selectedTheme'); changed=true; }} return {{ok:true,changed}}; }} catch (error) {{ return {{ok:false}}; }} }})()"""


def is_auth_page(url: str) -> bool:
    return bool(re.match(rf"^{re.escape(HA_LOGIN_URL_BASE)}/auth/authorize(?:\?|$)", url))


def is_ha_page(url: str) -> bool:
    return bool(url and (url + "/").startswith(HA_LOGIN_URL_BASE + "/"))


def is_deskos_page(url: str) -> bool:
    """DeskOS owns its idle lifecycle, so it must not be periodically reloaded."""
    current = urlparse(url)
    deskos = urlparse(DESKOS_URL)
    return (
        current.scheme == deskos.scheme
        and current.hostname == deskos.hostname
        and current.port == deskos.port
    )


def extract_evaluate_value(result: dict[str, Any]) -> Any:
    runtime_result = result.get("result")
    return runtime_result.get("value") if isinstance(runtime_result, dict) and "value" in runtime_result else runtime_result


async def perform_auto_login(controller: ChromiumController) -> bool:
    logger.info("HA auto-login detected")
    deadline = time.monotonic() + AUTO_LOGIN_TIMEOUT_SECONDS
    probe = build_login_probe_script()
    while time.monotonic() < deadline:
        try:
            if bool(extract_evaluate_value(await controller.evaluate(probe))):
                logger.info("HA auto-login form found")
                result = extract_evaluate_value(await controller.evaluate(build_auto_login_script()))
                if isinstance(result, dict) and result.get("status") == "submitted":
                    logger.info("HA auto-login credentials injected")
                    logger.info("HA auto-login submission requested")
                    return True
        except Exception as exc:
            logger.debug("HA auto-login poll retry: %s", exc)
        await asyncio.sleep(AUTO_LOGIN_POLL_SECONDS)
    logger.warning("HA auto-login timeout after %.0fs", AUTO_LOGIN_TIMEOUT_SECONDS)
    return False


async def main() -> None:
    controller = ChromiumController()
    sidebar, theme = normalize_sidebar(RAW_SIDEBAR), normalize_theme(RAW_THEME, DARK_MODE)
    settings_script = build_settings_script(sidebar, theme)
    logger.info("Chromium watchdog started: HA_LOGIN_URL=%s AUTO_LOGIN_TIMEOUT=%.0fs", HA_LOGIN_URL, AUTO_LOGIN_TIMEOUT_SECONDS)
    last_url = last_auth_url = last_settings_url = submitted_auth_url = ""
    last_reload_at, reload_count = time.monotonic(), 0
    while True:
        try:
            target = await controller.get_page_target()
            url = str(target.get("url") or "")
            if url and url != last_url:
                logger.info("URL: %s", url)
                last_url = url
            if url and is_auth_page(url) and url != last_auth_url:
                last_auth_url = url
                if HA_USERNAME and HA_PASSWORD and await perform_auto_login(controller):
                    submitted_auth_url = url
                elif not (HA_USERNAME and HA_PASSWORD):
                    logger.warning("HA auto-login timeout: credentials are not configured")
            if submitted_auth_url and url and is_ha_page(url) and not is_auth_page(url):
                logger.info("HA auto-login success")
                submitted_auth_url = ""
            if url and is_ha_page(url) and not is_auth_page(url) and url != last_settings_url:
                result = extract_evaluate_value(await controller.evaluate(settings_script))
                if isinstance(result, dict) and result.get("ok") and result.get("changed"):
                    await controller.reload(ignore_cache=False)
                    last_reload_at = time.monotonic()
                    logger.info("Reloaded page to apply updated HA localStorage")
                last_settings_url = url
            if (
                BROWSER_REFRESH > 0
                and url
                and url != "about:blank"
                and not is_deskos_page(url)
                and time.monotonic() - last_reload_at >= BROWSER_REFRESH
            ):
                reload_count += 1
                await controller.reload(ignore_cache=reload_count % HARD_RELOAD_FREQ == 0)
                last_reload_at = time.monotonic()
        except Exception as exc:
            logger.warning("Watchdog loop error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
