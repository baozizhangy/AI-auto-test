"""实验: 滚动WebView产品列表, 找到乐小融V2并点击"""
import sys
sys.path.insert(0, 'D:/UI_Agent')
from agent.adb_controller import ADBController
from agent.config import Config
import xml.etree.ElementTree as ET
import re

ctrl = ADBController(serial=Config.ANDROID_SERIAL)
w, h = ctrl.get_screen_size()

def check_withdraw():
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    for n in root.iter('node'):
        t = n.get('text', '')
        if '借款申请' in t:
            return True
    return False

def get_native_products():
    """Get products with real Y coordinates"""
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    products = []
    for n in root.iter('node'):
        t = n.get('text', '')
        b = n.get('bounds', '')
        if '额度由' in t and '提供' in t:
            nums = [int(x) for x in re.findall(r'\d+', b)]
            if len(nums) >= 4 and nums[1] != 130:
                products.append((t, nums))
    return products

# Restart app
ctrl.stop_test_app()
ctrl.wait(2)
ctrl.start_test_app()
ctrl.wait(10)

# Find the WebView area - try scrolling at different Y positions
print("=== Finding scrollable WebView area ===")

# Try scrolling at Y=400-800 area (above native products)
for scroll_y_center in [500, 800]:
    print(f"\nScrolling at X=400, Y range {scroll_y_center-200} to {scroll_y_center+200}")
    ctrl.swipe(400, scroll_y_center + 200, 400, scroll_y_center - 200, 500)
    ctrl.wait(2)
    ctrl.screenshot(f'D:/UI_Agent/screenshots/diag_scroll_{scroll_y_center}.png')
    
    # Check if native products moved
    native = get_native_products()
    if native:
        print(f"  Native products after scroll:")
        for t, nums in native[:3]:
            print(f"    {t[:30]} Y={nums[1]}")

# Now try tapping in the WebView area (around Y=1100 where we found products)
# But first scroll to find 乐小融V2 (which is at the bottom of the list)
print("\n=== Scrolling WebView product list ===")

# The WebView products seem to be around Y=1100 area
# Try scrolling that specific area
for i in range(5):
    # Scroll the WebView product area
    ctrl.swipe(600, 1300, 600, 500, 500)
    ctrl.wait(2)
    
    # Check for 乐小融V2 marker
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    
    # Check if we're on withdraw page
    if check_withdraw():
        print(f"  After scroll {i+1}: On withdraw page!")
        ctrl.screenshot(f'D:/UI_Agent/screenshots/diag_wv_scroll_{i}_success.png')
        break
    
    # Check what products are now at Y=1100 area
    # by tapping and seeing if we navigate
    print(f"  Scroll {i+1}: testing tap at (913, 1100)...")
    ctrl.tap(913, 1100)
    ctrl.wait(3)
    
    if check_withdraw():
        print(f"  Tap at (913,1100) after scroll {i+1}: SUCCESS!")
        ctrl.screenshot(f'D:/UI_Agent/screenshots/diag_wv_scroll_{i}_tap_success.png')
        break
    else:
        print(f"  Tap at (913,1100): no navigation, going back")
        ctrl.press_back()
        ctrl.wait(1)

# Alternative: try tapping at Y=1100 but different X to find different products
print("\n=== Try tapping at different X at Y=1100 ===")
for x in [300, 600, 913]:
    ctrl.tap(x, 1100)
    ctrl.wait(3)
    if check_withdraw():
        print(f"  ({x}, 1100): SUCCESS - withdraw page!")
        ctrl.screenshot(f'D:/UI_Agent/screenshots/diag_tap_x{x}.png')
        break
    else:
        print(f"  ({x}, 1100): no navigation")
        ctrl.press_back()
        ctrl.wait(1)

# Try different approach: look for "全部" tab to switch views
print("\n=== Looking for tabs ===")
xml = ctrl._dump_ui()
root = ET.fromstring(xml)
for n in root.iter('node'):
    t = n.get('text', '')
    if t in ['全部', '推荐', '全部产品']:
        b = n.get('bounds', '')
        print(f"  Tab: '{t}' at {b}")

# Check what's at the very top of the page (tabs?)
print("\n=== Elements near top (Y < 300) with text ===")
for n in root.iter('node'):
    t = n.get('text', '')
    b = n.get('bounds', '')
    if t:
        nums = [int(x) for x in re.findall(r'\d+', b)]
        if len(nums) >= 4 and 130 < nums[1] < 300 and nums[3] > nums[1]:
            print(f"  '{t}' bounds={b}")

print("\nDone")
