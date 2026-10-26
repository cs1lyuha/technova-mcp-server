import pytest

from technova_mcp.auth import AuthError, Forbidden, RateLimiter, TokenStore, bearer, hash_token


def store():
    s = TokenStore()
    s.add("tok-admin", "ana", "admin")
    s.add("tok-client", "agronord", "customer", "1003600012345")
    return s


def test_tokens_are_stored_hashed():
    s = store()
    assert "tok-admin" not in str(s.entries)
    assert hash_token("tok-admin") in s.entries


def test_resolve_roles():
    s = store()
    assert s.resolve("tok-admin").role == "admin"
    client = s.resolve("tok-client")
    assert client.customer_idno == "1003600012345"
    assert client.can_see("1003600012345") and not client.can_see("1012600098765")


@pytest.mark.parametrize("token", [None, "", "gresit"])
def test_missing_or_wrong_token(token):
    with pytest.raises(AuthError):
        store().resolve(token)


def test_permissions():
    s = store()
    s.add("tok-support", "ion", "support")
    support = s.resolve("tok-support")
    support.require("create_return")
    with pytest.raises(Forbidden):
        support.require("update_status")
    with pytest.raises(Forbidden):
        s.resolve("tok-client").require("create_return")


def test_customer_role_needs_scope():
    with pytest.raises(ValueError):
        TokenStore().add("x", "x", "customer")
    with pytest.raises(ValueError):
        TokenStore().add("x", "x", "root")


def test_bearer_header_parsing():
    assert bearer({"Authorization": "Bearer abc"}) == "abc"
    assert bearer({"authorization": "bearer  abc "}) == "abc"
    assert bearer({"Authorization": "Basic abc"}) is None
    assert bearer(None) is None


def test_rate_limiter_refills():
    t = [0.0]
    rl = RateLimiter(rate=60, burst=2, clock=lambda: t[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
    assert rl.allow("b")  # altă cheie, alt bucket
    t[0] = 1.0  # 1 secundă = 1 token la 60/min
    assert rl.allow("a")


def test_load_from_file(tmp_path):
    s = store()
    path = tmp_path / "tokens.json"
    import json
    path.write_text(json.dumps(s.entries))
    assert TokenStore.load(path).resolve("tok-admin").name == "ana"
