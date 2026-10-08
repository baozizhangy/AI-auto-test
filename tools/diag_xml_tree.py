"""诊断: 分析XML树中乐小融V2附近节点结构"""
import xml.etree.ElementTree as ET
import sys
sys.path.insert(0, 'D:/UI_Agent')

tree = ET.parse('D:/UI_Agent/screenshots/diag_now.xml')
root = tree.getroot()

# Flatten all nodes
nodes = list(root.iter('node'))
print(f"Total nodes: {len(nodes)}")

# Find 乐小融V2 index
for i, n in enumerate(nodes):
    if '乐小融V2' in n.get('text', ''):
        print(f"\n乐小融V2 at flat index {i}")
        print(f"\nNodes around it (i-8 to i+8):")
        for j in range(max(0, i-8), min(len(nodes), i+8)):
            t = nodes[j].get('text', '')[:50]
            b = nodes[j].get('bounds', '')
            c = nodes[j].get('class', '')
            print(f"  {j:3d}: text={t:50s} bounds={b:25s} class={c}")
        break

# Count "立即提现" buttons and their indices
print("\n'立即提现' button indices:")
btn_indices = []
for i, n in enumerate(nodes):
    if n.get('text', '') == '立即提现':
        btn_indices.append(i)
        print(f"  {i}: bounds={n.get('bounds','')}")

# Find WebView containers
print("\nWebView / ScrollView containers:")
for i, n in enumerate(nodes):
    cls = n.get('class', '')
    if any(k in cls for k in ['WebView', 'ScrollView', 'RecyclerView']):
        print(f"  {i}: class={cls} bounds={n.get('bounds','')}")

# Find all top-level structure (parent nodes of products)
print("\nParent structure of first product:")
for n in root.iter('node'):
    if '时光分期' in n.get('text', ''):
        # Walk up (we need parent tracking)
        break

# Find elements with real bounds (top != 130)
print("\nElements with real Y bounds (top != 130):")
real = [(n.get('text','')[:30], n.get('bounds',''), n.get('class','')) for n in root.iter('node') if n.get('text','') and n.get('bounds','[0,0][0,0]') != '[0,0][0,0]']
for t, b, c in real:
    nums = [int(x) for x in __import__('re').findall(r'\d+', b)]
    if len(nums) >= 4 and nums[1] != 130:
        print(f"  {t:30s} bounds={b} class={c}")
