import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)
os.environ["HF_HUB_DISABLE_XET"] = "1"
import urllib.request
print("系统代理(注册表):", urllib.request.getproxies(), flush=True)
from huggingface_hub import snapshot_download
t = time.time()
p = snapshot_download("Systran/faster-whisper-small", max_workers=2)
print(f"OK {p} ({time.time()-t:.0f}s)", flush=True)
