"""Прокси к go2rtc: видео доступно только после входа в АП."""
import asyncio
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import Response

from app.go2rtc import API_LISTEN, BASE_PATH
from app.users import User
from app.web.auth import current_user, user_from_session

router = APIRouter()
# Только файлы плеера. API go2rtc (/api/config и т.п.) наружу не отдаём — там пароли камер.
ALLOWED_FILES = {"stream.html", "video-stream.js", "video-rtc.js"}


@router.get(BASE_PATH + "/{path:path}")
async def http_proxy(path: str, request: Request, user: User = Depends(current_user)):
    if path not in ALLOWED_FILES:
        return Response(status_code=404)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"http://{API_LISTEN}{BASE_PATH}/{path}",
                                 params=dict(request.query_params))
        except httpx.HTTPError:
            return Response("go2rtc недоступен", status_code=502)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@router.websocket(BASE_PATH + "/api/ws")
async def ws_proxy(ws: WebSocket):
    if user_from_session(ws.session, ws.app.state.users) is None:
        await ws.close(code=1008)
        return
    src = ws.query_params.get("src")
    if src is None or ws.app.state.cfg.camera(src) is None:
        await ws.close(code=1008)  # только настроенные камеры, не произвольные источники
        return
    await ws.accept()
    url = f"ws://{API_LISTEN}{BASE_PATH}/api/ws?" + urlencode({"src": src})
    try:
        async with websockets.connect(url, max_size=None) as upstream:
            async def client_to_upstream():
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await upstream.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])

            async def upstream_to_client():
                async for m in upstream:
                    if isinstance(m, str):
                        await ws.send_text(m)
                    else:
                        await ws.send_bytes(m)

            tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except (OSError, websockets.WebSocketException):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
