"""实验: 滚动后WebView元素的bounds是否改变"""
import sys
sys.path.insert(0, 'D:/UI_Agent')
from agent.adb_controller import ADBController
from agent.config import Config
import xml.etree.ElementTree as ET
import re

ctrl = ADBController(serial=Config.ANDROID_SERIAL)
w, h = ctrl.get_screen_size()
print(f"Screen: {w}x{h}")

def dump_products():
    """dump并打印产品列表"""
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    products = []
    for n in root.iter('node'):
        t = n.get('text', '')
        b = n.get('bounds', '')
        if '额度由' in t and '提供' in t:
            nums = [int(x) for x in re.findall(r'\d+', b)]
            products.append((t[:40], nums[1] if len(nums) >= 2 else -1, b))
    return products

def dump_buttons():
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    btns = []
    for n in root.iter('node'):
        t = n.get('text', '')
        b = n.get('bounds', '')
        if t == '立即提现':
            nums = [int(x) for x in re.findall(r'\d+', b)]
            btns.append((nums[1] if len(nums) >= 2 else -1, b))
    return btns

print("\n=== Before scroll ===")
prods = dump_products()
for p, y, b in prods:
    print(f"  Y={y:5d}  {p}  {b}")

# Scroll up (content moves down) - try to see products at bottom
print("\n=== Scrolling up 3 times ===")
for i in range(3):
    ctrl.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
    ctrl.wait(1)

print("\n=== After 3 scrolls ===")
prods = dump_products()
for p, y, b in prods:
    print(f"  Y={y:5d}  {p}  {b}")

btns = dump_buttons()
print(f"\n立即提现 buttons: {len(btns)}")
for y, b in btns:
    print(f"  Y={y:5d}  {b}")

# Take screenshot
ctrl.screenshot('D:/UI_Agent/screenshots/diag_after_scroll.png')
print("\nScreenshot saved")
