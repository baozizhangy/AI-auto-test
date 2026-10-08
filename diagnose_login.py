"""诊断：排查 H5 页面为什么被重定向到登录页。"""

from playwright.sync_api import sync_playwright

TARGET_URL = (
    "http://bm-sit.shangtoutech.com/clg/hub/api/p/apply/bm/getH5Page"
    "?channelId=HUB_LXJ&h5Type=2"
    "&sequenceId=BM-HUB1222214684519088128"
    "&sign=1134f8c86b3c8f555cd2076067b5af87"
)

# 确保 URL 完整（用户给的链接）
TARGET_URL = "http://bm-sit.shangtoutech.com/clg/bcs/api/p/hub/uni/22588/SWpaaE1UVXhZV1kwTkdZNU9UbGtOVFF6WTJNMU9UTXpaQ0k6MXdSaXlLOmozZ3ZiWU1FeVpwWnN1MHRNZlBOT1lOXzVxQQ?channelId=HUB_LXJ&channelUid=2000&loadingPage=draw&channelApplyNo=BM1222221610623614976&applyNo=AP1222221615657500672#/loan-apply?loanFlowNo=LFN1222222030725824512"

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def diagnose():
    import time
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)

    print("=" * 60)
    print("诊断: 等待 SPA 完整渲染后检查页面状态")
    print("=" * 60)

    ctx = browser.new_context(
        user_agent=MOBILE_UA,
        viewport={"width": 375, "height": 812},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
    )
    page = ctx.new_page()

    # 记录所有网络请求
    api_calls = []
    page.on("response", lambda resp: api_calls.append(
        f"  [{resp.status}] {resp.url[:100]}"
    ))

    print(f"\n[1] 导航到目标 URL...")
    page.goto(TARGET_URL, wait_until="load", timeout=30000)
    print(f"  最终 URL: {page.url}")
    print(f"  URL hash: {page.evaluate('location.hash')}")

    # 等待 SPA 渲染
    print(f"\n[2] 等待 SPA 渲染（最多 20 秒）...")
    for i in range(20):
        time.sleep(1)
        url_hash = page.evaluate("location.hash")
        body_text = page.text_content("body") or ""
        has_input = page.locator("input.f-input").count() > 0
        has_login = "登录" in body_text or "手机号" in body_text
        has_loan = "借款" in body_text or "金额" in body_text or "可取现额度" in body_text
        print(f"  第 {i+1}s: hash={url_hash}, 有input={has_input}, 有登录={has_login}, 有借款={has_loan}")
        if has_input or has_login or has_loan:
            break

    print(f"\n[3] 最终页面状态:")
    print(f"  URL: {page.url}")
    print(f"  标题: {page.title()}")
    final_text = page.text_content("body") or ""
    print(f"  body 前 300 字: {final_text[:300]}")

    # 截图保存
    page.screenshot(path="screenshots/diagnose_final.png")
    print(f"  截图已保存: screenshots/diagnose_final.png")

    # 打印关键 API 请求
    print(f"\n[4] 网络请求 (共 {len(api_calls)} 个):")
    for call in api_calls[:30]:
        print(call)
    if len(api_calls) > 30:
        print(f"  ... 还有 {len(api_calls) - 30} 个请求")

    ctx.close()
    browser.close()
    pw.stop()
    print("\n诊断完成。")


if __name__ == "__main__":
    diagnose()
