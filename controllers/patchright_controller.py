import random
from patchright.sync_api import sync_playwright
from .base_controller import BaseBrowserController


class PatchrightController(BaseBrowserController):

    def launch_browser(self):
        try:
            p = sync_playwright().start()

            proxy_settings = {
                "server": self.proxy,
                "bypass": "localhost",
            } if self.proxy else None

            b = p.chromium.launch(
                headless=False,
                args=[
                    "--lang=zh-CN",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--use-gl=angle",
                    "--use-angle=gl-egl",
                    "--ignore-gpu-blocklist",
                    "--window-size=1280,800",
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
        精准返回当前真正可见（bounding_box 宽高 > 0）的「人工验证挑战 / 验证质询」子 Frame。
        """
        for _ in range(timeout_loops):
            for f in page.frames:
                try:
                    if f.title() in ("人工验证挑战", "验证质询"):
                        box = f.locator("body").bounding_box()
                        if box and box["width"] > 50 and box["height"] > 20:
                            return f
                except Exception:
                    pass
            page.wait_for_timeout(300)
        return None

    def handle_captcha(self, page):
        for attempt in range(0, self.max_captcha_retries + 1):
            f = self._find_visible_challenge_frame(page)
            if not f:
                if page.get_by_text("让我们来保护你的帐户").count() > 0 or page.locator("#EmailAddress").count() > 0:
                    return True
                return False

            page.wait_for_timeout(random.randint(600, 1100))

            # 优先使用真人长按「按住 人工挑战」，检测到“已完成”立即松开鼠标
            hold_btn = f.locator('[aria-label*="按住"]')
            box = hold_btn.bounding_box() if hold_btn.count() > 0 else None
            if box:
                tx = box["x"] + box["width"] / 2 + random.uniform(-14, 14)
                ty = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
                self.smooth_move_to(page, tx, ty)
                page.wait_for_timeout(random.randint(100, 220))
                page.mouse.down()
                for _ in range(55):
                    page.wait_for_timeout(200)
                    try:
                        txt = f.locator("body").inner_text()
                        if "已完成" in txt or "completed" in txt.lower():
                            break
                    except Exception:
                        break
                page.wait_for_timeout(random.randint(120, 280))
                page.mouse.up()
            else:
                # 备选：无障碍可访问性挑战双击模式
                loc = f.locator('[aria-label="可访问性挑战"]')
                self.smooth_click(page, loc)
                page.wait_for_timeout(random.randint(700, 1200))
                loc2 = f.locator('[aria-label="再次按下"]')
                self.smooth_click(page, loc2)

            # 等待验证通过并跳转至辅助邮箱绑定页 (#EmailAddress)
            for _ in range(30):
                page.wait_for_timeout(500)
                if page.get_by_text("一些异常活动").count() > 0 or page.get_by_text("帐户创建已被阻止").count() > 0:
                    print("[Error: Rate limit] - 验证码已交互，但当前 IP 注册频率达到上限被风控拦截。")
                    return False
                if page.get_by_text("让我们来保护你的帐户").count() > 0 or page.locator("#EmailAddress").count() > 0:
                    return True

        return False

    def get_thread_page(self):
        browser = self.get_thread_browser()
        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 800},
        )
        return context.new_page()

    def clean_up(self, page=None, type="all_browser"):
        if type == "done_browser" and page:
            context = page.context
            context.close()

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
