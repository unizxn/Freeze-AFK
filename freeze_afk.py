#!/usr/bin/env python3
"""
FreezeHost AFK - 自动挂机赚币脚本（CloakBrowser 版）

与原 SeleniumBase UC 版本的区别：
- 浏览器从 undetected-chromedriver（配置层补丁）换成 CloakBrowser（C++ 源码级补丁）
- Cloudflare Turnstile 在非交互模式下由 CloakBrowser 自动通过，不再依赖 uc_gui_click_captcha
- 人类化鼠标轨迹交给 CloakBrowser 的 humanize=True，删掉手写的 ActionChains 随机移动代码
- 自动按代理出口 IP 匹配时区/语言（geoip=True），并防 WebRTC 真实 IP 泄漏
- 无显示器环境（GitHub Actions）用 pyvirtualdisplay 自动起 Xvfb，不再需要 xvfb-run 包裹

保留不变：sing-box 代理 / Discord token 注入登录 / session 20 分钟循环 /
        Start AFK 长按触发 / 广告拦截绕过 / 截图上传 / Telegram 通知
"""
import os
import time
import platform

from cloakbrowser import launch

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
WARP_PROXY = os.environ.get("WARP_PROXY", "")          # socks5://127.0.0.1:40000
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "0"))  # 分钟，0 = 无限
SESSION_DURATION = 1200                                # 每个 session 20 分钟

INSTANCE_ID = int(os.environ.get("INSTANCE_ID", "0"))
LOG_FILE = os.environ.get("LOG_FILE", "")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 全局状态（给 send_tg_message 用）
global_start = 0
coins_start = None
coins_end = None
_display = None  # pyvirtualdisplay 实例（Linux 无显示器时自动启动）


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def log(msg):
    """带时间戳和实例编号的日志"""
    ts = time.strftime("%H:%M:%S")
    prefix = "[I%d]" % INSTANCE_ID if INSTANCE_ID else ""
    line = "[%s] %s %s" % (ts, prefix, msg)
    print(line, flush=True)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


def ensure_display():
    """Linux 无显示器环境（如 GitHub Actions）自动启动 Xvfb 虚拟显示器。

    CloakBrowser 在 headless=False 下需要 X server（Turnstile 在 headless 下不稳）。
    本地有显示器则什么都不做。
    """
    global _display
    if platform.system().lower() != "linux":
        return
    if os.environ.get("DISPLAY"):
        return
    try:
        from pyvirtualdisplay import Display
        _display = Display(visible=False, size=(1920, 1080))
        _display.start()
        log("Started virtual display (Xvfb) for headless Linux")
    except Exception as e:
        log("WARNING: failed to start virtual display: %s" % e)
        log("If the browser fails to launch, install xvfb + pyvirtualdisplay")


def stop_display():
    global _display
    if _display:
        try:
            _display.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Turnstile 等待
# ---------------------------------------------------------------------------
def wait_turnstile(page, timeout=120):
    """等待 Cloudflare Turnstile 出 token。

    CloakBrowser 在源码层就过了 Turnstile 的指纹检测，非交互式 challenge 会自动通过，
    这里只是轮询 cf-turnstile-response 的值，不再需要 uc_gui_click_captcha。
    """
    start = time.time()

    # 把 Turnstile 容器滚到视口中间
    try:
        page.evaluate(
            "var el = document.querySelector('#cf-turnstile-container');"
            "if (el) el.scrollIntoView({block: 'center'});"
        )
    except Exception:
        pass

    while time.time() - start < timeout:
        try:
            val = page.evaluate(
                "return document.querySelector('[name=cf-turnstile-response]')?.value || '';"
            )
            if val and len(str(val)) > 20:
                return str(val)
        except Exception:
            pass
        time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Discord token 登录
# ---------------------------------------------------------------------------
def login_via_discord_token(page, token):
    log("Opening FreezeHost...")
    page.goto("https://free.freezehost.pro", wait_until="domcontentloaded")
    time.sleep(5)

    # 点击 login 按钮
    try:
        page.click("button#login-btn", timeout=5000)
    except Exception:
        page.evaluate("document.getElementById('login-btn')?.click();")
    time.sleep(3)

    # 确认条款弹窗
    try:
        page.wait_for_selector("button#confirm-login", state="visible", timeout=5000)
        page.click("button#confirm-login")
        log("Confirmed terms")
    except Exception:
        log("No terms dialog")
    time.sleep(2)

    if "discord.com" in page.url:
        log("Inject token...")
        # 注入 token 到 localStorage（用 iframe 绕过某些限制）
        # 用 Playwright 参数传递，避免 token 里有特殊字符破坏 JS 字符串
        page.evaluate("""(token) => {
            var f = document.createElement("iframe");
            f.style.display = "none";
            document.body.appendChild(f);
            try { f.contentWindow.localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            document.body.removeChild(f);
        }""", token)

        log("Reload...")
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)

        url = page.url
        if "discord.com/login" in url:
            log("Token invalid!")
            return False

        if "discord.com/oauth2" in url:
            log("Auto-authorize...")
            page.evaluate("""() => {
                document.querySelectorAll("button").forEach(function(btn){
                    if(btn.textContent.toLowerCase().includes("authorize")) btn.click();
                });
            }""")
            time.sleep(5)

        for _ in range(20):
            url = page.url
            if url.startswith("https://free.freezehost.pro"):
                break
            time.sleep(2)

    url = page.url
    log("Login URL: %s" % url)
    return url.startswith("https://free.freezehost.pro")


# ---------------------------------------------------------------------------
# 广告拦截绕过
# ---------------------------------------------------------------------------
def adblocker(page):
    log("Bypassing adblocker...")
    try:
        page.evaluate("""
            if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
            var msg = document.getElementById('adblocker-message');
            if(msg) msg.style.display = 'none';
        """)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 点击 Start AFK 按钮
# ---------------------------------------------------------------------------
def click_start_afk(page):
    """长按 Start AFK 按钮触发 session。

    原脚本这里写了 ~50 行 ActionChains 随机鼠标轨迹来骗过行为检测。
    换成 CloakBrowser 后，launch(humanize=True) 已经让所有鼠标/键盘/滚动
    都走贝塞尔曲线 + 微抖动 + 真实停顿，比手写的随机数轨迹更像人，
    所以这段代码可以大幅简化。
    """
    adblocker(page)
    for attempt in range(3):
        try:
            page.wait_for_selector("#afk-action-trigger", state="visible", timeout=5000)
            page.locator("#afk-action-trigger").scroll_into_view_if_needed()
            time.sleep(1)

            log("Holding Start AFK button...")
            # humanize=True 自动让 hover/down 走人类轨迹
            page.locator("#afk-action-trigger").hover()
            page.mouse.down()

            # 按住最多 2 秒，按钮消失即视为 session 启动成功
            start_hold = time.time()
            success = False
            while time.time() - start_hold < 2.0:
                time.sleep(0.05)
                try:
                    if not page.is_visible("#afk-action-trigger"):
                        success = True
                        break
                except Exception:
                    # 元素已 stale，说明页面发生了变化，视为成功
                    success = True
                    break

            try:
                page.mouse.up()
            except Exception:
                pass
            log("Released Start AFK!")

            if success:
                log("Button #afk-action-trigger gone, session started!")
                return True

            # 再确认一次
            try:
                if not page.is_visible("#afk-action-trigger"):
                    log("Session started!")
                    return True
            except Exception:
                return True

            raise Exception("Button still visible after 2s holding")
        except Exception as e:
            err_msg = str(e)
            # 元素消失导致的异常是预期内的成功信号
            if "not interactable" in err_msg or "stale" in err_msg or "no size" in err_msg:
                log("Expected visibility exception: %s" % err_msg)
                try:
                    page.mouse.up()
                except Exception:
                    pass
                return True
            log("Attempt %d failed: %s" % (attempt + 1, err_msg[:200]))
    return False


# ---------------------------------------------------------------------------
# 单个 earn session
# ---------------------------------------------------------------------------
def run_earn_session(page, session_num, token):
    log("Loading /earn...")
    page.goto("https://free.freezehost.pro/earn", wait_until="domcontentloaded")
    time.sleep(15)

    url = page.url
    if not url.startswith("https://free.freezehost.pro"):
        log("Session expired, re-login...")
        if not login_via_discord_token(page, token):
            return False
        page.goto("https://free.freezehost.pro/earn", wait_until="domcontentloaded")
        time.sleep(15)

    log("Waiting Turnstile...")
    token_val = wait_turnstile(page, timeout=120)
    if not token_val:
        log("Turnstile failed!")
        try:
            os.makedirs("screenshots", exist_ok=True)
            page.screenshot(path="screenshots/fh_fail_%d_%d.png" % (INSTANCE_ID, session_num))
        except Exception:
            pass
        return False

    log("Turnstile OK! Token: %s..." % token_val[:30])

    if not click_start_afk(page):
        log("WARNING: Start AFK button click failed!")
        return False

    log("Earning for %ds..." % SESSION_DURATION)
    start = time.time()
    while time.time() - start < SESSION_DURATION:
        try:
            url = page.url
            if not url.startswith("https://free.freezehost.pro"):
                log("Expired during earning")
                break
        except Exception:
            break

        if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
            log("Max runtime reached!")
            return None

        time.sleep(30)

    log("Session #%d done" % session_num)
    return True


# ---------------------------------------------------------------------------
# 通知 & 状态
# ---------------------------------------------------------------------------
def send_tg_message(start_time):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    end_time = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        import requests
        tg_msg = """[FreezeHost] AFK finished!
        Start Time: %s
        End Time: %s
        Coins Start: %s
        Coins End: %s
        """ % (start_time, end_time, coins_start, coins_end)
        requests.post(
            "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": tg_msg}
        )
    except Exception as e:
        log("Failed to send telegram message: %s" % str(e))


def get_coins(page):
    selector = "div.text-right > div.flex > span:last-child"
    try:
        page.wait_for_selector(selector, timeout=60000)
        return page.locator(selector).first.text_content()
    except Exception as e:
        log("Failed to get coins: %s" % str(e))
        return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    global global_start, coins_start, coins_end

    start_time = time.strftime("%Y-%m-%d %H:%M:%S")

    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not set!")
        print("Set via: export DISCORD_TOKEN='your_token'")
        return

    # 支持多个 token（逗号分隔），按实例编号选
    tokens = [t.strip() for t in DISCORD_TOKEN.split(",") if t.strip()]
    token = tokens[INSTANCE_ID % len(tokens)]

    log("=" * 50)
    log("FreezeHost AFK (CloakBrowser) - Instance #%d" % INSTANCE_ID)
    log("Token: %s...%s" % (token[:10], token[-5:]))
    log("Proxy: %s" % (WARP_PROXY or "none"))
    log("=" * 50)

    global_start = time.time()
    ensure_display()

    # CloakBrowser 启动参数
    launch_kwargs = {
        "headless": False,     # Turnstile 在 headless 下不稳定，必须 headed
        "humanize": True,      # 人类化鼠标/键盘/滚动（替代手写 ActionChains）
        "geoip": True,         # 按代理出口 IP 自动匹配时区/语言，并防 WebRTC IP 泄漏
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1280,720",
        ],
    }
    if WARP_PROXY:
        launch_kwargs["proxy"] = WARP_PROXY  # 原生支持 socks5://...

    browser = launch(**launch_kwargs)
    try:
        page = browser.new_page()

        if not login_via_discord_token(page, token):
            log("Login failed!")
            return
        log("Login OK!")
        time.sleep(5)

        coins_start = get_coins(page)
        log("Coins Start: %s" % coins_start)

        session = 0
        while True:
            if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
                log("Max runtime reached!")
                break

            session += 1
            log("")
            log("=== Session #%d ===" % session)

            result = run_earn_session(page, session, token)
            if result is None:
                break
            if not result:
                log("Session failed, retrying...")

            time.sleep(5)

        # 循环结束，刷新页面拿结束硬币数
        page.reload(wait_until="domcontentloaded")
        coins_end = get_coins(page)
        log("Coins End: %s" % coins_end)
    finally:
        try:
            browser.close()
        except Exception:
            pass
        stop_display()

    send_tg_message(start_time)
    log("Done!")


if __name__ == "__main__":
    main()
