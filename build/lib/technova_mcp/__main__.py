"""Pornire și administrare.

    python -m technova_mcp init                                   # creează technova.db cu date demo
    python -m technova_mcp token add ana admin                    # generează un token (se afișează o singură dată)
    python -m technova_mcp token add agronord customer --customer 1003600012345
    TECHNOVA_TOKEN=... python -m technova_mcp serve               # stdio (Claude Code, Claude Desktop, Inspector)
    python -m technova_mcp serve --http --port 8765               # Streamable HTTP, Bearer per cerere
    python -m technova_mcp serve --read-only                      # fără tool-uri de scriere
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from technova_mcp.auth import TokenStore
from technova_mcp.db import connect, seed
from technova_mcp.server import build_server

DEFAULT_DB = os.environ.get("TECHNOVA_DB", "technova.db")
DEFAULT_TOKENS = os.environ.get("TECHNOVA_TOKENS", "tokens.json")


def load_tokens(path: str) -> TokenStore:
    return TokenStore.load(path) if Path(path).exists() else TokenStore()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="technova-mcp")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--tokens", default=DEFAULT_TOKENS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    tok = sub.add_parser("token")
    tok_sub = tok.add_subparsers(dest="action", required=True)
    add = tok_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("role", choices=["customer", "support", "admin"])
    add.add_argument("--customer")
    tok_sub.add_parser("list")
    serve = sub.add_parser("serve")
    serve.add_argument("--http", action="store_true")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--read-only", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "init":
        seed(connect(args.db))
        print(f"Baza {args.db} e gata.")
        return 0

    if args.cmd == "token":
        store = load_tokens(args.tokens)
        if args.action == "add":
            token = "tn_" + secrets.token_urlsafe(24)
            store.add(token, args.name, args.role, args.customer)
            Path(args.tokens).write_text(json.dumps(store.entries, indent=2, ensure_ascii=False), encoding="utf-8")
            os.chmod(args.tokens, 0o600)
            print(f"Token pentru {args.name} ({args.role}). Se afișează o singură dată:\n{token}")
        else:
            for entry in store.entries.values():
                print(f"{entry['name']:<20} {entry['role']:<9} {entry.get('customer_idno') or ''}")
        return 0

    conn = connect(args.db)
    seed(conn)
    tokens = load_tokens(args.tokens)
    if args.http:
        # pe HTTP NU folosim tokenul procesului: fiecare cerere trebuie să vină cu Bearer propriu
        server = build_server(conn, tokens, default_token=None, read_only=args.read_only)
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server = build_server(conn, tokens, default_token=os.environ.get("TECHNOVA_TOKEN"),
                              read_only=args.read_only)
        server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
