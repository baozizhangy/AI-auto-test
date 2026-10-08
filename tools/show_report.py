"""查看最新测试报告"""
import json
from pathlib import Path

report_dir = Path('D:/UI_Agent/reports')
reports = sorted(report_dir.glob('lexiang_withdraw_flow_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)

if not reports:
    print("No reports found")
    exit()

latest = reports[0]
print(f"Report: {latest.name}\n")

r = json.load(open(latest, encoding='utf-8'))
passed = sum(1 for s in r['steps'] if s['passed'])
total = len(r['steps'])

for i, s in enumerate(r['steps']):
    status = "PASS" if s['passed'] else "FAIL"
    dur = f"{s['duration_ms']/1000:.1f}s" if s['duration_ms'] > 0 else "0s"
    print(f"  {i+1:2d}. [{status}] {s['step_name']}")
    if not s['passed']:
        print(f"      -> {s['summary']}")
    if s['duration_ms'] > 0:
        print(f"      ({dur})")

print(f"\n  Result: {passed}/{total} passed")
print(f"  Duration: {r['total_duration_ms']/1000:.1f}s")
