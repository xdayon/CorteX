#!/usr/bin/env python3
"""Checagem visual do Studio em 390 px e desktop (exigida para mudanças visuais).

    .venv/bin/python scripts/studio_visual_check.py --url http://127.0.0.1:8787/ --out /tmp/cortex-shots

Abre a página no Google Chrome real (Flatpak, headless, perfil temporário isolado),
tira screenshots em cada largura e reporta rolagem horizontal e erros de console.
Use o Chrome do Flatpak: o chrome-headless-shell e o Chromium do Playwright não
decodificam H.264, então a prévia de vídeo falha neles mesmo com o app correto.
Para checar o editor com dados reais sem tocar o banco, suba API/worker numa porta
separada com CORTEX__PATHS__DATABASE apontando para uma cópia do SQLite.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

PORT = 9345


def _launch(profile: Path) -> tuple[subprocess.Popen, str]:
    process = subprocess.Popen([
        "flatpak", "run", "com.google.Chrome", "--headless=new",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
        "--no-first-run", "--autoplay-policy=no-user-gesture-required", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read())
            page = next(target for target in targets if target["type"] == "page")
            return process, page["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.3)
    process.kill()
    raise SystemExit("Google Chrome (Flatpak) não iniciou; instale com: flatpak install com.google.Chrome")


async def _check(url: str, widths: list[int], out: Path) -> dict:
    profile = Path(tempfile.mkdtemp(prefix="cortex-visual-"))
    process, ws_url = _launch(profile)
    report: dict = {"url": url, "widths": {}}
    try:
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            counter = 0
            console: list[str] = []

            async def send(method: str, params: dict | None = None) -> dict:
                nonlocal counter
                counter += 1
                ident = counter
                await ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 60))
                    if message.get("method") == "Runtime.exceptionThrown":
                        console.append(str(message["params"]["exceptionDetails"].get("text"))[:300])
                    elif message.get("method") == "Log.entryAdded" and message["params"]["entry"]["level"] == "error":
                        console.append(message["params"]["entry"].get("text", "")[:300])
                    if message.get("id") == ident:
                        return message.get("result", {})

            for domain in ("Page", "Runtime", "Log"):
                await send(f"{domain}.enable")
            for width in widths:
                mobile = width < 700
                await send("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 844 if mobile else 900, "deviceScaleFactor": 1, "mobile": mobile,
                })
                await send("Page.navigate", {"url": url})
                await asyncio.sleep(4)
                metrics = await send("Runtime.evaluate", {
                    "expression": "({overflow: document.documentElement.scrollWidth - innerWidth})",
                    "returnByValue": True,
                })
                shot = await send("Page.captureScreenshot", {"format": "png"})
                path = out / f"studio-{width}.png"
                path.write_bytes(base64.b64decode(shot["data"]))
                report["widths"][width] = {
                    "horizontal_overflow_px": metrics.get("result", {}).get("value", {}).get("overflow"),
                    "screenshot": str(path),
                }
            report["console_errors"] = console
    finally:
        process.kill()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8787/")
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "cortex-shots")
    parser.add_argument("--widths", default="390,1440")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    widths = [int(value) for value in args.widths.split(",")]
    print(json.dumps(asyncio.run(_check(args.url, widths, args.out)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
