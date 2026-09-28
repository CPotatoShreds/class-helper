"""端到端冒烟：TTS 语音 → WS 推流 → VAD/ASR → transcript 事件。

用法：uv run python scripts/e2e_smoke.py [wav文件]
依赖服务端已启动（serve）。用 tiny 模型即可跑通。
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys

import imageio_ffmpeg
import websockets

URL = "ws://127.0.0.1:8765/ws"
TOKEN = "change-me-please"
CHUNK = 3200  # 200ms


async def load_pcm(path: str) -> bytes:
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = await asyncio.create_subprocess_exec(
        exe, "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-vn", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out


async def main() -> None:
    pcm = await load_pcm(sys.argv[1] if len(sys.argv) > 1 else "tts.wav")
    print(f"载入音频 {len(pcm)/16000:.1f}s")
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        print("<<", await ws.recv())

        await ws.send(json.dumps({"type": "start_session", "course": "冒烟测试"}))
        seq = 0
        for i in range(0, len(pcm), CHUNK):
            await ws.send(struct.pack("<I", seq) + pcm[i : i + CHUNK])
            seq += 1
            await asyncio.sleep(0.02)
        print(f">> 已推流 {seq} 帧，等待转写...")

        stopped = False
        transcripts = 0
        while not stopped:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
            except TimeoutError:
                print("!! 180s 无消息，放弃等待")
                break
            data = json.loads(raw)
            t = data.get("type")
            if t == "audio_ack":
                continue
            if t == "transcript":
                transcripts += 1
                print(f"<< transcript [{data.get('wall')}] {data.get('text')}")
            elif t == "status":
                print("<< status", data.get("detail"), data.get("message"))
            elif t in {"alert", "answer_done", "error"}:
                print("<<", data)
            elif t == "session_stopped":
                print("<< session_stopped", data)
                stopped = True
                break
        if not stopped:
            await ws.send(json.dumps({"type": "stop_session"}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                data = json.loads(raw)
                if data.get("type") == "session_stopped":
                    print("<< session_stopped", data)
                    break
                if data.get("type") != "audio_ack":
                    print("<<", data.get("type"))
    print("E2E 完成")


if __name__ == "__main__":
    asyncio.run(main())
