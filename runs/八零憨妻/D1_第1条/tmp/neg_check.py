"""反向测试：去掉 act.restricted 后，大结局门禁是否报 ERROR。

只读原包，副本写在同目录，跑完不删（runs 下的临时验证产物）。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PACK = ROOT / "packs/short-drama/八零憨妻的甜蜜日常/drama-pack.json"
TMP = Path(__file__).resolve().parent / "neg_pack.json"

d = json.loads(PACK.read_text(encoding="utf-8"))
for a in d["story_arc"]["acts"]:
    a.pop("restricted", None)
TMP.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

r = subprocess.run(
    [sys.executable, str(ROOT / "skills/short-drama-pack/scripts/validate_pack.py"), str(TMP)],
    capture_output=True, text=True,
)
print(r.stdout[-1200:])
print("---- 期望：出现 act 6 覆盖第 [20] 集却未标 restricted 的 ERROR ----")
