"""Pornește serverul ca proces separat și vorbește cu el pe stdio, exact cum face Claude Code."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from technova_mcp.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def test_token_cli_and_stdio_server(tmp_path, capsys):
    db, tokens = str(tmp_path / "t.db"), str(tmp_path / "tokens.json")
    main(["--db", db, "--tokens", tokens, "init"])
    main(["--db", db, "--tokens", tokens, "token", "add", "ana", "support"])
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("tn_") and token not in Path(tokens).read_text()
    assert oct(os.stat(tokens).st_mode)[-3:] == "600"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "technova_mcp", "--db", db, "--tokens", tokens, "serve"],
        env={**os.environ, "TECHNOVA_TOKEN": token, "PYTHONPATH": str(ROOT)},
    )

    async def go():
        async with Client(params) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            result = await client.call_tool("get_order", {"order_id": "TN-10013"})
            return names, result

    names, result = asyncio.run(asyncio.wait_for(go(), timeout=30))
    assert "create_return" in names
    assert not result.is_error and "LAP-MBA13" in result.content[0].text
