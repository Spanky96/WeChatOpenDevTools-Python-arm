#!/usr/bin/env python3
"""Local XWeb remote-debug endpoint server.

WeChatAppEx renderers read `remote_debug_endpoint` from launch config and
dial it for XWeb remote debugging. This server accepts those connections
and dumps every frame so the protocol can be reverse-engineered.
"""
import asyncio
import datetime

import websockets

LOG = open("/tmp/wechat_rea/xweb_bridge.log", "a", buffering=1)


def log(*parts):
    stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{stamp}] " + " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.write(line + "\n")


async def handler(ws):
    peer = ws.remote_address
    log("CONNECT from", peer)
    try:
        async for message in ws:
            if isinstance(message, bytes):
                log(f"BIN {len(message)}B:", message[:64].hex())
            else:
                log("TXT:", message[:2000])
            # echo back for protocol probing
            await ws.send(message)
    except websockets.ConnectionClosed as e:
        log("CLOSED", peer, e.code, e.reason)
    except Exception as e:
        log("ERR", e)


async def main():
    async with websockets.serve(handler, "127.0.0.1", 9999,
                                 subprotocols=None, max_size=2 ** 22):
        log("xweb bridge listening on ws://127.0.0.1:9999")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
