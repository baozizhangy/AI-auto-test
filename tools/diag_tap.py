"""实验: 找到WebView "立即提现"按钮的实际屏幕位置"""
import sys
sys.path.insert(0, 'D:/UI_Agent')
from agent.adb_controller import ADBController
from agent.config import Config
import xml.etree.ElementTree as ET
import re

ctrl = ADBController(serial=Config.ANDROID_SERIAL)
w, h = ctrl.get_screen_size()

def check_on_withdraw_page():
    """Check if we're on the withdraw page"""
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    markers = ["借款申请", "可取现额度", "确认借款"]
    for n in root.iter('node'):
        t = n.get('text', '')
        for m in markers:
            if m in t:
                return True, t
    return False, None

def count_immediate_buttons():
    xml = ctrl._dump_ui()
    root = ET.fromstring(xml)
    count = 0
    for n in root.iter('node'):
        if n.get('text', '') == '立即提现':
            count += 1
    return count

# First go back to home page
ctrl.press_back()
ctrl.wait(1)
ctrl.press_back()
ctrl.wait(1)

# Restart app
ctrl.stop_test_app()
ctrl.wait(2)
ctrl.start_test_app()
ctrl.wait(8)

# Check current state
xml = ctrl._dump_ui()
root = ET.fromstring(xml)
# Check how many "立即提现" and "立即申请" buttons
wv_btns = sum(1 for n in root.iter('node') if n.get('text','') == '立即提现')
native_btns = sum(1 for n in root.iter('node') if n.get('text','') == '立即申请')
print(f"WebView '立即提现' buttons: {wv_btns}")
print(f"Native '立即申请' buttons: {native_btns}")

# Find a native "立即申请" button to test tapping
for n in root.iter('node'):
    if n.get('text','') == '立即申请':
        b = n.get('bounds','')
        nums = [int(x) for x in re.findall(r'\d+', b)]
        cx = (nums[0] + nums[2]) // 2
        cy = (nums[1] + nums[3]) // 2
        print(f"\nNative '立即申请' at center=({cx},{cy}) bounds={b}")
        break

# Strategy: try tapping at X=913 (WebView button X center) at different Y positions
# The WebView products should be somewhere on screen
# Try Y positions from 200 to 2400 in steps
print("\n=== Tapping at X=913 at various Y positions ===")

# First, let's try the first WebView product button
# It should be near the top of the WebView area
test_ys = [200, 350, 500, 700, 900, 1100, 1300, 1500, 1700, 1900, 2100]

for y in test_ys:
    # Go back to home if we navigated away
    on_wp, marker = check_on_withdraw_page()
    if on_wp:
        print(f"  Y={y}: ALREADY on withdraw page! (marker={marker})")
        break
    
    # Make sure we're on home
    ctrl._dump_ui()  # ensure fresh dump
    
    # Tap at (913, y)
    ctrl.tap(913, y)
    ctrl.wait(3)
    
    # Check if we navigated
    on_wp, marker = check_on_withdraw_page()
    if on_wp:
        print(f"  Y={y}: SUCCESS! Navigated to withdraw page (marker={marker})")
        ctrl.screenshot(f'D:/UI_Agent/screenshots/diag_tap_{y}_success.png')
        break
    else:
        print(f"  Y={y}: no navigation")
        # Go back
        ctrl.press_back()
        ctrl.wait(1)

# Also try: tap on native "立即申请" to see what happens
print("\n=== Try tapping native '立即申请' ===")
ctrl._dump_ui()
for n in ET.fromstring(ctrl._dump_ui()).iter('node'):
    if n.get('text','') == '立即申请':
        b = n.get('bounds','')
        nums = [int(x) for x in re.findall(r'\d+', b)]
        cx = (nums[0] + nums[2]) // 2
        cy = (nums[1] + nums[3]) // 2
        print(f"Tapping native '立即申请' at ({cx},{cy})")
        ctrl.tap(cx, cy)
        ctrl.wait(5)
        on_wp, marker = check_on_withdraw_page()
        print(f"After native tap: on_withdraw={on_wp}, marker={marker}")
        ctrl.screenshot('D:/UI_Agent/screenshots/diag_native_tap.png')
        ctrl.press_back()
        ctrl.wait(1)
        break

print("\nDone")
