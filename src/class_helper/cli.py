"""class-helper 命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def get_lan_ip() -> str:
    """取本机局域网 IP（UDP connect 不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _print_banner(cfg, url: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        pass
    console.print(f"[bold green]{url}[/bold green]")
    console.print(f"  token: [cyan]{cfg.server.token}[/cyan]  归档目录: [cyan]{cfg.archive_root}[/cyan]")
    console.print(
        "[yellow]提示：App 填入上方地址与 token 即可连接。"
        "若手机连不上，先检查 Windows 防火墙放行该端口（README 有命令），"
        "或教学区 WiFi 与宿舍网段隔离（改用 Tailscale，见 README）。[/yellow]"
    )


def cmd_serve(args: argparse.Namespace) -> int:
    from class_helper.config import load_config

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]配置错误：{exc}[/red]")
        return 1
    if not Path("config.toml").exists() and args.config is None:
        console.print("[yellow]未找到 config.toml，正在使用默认配置（LLM 不可用，仅录音归档）。[/yellow]")
        console.print("[yellow]请复制 config.example.toml 为 config.toml 并填写 API key。[/yellow]")

    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port

    from class_helper.server.app import create_app

    app = create_app(cfg)

    lan_ip = get_lan_ip()
    url = f"ws://{lan_ip}:{cfg.server.port}/ws?token={cfg.server.token}"
    console.print("[bold]class-helper 服务启动[/bold]")
    _print_banner(cfg, url)

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from class_helper.config import load_config

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]配置错误：{exc}[/red]")
        return 1
    path = Path(args.file)
    if not path.exists():
        console.print(f"[red]文件不存在: {path}[/red]")
        return 1
    from class_helper.archive.importer import import_audio
    from class_helper.asr.faster_whisper_asr import WhisperASR
    from class_helper.brain.llm import LLMClient

    llm = LLMClient(cfg.llm) if cfg.llm.base_url else None
    asr = WhisperASR(cfg.asr)

    async def _run():
        await asr.load()
        console.print(f"ASR: {asr.describe()}")
        session_dir = await import_audio(path, args.course, cfg, llm, asr, console)
        console.print(f"[green]归档完成: {session_dir}[/green]")

    asyncio.run(_run())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="class-helper",
        description="代课bot：课堂听课值守、点名实时速答与课程归档",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="启动服务端（等 App 连接）")
    p_serve.add_argument("--config", default=None, help="配置文件路径（默认 ./config.toml）")
    p_serve.add_argument("--host", default=None, help="覆盖监听地址")
    p_serve.add_argument("--port", type=int, default=None, help="覆盖监听端口")

    p_import = sub.add_parser("import", help="课后导入录音文件，生成转写与复习总结")
    p_import.add_argument("file", help="音频文件（m4a/mp3/wav/webm 等 ffmpeg 支持的格式）")
    p_import.add_argument("--course", required=True, help="课程名")
    p_import.add_argument("--config", default=None, help="配置文件路径")

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
        if args.command == "import":
            return cmd_import(args)
    except KeyboardInterrupt:
        console.print("\n已退出")
        return 0
    except Exception:  # noqa: BLE001
        console.print_exception()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
