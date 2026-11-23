# -*- coding: utf-8 -*-
"""
跨平台环境安装：Mac / Windows 通用。
用法：   python setup_env.py
它会：建 .venv → 升 pip → 装依赖（走国内镜像）。

版本组合是实测锁定的（Intel Mac + Python 3.12）：
  torch 2.2.2 / torchaudio 2.2.2 / numpy 1.26.4 / scipy 1.11.4
  speechbrain 1.0.0 / transformers 4.44.2
不要随意升版本——speechbrain 1.1+ 要 torch 2.7，装不上；numpy 2.x 与 torch 2.2 不兼容。
"""
import os
import sys
import subprocess
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / "Scripts" / "python.exe" if os.name == "nt" else VENV / "bin" / "python"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

# pip 装包时禁掉 pyc 编译：某些机器安全策略会拦"生成后删除临时文件"，不加会失败
ENV = dict(os.environ)
ENV["PYTHONDONTWRITEBYTECODE"] = "1"


def pip(*args):
    cmd = [str(PY), "-m", "pip", "install", "--no-compile", *args, "-i", MIRROR]
    print("▶", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=ENV)


def main():
    if not VENV.exists():
        print(f"建虚拟环境：{VENV}")
        venv.create(VENV, with_pip=True)
    if not PY.exists():
        sys.exit(f"找不到虚拟环境 Python：{PY}")

    pip("--upgrade", "pip")

    # 基础数值三件套（版本锁死，避免 numpy2/scipy 与 torch 不兼容）
    pip("numpy==1.26.4")
    pip("scipy==1.11.4")
    pip("imageio-ffmpeg", "soundfile")

    # torch + torchaudio（Windows 的 wheel 自带 CPU，无需额外源）
    pip("torch==2.2.2", "torchaudio==2.2.2")

    # transformers 4.x（5.x 依赖链太长会拖垮 speechbrain）
    pip("transformers==4.44.2")

    # speechbrain + 它用到的纯 Python 依赖
    pip("speechbrain==1.0.0")
    pip("tqdm", "joblib", "cloudpickle", "sentencepiece", "hyperpyyaml")
    pip("ruamel.yaml==0.17.40")  # 0.19 与 hyperpyyaml 1.2.3 不兼容

    print("\n✅ 环境装好。验证：")
    subprocess.run([str(PY), "-c",
                    "from speechbrain.inference.speaker import EncoderClassifier; print('OK')"],
                   env=ENV)
    print("\n下一步：改 config.json 两个路径，然后")
    print("   python build_samples.py   # 做声纹尺子")
    print("   python label_all.py       # 全剧打标签")


if __name__ == "__main__":
    main()
