"""__main__.py —— 启动入口

启动方式：
    cd agent-python/enterprise_oa_mcp_server
    uv run python -m enterprise_oa_mcp_server

或（在主包目录下）：
    uv run --project enterprise_oa_mcp_server python -m enterprise_oa_mcp_server
"""

from __future__ import annotations

import asyncio
import logging

from .server import build_server, read_host_port_path


async def _run() -> None:
    logging.basicConfig(level=logging.INFO)
    host, port, path = read_host_port_path()
    server = build_server()
    print(
        f"enterprise-oa-mcp starting at http://{host}:{port}{path} (Streamable HTTP)",
        flush=True,
    )
    await server.run_streamable_http_async(host=host, port=port, streamable_http_path=path)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
