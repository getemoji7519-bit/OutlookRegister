import base64
from datetime import datetime
import hashlib
import json
import secrets
import string
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, quote
from urllib.request import getproxies
from utils import load_config


def get_proxy():
    proxies = getproxies()
    http_proxy = proxies.get("http") or proxies.get("https")
    if http_proxy:
        return {"http": http_proxy, "https": http_proxy}
    return {"http": None, "https": None}


def generate_code_verifier(length=128):
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_code_challenge(code_verifier):
    sha256_hash = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(sha256_hash).decode().rstrip("=")


def handle_oauth2_form(page, email, attempt):
    page.wait_for_timeout(1500)
    # 1. 如果出现账号选择列表或登录框
    try:
        if page.locator('[name="loginfmt"]').count() > 0 and page.locator('[name="loginfmt"]').first.is_visible():
            page.locator('[name="loginfmt"]').first.fill(email, timeout=5000)
            page.locator('#idSIButton9, [data-testid="primaryButton"], [type="submit"]').first.click(timeout=5000)
            page.wait_for_timeout(1500)
        elif page.get_by_text(email).count() > 0:
            page.get_by_text(email).first.click(timeout=5000)
            page.wait_for_timeout(1500)
    except Exception:
        pass

    # 2. 点击 OAuth2 权限同意按钮 (Accept / 接受 / 是)
    consent_selectors = [
        '[data-testid="appConsentPrimaryButton"]',
        "#idBtn_Accept",
        "#idSIButton9",
        '[data-testid="primaryButton"]',
        'button:has-text("接受")',
        'button:has-text("Accept")',
        'button:has-text("是")',
        'input[value="是"]',
        'input[value="Accept"]',
    ]
    for _ in range(15):
        for sel in consent_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
        page.wait_for_timeout(600)


def get_access_token(page, email, max_retries=3):
    for attempt in range(max_retries):
        result = _try_get_access_token(page, email, attempt)
        if result[0] is not False:
            return result
    return False, False, False


def _try_get_access_token(page, email, attempt):
    data = load_config()
    SCOPES = data["oauth2"]["Scopes"]
    client_id = data["oauth2"].get("client_id") or "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    redirect_url = data["oauth2"].get("redirect_url") or "https://localhost"
    _email_suffix = data["email_suffix"]

    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_url,
        "scope": " ".join(SCOPES),
        "response_mode": "query",
        "login_hint": f"{email}{_email_suffix}",
        "code_challenge": code_verifier if False else code_challenge,
        "code_challenge_method": "S256",
    }

    authorize_url = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
        + "&".join(f"{k}={quote(v)}" for k, v in params.items())
    )

    captured_url = None

    def on_request(request):
        nonlocal captured_url
        if redirect_url in request.url and "code=" in request.url:
            captured_url = request.url

    page.on("request", on_request)

    try:
        try:
            page.wait_for_timeout(250)
            page.goto(authorize_url, timeout=30000)
        except Exception:
            if not captured_url:
                return False, False, False

        if not captured_url:
            handle_oauth2_form(page, f"{email}{_email_suffix}", attempt)

        max_refreshes = 1
        refresh_count = 0
        refresh_interval = 200

        for i in range(400):
            if captured_url:
                break
            page.wait_for_timeout(100)
            if i > 0 and i % refresh_interval == 0:
                if refresh_count >= max_refreshes:
                    return False, False, False
                refresh_count += 1
                try:
                    page.reload(timeout=10000)
                except Exception:
                    pass
        else:
            if not captured_url:
                return False, False, False

    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass

    if not captured_url or "code=" not in captured_url:
        return False, False, False

    auth_code = parse_qs(captured_url.split("?", 1)[1])["code"][0]

    try:
        token_payload = urllib.parse.urlencode({
            "client_id": client_id,
            "code": auth_code,
            "redirect_uri": redirect_url,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
            "scope": " ".join(SCOPES),
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read().decode("utf-8"))
            if "refresh_token" in tokens:
                return (
                    tokens["refresh_token"],
                    tokens.get("access_token", ""),
                    datetime.now().timestamp() + int(tokens.get("expires_in", 3600)),
                )
    except Exception as e:
        print(f"[OAuth2 Token Exchange Error] {e}")
        return False, False, False

    return False, False, False
