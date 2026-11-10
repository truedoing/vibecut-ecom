"""大结局门禁回归测试。

做法：把包里覆盖大结局集的幕的 `restricted` 临时摘掉，跑 validate_pack.py，
确认它**确实**报 ERROR。门禁如果只是写在文档里、代码里没生效，这个测试会失败。

用法：
  python check_finale_gate.py packs/short-drama/八零憨妻的甜蜜日常/drama-pack.json
退出码：0 = 门禁有效；1 = 门禁失效或该包没有可测的幕。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VALIDATOR = HERE / "validate_pack.py"
NEEDLE = "却没标 restricted"
# 临时包写到 build/（已 gitignore）；跑完删掉，删不掉也不污染仓库
SCRATCH = ROOT / "build" / ".gatecheck"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    pack = Path(sys.argv[1]).resolve()
    d = json.loads(pack.read_text(encoding="utf-8"))

    finale = set((d.get("asset_index") or {}).get("finale_episodes") or [])
    acts = (d.get("story_arc") or {}).get("acts") or []
    targets = [
        a for a in acts
        if isinstance(a.get("ep_from"), int) and isinstance(a.get("ep_to"), int)
        and finale & set(range(a["ep_from"], a["ep_to"] + 1))
    ]
    if not targets:
        print(f"跳过：{pack.name} 没有覆盖大结局集 {sorted(finale) or '(未声明 finale_episodes)'} 的幕")
        return 1
    for a in targets:
        if not a.get("restricted"):
            print(f"包里 act {a.get('no')} 覆盖大结局却没标 restricted —— 包本身不合规，先修包")
            return 1

    mutated = json.loads(json.dumps(d, ensure_ascii=False))
    for a in mutated["story_arc"]["acts"]:
        a.pop("restricted", None)

    SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp = SCRATCH / f"{pack.stem}.json"
    tmp.write_text(json.dumps(mutated, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(VALIDATOR), str(tmp)],
            capture_output=True, text=True,
        )
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # 安全策略可能禁止删除，build/ 已忽略，留着无害

    hit = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("ERROR") and NEEDLE in ln]
    if hit:
        print(f"✓ 门禁有效：{hit[0]}")
        print(f"  （被测幕：{[a.get('no') for a in targets]}，大结局集 {sorted(finale)}）")
        return 0
    print("✗ 门禁失效：摘掉 restricted 后校验器仍判通过")
    print(r.stdout[-800:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
