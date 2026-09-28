"""FastAPI 服务：WS（控制+音频+事件推送）与 REST（归档浏览、探活）。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from class_helper.brain.llm import LLMClient
from class_helper.config import AppConfig
from class_helper.protocol import (
    MSG_AUTH,
    MSG_AUTH_OK,
    MSG_ERROR,
    MSG_MANUAL_ALERT,
    MSG_PING,
    MSG_PONG,
    MSG_START_SESSION,
    MSG_STOP_SESSION,
    decode_msg,
    encode_msg,
    unpack_audio,
)
from class_helper.server.session import ClassSession

logger = logging.getLogger(__name__)

AUTH_TIMEOUT = 15  # 秒，等首条 auth 消息


@dataclass(eq=False)  # 仅按身份比较，可放入 set
class ClientConn:
    ws: WebSocket
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


class AppState:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.llm = LLMClient(cfg.llm)
        self.asr = self._make_asr(cfg)
        self.session: ClassSession | None = None
        self.clients: set[ClientConn] = set()
        self.asr_loading = False
        self.last_audio_ack = -1

    @staticmethod
    def _make_asr(cfg: AppConfig):
        # 局部导入：加载 faster-whisper 较重，推迟到真正创建服务时
        from class_helper.asr.faster_whisper_asr import WhisperASR

        return WhisperASR(cfg.asr)

    def broadcast(self, message: dict[str, Any]) -> None:
        """向所有已连接客户端推送事件。"""
        text = encode_msg(message["type"], **{k: v for k, v in message.items() if k != "type"})
        for client in list(self.clients):
            try:
                client.queue.put_nowait(text)
            except asyncio.QueueFull:
                logger.warning("客户端事件队列满，丢弃事件")

    async def emit(self, message: dict[str, Any]) -> None:
        """会话用的事件回调（协程形式）。"""
        self.broadcast(message)

    async def ensure_asr(self) -> bool:
        """确保 ASR 就绪；未就绪返回 False（加载在后台进行）。"""
        if self.asr.loaded:
            return True
        if self.asr_loading:
            return False
        self.asr_loading = True
        self.broadcast(
            {"type": "status", "detail": "asr_loading", "message": "正在加载语音模型（首次需下载）..."}
        )

        async def _load() -> None:
            try:
                desc = await self.asr.load()
                self.broadcast({"type": "status", "detail": "asr_ready", "message": desc})
            except Exception as exc:  # noqa: BLE001
                logger.exception("ASR 加载失败")
                self.broadcast({"type": "status", "detail": "asr_failed", "message": str(exc)})
            finally:
                self.asr_loading = False

        asyncio.create_task(_load())
        return False

    async def start_session(self, course: str) -> None:
        if self.session and not self.session.closed:
            self.broadcast({"type": MSG_ERROR, "message": "已有进行中的会话，请先结束当前会话"})
            return
        # 会话先建先跑：ASR 后台加载，加载完成前语音段暂缺（音频已存档），无需客户端重试
        await self.ensure_asr()
        session = ClassSession(
            course=course,
            cfg=self.cfg,
            llm=self.llm,
            asr=self.asr,
            emit=self.emit,
        )
        self.session = session
        self.last_audio_ack = -1
        await session.run()
        logger.info("会话开始: %s", course)

    async def stop_session(self) -> None:
        if not self.session:
            self.broadcast({"type": MSG_ERROR, "message": "当前没有进行中的会话"})
            return
        result = await self.session.stop()
        self.session = None
        logger.info("会话结束: %s", result)


def create_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="class-helper", version="0.1.0", docs_url=None, redoc_url=None)
    state = AppState(cfg)
    app.state.helper = state

    async def handle_control(conn: ClientConn, raw: str) -> None:
        try:
            msg = decode_msg(raw)
        except ValueError as exc:
            await conn.ws.send_text(encode_msg(MSG_ERROR, message=str(exc)))
            return
        mtype = msg.get("type")
        if mtype == MSG_PING:
            await conn.ws.send_text(encode_msg(MSG_PONG))
        elif mtype == MSG_START_SESSION:
            await state.start_session(str(msg.get("course", "")).strip())
        elif mtype == MSG_STOP_SESSION:
            await state.stop_session()
        elif mtype == MSG_MANUAL_ALERT:
            if state.session:
                await state.session.manual_alert()
            else:
                await conn.ws.send_text(encode_msg(MSG_ERROR, message="当前没有进行中的会话"))
        else:
            await conn.ws.send_text(encode_msg(MSG_ERROR, message=f"未知消息类型: {mtype}"))

    def handle_audio(data: bytes) -> None:
        if not state.session:
            return
        try:
            seq, pcm = unpack_audio(data)
        except ValueError:
            logger.warning("非法音频帧，丢弃 %d 字节", len(data))
            return
        state.session.push_audio_seq(seq, pcm)
        ack = state.session.ack_seq()
        if ack != state.last_audio_ack:
            state.last_audio_ack = ack
            state.broadcast({"type": "audio_ack", "seq": ack})

    async def ws_sender(conn: ClientConn) -> None:
        try:
            while True:
                text = await conn.queue.get()
                await conn.ws.send_text(text)
        except asyncio.CancelledError:
            pass

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        conn = ClientConn(ws=ws)
        sender = asyncio.create_task(ws_sender(conn))
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT)
            msg = decode_msg(raw)
            if msg.get("type") != MSG_AUTH or msg.get("token") != cfg.server.token:
                await ws.send_text(encode_msg(MSG_ERROR, message="鉴权失败：token 错误"))
                await ws.close(code=4001)
                return
            await ws.send_text(encode_msg(MSG_AUTH_OK, server_version=app.version))
            state.clients.add(conn)
            if state.session:
                payload = state.session.status()
                await ws.send_text(encode_msg("session_state", **payload))
            logger.info("客户端已连接（当前 %d 个）", len(state.clients))

            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if (text := message.get("text")) is not None:
                    await handle_control(conn, text)
                elif (data := message.get("bytes")) is not None:
                    handle_audio(data)
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            logger.info("客户端连接后超时未鉴权，关闭")
        except Exception:  # noqa: BLE001
            logger.exception("WS 连接异常")
        finally:
            state.clients.discard(conn)
            sender.cancel()
            logger.info("客户端断开（当前 %d 个）", len(state.clients))

    @app.get("/")
    async def index() -> JSONResponse:
        return JSONResponse(
            {"app": "class-helper", "hint": "用 class-helper App 连接 ws://<host>:<port>/ws"}
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "asr": state.asr.describe(),
                "asr_loading": state.asr_loading,
                "session": state.session.status() if state.session else None,
                "clients": len(state.clients),
            }
        )

    def check_token(token: str | None) -> bool:
        return bool(token) and token == cfg.server.token

    @app.get("/api/archive")
    async def archive_list(token: str | None = Query(default=None)) -> JSONResponse:
        if not check_token(token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        root = cfg.archive_root
        out: list[dict] = []
        if root.is_dir():
            for course_dir in sorted(root.iterdir()):
                if not course_dir.is_dir():
                    continue
                sessions = []
                for date_dir in sorted(course_dir.iterdir()):
                    if not date_dir.is_dir():
                        continue
                    for sess_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
                        files = sorted(
                            f.name
                            for f in sess_dir.iterdir()
                            if f.suffix in {".md", ".jsonl"} and f.is_file()
                        )
                        sessions.append(
                            {"date": date_dir.name, "session": sess_dir.name, "files": files}
                        )
                out.append({"course": course_dir.name, "sessions": sessions})
        return JSONResponse(out)

    @app.get("/api/archive/{course}/{date}/{session_name}/{filename}")
    async def archive_file(
        course: str,
        date: str,
        session_name: str,
        filename: str,
        token: str | None = Query(default=None),
    ) -> JSONResponse:
        if not check_token(token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not filename.endswith((".md", ".jsonl")):
            return JSONResponse({"error": "只允许读取 .md/.jsonl"}, status_code=400)
        root = cfg.archive_root.resolve()
        path = (root / course / date / session_name / filename).resolve()
        if not path.is_relative_to(root):
            return JSONResponse({"error": "非法路径"}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"filename": filename, "content": path.read_text(encoding="utf-8")})

    return app
