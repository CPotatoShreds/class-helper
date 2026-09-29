"""本机端到端冒烟（无 ffmpeg 依赖）：TTS wav → WS → ASR → 检测 → LLM 速答。

用法：先启动服务，再 python scripts/e2e_local.py [wav文件] [--token T]
wav 需为 16bit PCM（任意采样率/声道，自动重采样到 16k 单声道）。
"""

import asyncio
import json
import struct
import sys
import wave

import numpy as np
import websockets

URL = "ws://127.0.0.1:8765/ws"
CHUNK = 3200  # 200ms


def load_pcm_16k(path: str) -> bytes:
    w = wave.open(path, "rb")
    raw = w.readframes(w.getnframes())
    rate, ch = w.getframerate(), w.getnchannels()
    w.close()
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        t = np.arange(0, len(x) / rate, 1 / 16000) * rate
        x = np.interp(t, np.arange(len(x)), x)
    return x.astype(np.int16).tobytes()


def token_from_config() -> str:
    try:
        import tomllib

        with open("config.toml", "rb") as f:
            return tomllib.load(f)["server"]["token"]
    except Exception:
        return "change-me-please"


async def main() -> None:
    wav = sys.argv[1] if len(sys.argv) > 1 else "tts.wav"
    token = sys.argv[sys.argv.index("--token") + 1] if "--token" in sys.argv else token_from_config()
    pcm = load_pcm_16k(wav)
    print(f"载入 {len(pcm)/32000:.1f}s 音频", flush=True)

    async with websockets.connect(f"{URL}?token={token}") as ws:
        try:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            print("<<", await ws.recv(), flush=True)
            await ws.send(json.dumps({"type": "start_session", "course": "端到端验证"}))

            seq = 0
            for i in range(0, len(pcm), CHUNK):
                await ws.send(struct.pack("<I", seq) + pcm[i : i + CHUNK])
                seq += 1
                await asyncio.sleep(0.02)
            print(f">> 推流 {seq} 帧", flush=True)

            stopped = False
            while not stopped:
                raw = await asyncio.wait_for(ws.recv(), timeout=240)
                d = json.loads(raw)
                t = d.get("type")
                if t == "audio_ack":
                    continue
                if t == "transcript":
                    print(f"<< transcript [{d.get('wall')}] {d.get('text')}", flush=True)
                elif t == "alert":
                    print(f"<< ALERT {d.get('level')} | 问题: {d.get('question')}", flush=True)
                elif t == "answer_delta":
                    print(f"  + {d.get('text','')}", end="", flush=True)
                elif t == "answer_done":
                    print(f"\n<< ANSWER_DONE", flush=True)
                elif t == "session_stopped":
                    print(f"<< session_stopped 转写{d.get('transcripts')}条", flush=True)
                    stopped = True
                else:
                    print(f"<< {t} {str(d)[:100]}", flush=True)
        finally:
            await ws.send(json.dumps({"type": "stop_session"}))


if __name__ == "__main__":
    asyncio.run(main())
