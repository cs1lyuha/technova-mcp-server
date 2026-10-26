"""Teste end-to-end prin protocolul MCP (client în memorie, aceleași mesaje JSON-RPC ca pe stdio)."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from mcp import Client

from technova_mcp.auth import AuthError, RateLimiter
from technova_mcp.server import build_server, resolve_principal


def call(server, tool, args):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, args)
    return asyncio.run(go())


def tool_names(server):
    async def go():
        async with Client(server) as client:
            return sorted(t.name for t in (await client.list_tools()).tools)
    return asyncio.run(go())


def test_full_server_exposes_five_tools(conn, tokens):
    assert tool_names(build_server(conn, tokens, default_token="tok-admin")) == [
        "create_return", "get_order", "list_orders", "search_products", "update_order_status"]


def test_read_only_deployment_does_not_even_register_write_tools(conn, tokens):
    assert tool_names(build_server(conn, tokens, default_token="tok-admin", read_only=True)) == [
        "get_order", "list_orders", "search_products"]


def test_tool_annotations_tell_the_client_what_is_safe(conn, tokens):
    async def go():
        async with Client(build_server(conn, tokens, default_token="tok-admin")) as client:
            return {t.name: t.annotations for t in (await client.list_tools()).tools}
    ann = asyncio.run(go())
    assert ann["get_order"].read_only_hint is True
    assert ann["update_order_status"].destructive_hint is True


def test_no_token_means_no_data(conn, tokens):
    res = call(build_server(conn, tokens), "get_order", {"order_id": "TN-10013"})
    assert res.is_error and "Neautorizat" in res.content[0].text


def test_customer_scope_through_mcp(conn, tokens):
    server = build_server(conn, tokens, default_token="tok-agronord")
    assert not call(server, "get_order", {"order_id": "TN-10013"}).is_error
    other = call(server, "get_order", {"order_id": "TN-10002"})
    assert other.is_error and "nu a fost găsită" in other.content[0].text


def test_customer_cannot_write_even_if_tool_is_listed(conn, tokens):
    res = call(build_server(conn, tokens, default_token="tok-agronord"), "create_return",
               {"order_id": "TN-10013", "sku": "LAP-MBA13", "qty": 1, "reason": "test"})
    assert res.is_error and "Interzis" in res.content[0].text


@pytest.mark.parametrize("args", [
    {"order_id": "10013"},                       # format greșit
    {"order_id": "TN-10013; DROP TABLE orders"},  # injecție
])
def test_input_validation_rejects_bad_order_ids(conn, tokens, args):
    assert call(build_server(conn, tokens, default_token="tok-admin"), "get_order", args).is_error


def test_input_limits(conn, tokens):
    server = build_server(conn, tokens, default_token="tok-admin")
    assert call(server, "search_products", {"query": "x" * 101}).is_error
    assert call(server, "list_orders", {"limit": 500}).is_error
    assert call(server, "create_return", {"order_id": "TN-10013", "sku": "LAP-MBA13", "qty": 1,
                                          "reason": "a" * 501}).is_error


def test_return_via_mcp_is_idempotent(conn, tokens):
    server = build_server(conn, tokens, default_token="tok-support")
    args = {"order_id": "TN-10013", "sku": "LAP-MBA13", "qty": 1, "reason": "defect", "idempotency_key": "agent-42"}
    first, second = call(server, "create_return", args), call(server, "create_return", args)
    assert not first.is_error and not second.is_error
    assert conn.execute("SELECT COUNT(*) FROM returns").fetchone()[0] == 1


def test_policy_error_is_explained(conn, tokens):
    res = call(build_server(conn, tokens, default_token="tok-admin"), "create_return",
               {"order_id": "TN-10009", "sku": "EAR-APP", "qty": 1, "reason": "nu-mi plac"})
    assert res.is_error and "in-ear" in res.content[0].text


def test_rate_limit(conn, tokens):
    server = build_server(conn, tokens, default_token="tok-admin", limiter=RateLimiter(rate=1, burst=2))
    results = [call(server, "search_products", {"query": "Laptop"}) for _ in range(3)]
    assert [r.is_error for r in results] == [False, False, True]
    assert "Prea multe cereri" in results[2].content[0].text


def test_every_call_is_audited_without_raw_arguments(conn, tokens):
    server = build_server(conn, tokens, default_token="tok-agronord")
    call(server, "get_order", {"order_id": "TN-10013"})
    call(server, "get_order", {"order_id": "TN-10002"})
    rows = conn.execute("SELECT principal, tool, args, outcome FROM tool_calls ORDER BY id").fetchall()
    assert [(r["principal"], r["outcome"]) for r in rows] == [("agronord", "ok"), ("agronord", "not_found")]
    assert "TN-10013" not in rows[0]["args"]  # doar hash


def test_resources_and_prompt(conn, tokens):
    async def go():
        async with Client(build_server(conn, tokens, default_token="tok-admin")) as client:
            policy = await client.read_resource("technova://policies/retur")
            catalog = await client.read_resource("technova://catalog")
            prompt = await client.get_prompt("support_reply", {"order_id": "TN-10013", "question": "Pot returna?"})
            return policy, catalog, prompt
    policy, catalog, prompt = asyncio.run(go())
    assert "30 de zile" in policy.contents[0].text
    assert "LAP-TP14" in catalog.contents[0].text
    assert "get_order" in prompt.messages[0].content.text


def test_http_bearer_wins_over_process_token(tokens):
    ctx = NS(headers={"Authorization": "Bearer tok-agronord"})
    assert resolve_principal(tokens, ctx, "tok-admin").role == "customer"
    assert resolve_principal(tokens, NS(headers=None), "tok-admin").role == "admin"
    with pytest.raises(AuthError):
        resolve_principal(tokens, NS(headers={"Authorization": "Bearer fals"}), None)
