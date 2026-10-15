import random
from urllib.parse import urlsplit, unquote
from patchright.sync_api import sync_playwright
from .base_controller import BaseBrowserController


def get_playwright_proxy_dict(proxy_str):
    if not proxy_str:
        return None
    try:
        u = urlsplit(proxy_str)
        server = f"{u.scheme or 'http'}://{u.hostname}:{u.port}"
        p_dict = {
            "server": server,
            "bypass": "localhost",
        }
        if u.username:
            p_dict["username"] = unquote(u.username)
        if u.password:
            p_dict["password"] = unquote(u.password)
        return p_dict
    except Exception as e:
        print(f"[Warn] Proxy parse error ({proxy_str}): {e}")
        return {"server": proxy_str, "bypass": "localhost"}


class PatchrightController(BaseBrowserController):

    def launch_browser(self):
        try:
            p = sync_playwright().start()
            proxy_settings = get_playwright_proxy_dict(self.proxy)

            b = p.chromium.launch(
                headless=False,
                args=[
                    "--lang=en-US",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--use-gl=angle",
                    "--use-angle=gl-egl",
                    "--ignore-gpu-blocklist",
                    "--window-size=1280,800",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--enforce-webrtc-ip-permission-check",
                ],
                proxy=proxy_settings,
            )

            return p, b

        except Exception as e:
            print(f"启动浏览器失败: {e}")
            return False, False

    def _find_visible_challenge_frame(self, page, timeout_loops=35):
        """
        遍历所有 iframe，过滤掉 4 个 display:none 的蜜罐 iframe，
        精准返回当前真正可见（bounding_box 宽高 > 0）的「人工验证挑战 / 验证质询 / Challenge」子 Frame。
        """
        for _ in range(timeout_loops):
            for f in page.frames:
                try:
                    t = (f.title() or "").lower()
                    if any(k in t for k in ("人工验证", "验证质询", "challenge", "verification", "arkose")):
                        box = f.locator("body").bounding_box()
                        if box and box["width"] > 50 and box["height"] > 20:
                            return f
                except Exception:
                    pass
            page.wait_for_timeout(300)
        return None

    def _is_post_captcha_page(self, page) -> bool:
        u = page.url or ""
        return (
            "account.live.com/interrupt" in u
            or "account.live.com/proofs" in u
            or "outlook.live.com/mail" in u
            or page.get_by_text("让我们来保护你的帐户").count() > 0
            or page.get_by_text("帮助保护你的帐户").count() > 0
            or page.get_by_text("添加电子邮件").count() > 0
            or page.get_by_text("Help us protect your account").count() > 0
            or page.get_by_text("Add an email address").count() > 0
            or page.locator("#EmailAddress").count() > 0
        )

    def handle_captcha(self, page):
        for attempt in range(0, 3):
            if getattr(page, "captcha_passed_token", False) or self._is_post_captcha_page(page):
                for _ in range(60):
                    if self._is_post_captcha_page(page):
                        return True
                    page.wait_for_timeout(500)
                return self._is_post_captcha_page(page)

            f = self._find_visible_challenge_frame(page, timeout_loops=45)
            if not f:
                if self._is_post_captcha_page(page):
                    return True
                return False

            # 等待验证码 iframe 的第 4 次 msft 遥测握手完成
            if attempt == 0:
                for _ in range(25):
                    if getattr(page, "msft_count", 0) >= 4:
                        break
                    page.wait_for_timeout(400)
                page.wait_for_timeout(random.randint(1500, 2200))
            else:
                page.wait_for_timeout(random.randint(800, 1300))

            # 优先使用真人长按「按住 人工挑战 / Press and hold」
            hold_btn = f.locator('[aria-label*="按住"], [aria-label*="Press and hold"], [aria-label*="press and hold"]')
            box = hold_btn.bounding_box() if hold_btn.count() > 0 else None
            if box:
                init_txt = ""
                try:
                    init_txt = f.locator("body").inner_text().strip()
                except Exception:
                    pass
                tx = box["x"] + box["width"] / 2 + random.uniform(-12, 12)
                ty = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
                self.smooth_move_to(page, tx, ty)
                page.wait_for_timeout(random.randint(150, 260))
                page.mouse.down()
                completed_seen = False
                held_ms = 0
                for hold_step in range(65):
                    page.wait_for_timeout(200)
                    held_ms = (hold_step + 1) * 200
                    if getattr(page, "captcha_passed_token", False):
                        completed_seen = True
                        break
                    try:
                        txt = f.locator("body").inner_text().strip()
                        if held_ms >= 6400 and txt != init_txt and ("已完成" in txt or "请稍候" in txt or "please wait" in txt.lower()):
                            completed_seen = True
                            break
                    except Exception:
                        if held_ms >= 7600:
                            completed_seen = True
                            break
                page.wait_for_timeout(random.randint(160, 280))
                page.mouse.up()
                print(f"  [Captcha attempt={attempt+1}] mouse released after {held_ms}ms (completed_seen={completed_seen})", flush=True)
            else:
                loc = f.locator('[aria-label*="挑战"], [aria-label*="challenge"], [aria-label*="Challenge"]')
                if loc.count() > 0:
                    self.smooth_click(page, loc.first)
                page.wait_for_timeout(random.randint(700, 1200))

            # 等待验证通过并跳转至辅助邮箱绑定页 (#EmailAddress / account.live.com/interrupt)
            for step in range(70):
                page.wait_for_timeout(500)
                if page.get_by_text("一些异常活动").count() > 0 or page.get_by_text("帐户创建已被阻止").count() > 0 or page.get_by_text("Unusual activity").count() > 0:
                    print("[Error: Rate limit] - 验证码已交互，但当前 IP 注册频率达到上限被风控拦截。")
                    return False
                if self._is_post_captcha_page(page):
                    return True
                # 若松开鼠标满 11 秒仍未收到 continuationToken，立即进入下一次按压挑战（防止挑战会话超时）
                if step >= 22 and not getattr(page, "captcha_passed_token", False):
                    break

        return False

    def get_thread_page(self):
        browser = self.get_thread_browser()
        if not browser:
            return None
        tz = getattr(self, "current_timezone", None) or "America/New_York"
        context = browser.new_context(
            locale="en-US",
            timezone_id=tz,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.msft_count = 0
        page.risk_tier = None
        page.captcha_passed_token = False

        def _track_msft(resp):
            try:
                u = resp.url
                if "risk/initialize" in u and resp.status == 200:
                    txt = resp.text()
                    page.risk_tier = "AQAB" if '"continuationToken":"AQAB' in txt else "BgAB"
                elif "/api/v2/msft" in u and resp.status == 200:
                    page.msft_count += 1
                elif "risk/verify" in u and resp.status == 200:
                    txt = resp.text()
                    if '"continuationToken":' in txt and '"challengeDetails":' not in txt:
                        page.captcha_passed_token = True
                        print("  [risk/verify 200 OK] Official continuationToken issued! Waiting for CreateAccount...", flush=True)
                elif "CreateAccount" in u and resp.status == 200:
                    print("  [CreateAccount 200 OK] Account creation API completed!", flush=True)
            except Exception:
                pass

        page.on("response", _track_msft)
        return page

    def clean_up(self, page=None, type="all_browser"):
        if type == "done_browser" and page:
            try:
                context = page.context
                context.close()
            except Exception:
                pass

        elif type == "all_browser":
            for p, b in self.active_resources:
                try:
                    b.close()
                except Exception:
                    pass
                try:
                    p.stop()
                except Exception:
                    pass
