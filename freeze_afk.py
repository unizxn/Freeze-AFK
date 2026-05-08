#!/usr/bin/env python3
"""
FreezeHost AFK - 自动挂机赚币脚本
使用 SeleniumBase UC 模式绕过 Cloudflare Turnstile
"""
import os
import time
import platform

# Linux 服务器上需要虚拟显示器
if platform.system().lower() == "linux":
    from pyvirtualdisplay import Display
    disp = Display(visible=False, size=(1920, 1080))
    disp.start()
    os.environ["DISPLAY"] = disp.new_display_var

from seleniumbase import SB

# Discord Token - 从环境变量读取
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

# WARP 代理地址（可选，推荐使用）
WARP_PROXY = os.environ.get("WARP_PROXY", "socks5://127.0.0.1:40000")

# 最大运行时长（分钟），0 = 无限
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "0"))

# 每个 session 赚币时长（秒）
SESSION_DURATION = 1200  # 20 分钟


def log(msg):
    """带时间戳的日志"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_turnstile(sb, timeout=120):
    """
    等待 Turnstile 验证通过

    原理：
    1. WARP IP 是 Cloudflare 信任的，challenge 会降级为 managed 类型
    2. SeleniumBase UC 模式自动处理验证框点击
    3. 等待 cf-turnstile-response input 出现值
    """
    start = time.time()
    last_click = 0

    while time.time() - start < timeout:
        try:
            val = sb.execute_script(
                "return document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || "
                "window.turnstileToken || '';"
            )
            if val and len(str(val)) > 20:
                return str(val)
        except:
            pass

        now = time.time()
        if now - last_click > 5:
            try:
                sb.uc_gui_click_captcha()
                last_click = now
            except:
                pass
        time.sleep(2)

    return None


def login_via_discord_token(sb):
    """
    通过 Discord Token 登录 FreezeHost

    原理：
    1. 点击 FreezeHost 的 Discord 登录按钮
    2. 在 discord.com 页面注入 token 到 localStorage
    3. 刷新页面后 Discord 自动登录
    4. OAuth 回调返回 FreezeHost
    """
    log("Opening FreezeHost...")
    sb.uc_open_with_reconnect("https://free.freezehost.pro", reconnect_time=5)
    time.sleep(5)

    # 点击登录按钮
    log("Click Login...")
    try:
        sb.click("button#login-btn")
    except:
        sb.execute_script("document.getElementById('login-btn')?.click();")
    time.sleep(3)

    # 确认条款弹窗
    try:
        sb.wait_for_element_visible("button#confirm-login", timeout=5)
        sb.click("button#confirm-login")
        log("Confirmed terms")
    except:
        log("No terms dialog")
    time.sleep(2)

    # 如果跳转到 Discord 登录页
    if "discord.com" in sb.get_current_url():
        log("Inject Discord token...")

        sb.execute_script(f"""(function() {{
            var token = '{DISCORD_TOKEN}';
            var f = document.createElement("iframe");
            f.style.display = "none";
            document.body.appendChild(f);
            try {{ f.contentWindow.localStorage.setItem("token", '"'+token+'"'); }} catch(e) {{}}
            try {{ localStorage.setItem("token", '"'+token+'"'); }} catch(e) {{}}
            document.body.removeChild(f);
        }})();""")

        log("Reload to apply token...")
        sb.driver.refresh()
        time.sleep(8)

        url = sb.get_current_url()
        log(f"URL: {url}")

        if "discord.com/login" in url:
            log("Token invalid!")
            return False

        # 如果还在 OAuth 页面，尝试自动授权
        if "discord.com/oauth2" in url:
            sb.execute_script("""() => {
                document.querySelectorAll("button").forEach(btn => {
                    if (btn.textContent.toLowerCase().includes("authorize")) 
                        btn.click();
                });
            }""")
            time.sleep(5)

        # 等待跳回 FreezeHost
        for _ in range(20):
            if "free.freezehost.pro" in sb.get_current_url():
                break
            time.sleep(2)

    url = sb.get_current_url()
    log(f"Login result: {url}")
    return "free.freezehost.pro" in url


def run_earn_session(sb, session_num):
    """
    执行一次挂机赚币 session

    每个 session 最长 20 分钟（1200秒）
    页面 JavaScript 自动处理 WebSocket 连接和挑战响应
    """
    log(f"Loading /earn page...")
    sb.uc_open_with_reconnect("https://free.freezehost.pro/earn", reconnect_time=6)
    time.sleep(15)

    # 检查是否需要重新登录
    if "discord.com" in sb.get_current_url():
        log("Session expired, re-login...")
        if not login_via_discord_token(sb):
            return False
        sb.uc_open_with_reconnect("https://free.freezehost.pro/earn", reconnect_time=6)
        time.sleep(15)

    # 等待 Turnstile 验证
    log("Waiting Turnstile...")
    token = wait_turnstile(sb, timeout=120)

    if token:
        log(f"Turnstile passed! Token: {token[:30]}...")
        log(f"Session #{session_num} earning for {SESSION_DURATION}s ({SESSION_DURATION//60} min)...")

        start = time.time()
        while time.time() - start < SESSION_DURATION:
            try:
                if "discord.com" in sb.get_current_url():
                    log("Session expired during earning")
                    break
            except:
                break

            # 检查最大运行时长
            if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
                log("Max runtime reached!")
                return None  # None 表示应该退出

            time.sleep(30)

        log(f"Session #{session_num} completed!")
        return True
    else:
        log("Turnstile failed!")
        try:
            sb.save_screenshot(f"/tmp/fh_fail_{session_num}.png")
        except:
            pass
        return False


def main():
    global global_start

    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not set!")
        print("Set via: export DISCORD_TOKEN='your_token'")
        print("Or set as GitHub Secret: DISCORD_TOKEN")
        return

    log("=" * 50)
    log("FreezeHost AFK - Auto Earn Coins")
    log("=" * 50)
    log(f"Proxy: {WARP_PROXY or 'none'}")
    log(f"Max runtime: {MAX_RUNTIME} min {'(unlimited)' if MAX_RUNTIME == 0 else ''}")
    log(f"Session duration: {SESSION_DURATION}s ({SESSION_DURATION//60} min)")
    log("=" * 50)

    global_start = time.time()

    # SeleniumBase UC 配置
    sb_options = {
        "uc": True,
        "test": True,
        "headed": True,
        "chromium_arg": "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-size=1280,720",
    }

    if WARP_PROXY:
        sb_options["proxy"] = WARP_PROXY

    with SB(**sb_options) as sb:
        if not login_via_discord_token(sb):
            log("Login failed!")
            return
        log("Login successful!")

        session = 0
        while True:
            # 检查最大运行时长
            if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
                log("Max runtime reached, exiting!")
                break

            session += 1
            log(f"\n{'='*30}")
            log(f"Session #{session}")
            log(f"{'='*30}")

            result = run_earn_session(sb, session)
            if result is None:
                break
            if not result:
                log("Session failed, retrying...")

            time.sleep(5)

    log("Done!")


if __name__ == "__main__":
    main()
