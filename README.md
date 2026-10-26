# 🔌 technova-mcp-server

Un server **MCP (Model Context Protocol)** care dă unui agent AI (Claude Code, Claude Desktop, orice client MCP)
acces controlat la datele unui magazin online: produse, comenzi, retururi și politici.

Accentul nu e pe „merge”, ci pe **ce se întâmplă când îl dai unui model în producție**: cine are voie la ce,
ce face un retry, ce face un text malițios dintr-o comandă și cum afli după aceea cine a apelat ce.

> Pasul 4 din planul meu de studiu: Proiectul 3 (MCP Server propriu). Construit pe [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 2.x.

![CI](https://github.com/cs1lyuha/technova-mcp-server/actions/workflows/ci.yml/badge.svg)

## Ce expune

| Tip | Nume | Rol minim | Ce face |
|---|---|---|---|
| tool | `search_products` | customer | caută în catalog (preț, stoc) |
| tool | `get_order` | customer (doar ale lui) | status, produse, total, retururi, termen-limită de retur |
| tool | `list_orders` | customer (doar ale lui) | comenzi filtrate după client și status |
| tool | `create_return` | support | cerere de retur, cu politica verificată în cod și `idempotency_key` |
| tool | `update_order_status` | admin | tranziții de status, doar cele permise de mașina de stări |
| resursă | `technova://policies/{retur,livrare,garantie}` | — | politicile oficiale, în Markdown |
| resursă | `technova://catalog` | — | catalogul complet, în JSON |
| prompt | `support_reply` | — | pașii pentru un răspuns de suport corect |

Tool-urile au adnotări MCP (`readOnlyHint`, `destructiveHint`, `idempotentHint`), ca clientul să știe ce poate rula fără confirmare.

## Securitate: MCP server ca suprafață de atac

| Risc | Ce am făcut | Test |
|---|---|---|
| Agentul cere date care nu-i aparțin | rolul `customer` vede doar firma din token; pentru o comandă străină primește **„nu a fost găsită”**, nu „interzis”, ca să nu afle ce comenzi există | `test_customer_scope_through_mcp` |
| Prea multe drepturi | 3 roluri cu permisiuni explicite; `--read-only` **nici nu înregistrează** tool-urile de scriere | `test_read_only_deployment_does_not_even_register_write_tools` |
| Verificarea doar în stratul MCP | drepturile se verifică și în `service.py`, lângă date | `test_customer_cannot_create_returns` |
| Input malițios / greșit | Pydantic: `order_id` după regex `^TN-\d{5}$`, limite de lungime și interval, enum pentru status; SQL doar parametrizat | `test_input_validation_rejects_bad_order_ids`, `test_search_is_parameterized_not_injectable` |
| **Prompt injection prin date** | nota clientului e încadrată în `<untrusted_customer_text>`, iar instrucțiunile serverului spun modelului că e doar text. Comanda demo `TN-10008` conține chiar un astfel de atac | `test_untrusted_note_is_marked` |
| Retry-ul agentului dublează acțiunea | `create_return` acceptă `idempotency_key`: același apel → aceeași cerere | `test_return_via_mcp_is_idempotent` |
| Tokeni furați din config | se salvează doar SHA-256, fișierul are `chmod 600`, comparația e în timp constant | `test_tokens_are_stored_hashed`, `test_token_cli_and_stdio_server` |
| Pe HTTP, un apel fără token „moștenește” drepturile procesului | pe HTTP tokenul procesului e dezactivat; fiecare cerere are nevoie de `Authorization: Bearer` | `test_http_bearer_wins_over_process_token` |
| Abuz / bucle de agent | rate limit token bucket per utilizator (60/min, rafale de 20) | `test_rate_limit` |
| „Cine a făcut asta?” | fiecare apel ajunge în `tool_calls` (cine, ce tool, rezultat). Argumentele se salvează **doar ca hash**, fără date personale | `test_every_call_is_audited_without_raw_arguments` |
| Scurgeri de detalii interne | excepțiile neprevăzute devin „Eroare internă. Incidentul a fost înregistrat.”, iar stack trace-ul rămâne în log | — |

## Instalare și conectare la Claude Code

```bash
git clone https://github.com/cs1lyuha/technova-mcp-server.git
cd technova-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

technova-mcp init                                   # technova.db cu 24 de comenzi demo
technova-mcp token add ana support                  # afișează tokenul o singură dată
technova-mcp token add agronord customer --customer 1003600012345
```

**Claude Code** (stdio):

```bash
claude mcp add technova \
  -e TECHNOVA_TOKEN=tn_... -e TECHNOVA_DB=$PWD/technova.db -e TECHNOVA_TOKENS=$PWD/tokens.json \
  -- $PWD/.venv/bin/technova-mcp serve
```

Apoi, în Claude Code: *„Ce conține comanda TN-10013 și până când se poate returna?”* sau *„Fă un retur pentru un MacBook din TN-10013, ecranul are pixeli morți.”*

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "technova": {
      "command": "/cale/către/.venv/bin/technova-mcp",
      "args": ["serve"],
      "env": { "TECHNOVA_TOKEN": "tn_...", "TECHNOVA_DB": "/cale/technova.db", "TECHNOVA_TOKENS": "/cale/tokens.json" }
    }
  }
}
```

**MCP Inspector:**

```bash
npx @modelcontextprotocol/inspector -e TECHNOVA_TOKEN=tn_... .venv/bin/technova-mcp serve
```

**HTTP (Streamable HTTP) + Docker:**

```bash
docker build -t technova-mcp .
docker run --rm -v technova-data:/home/mcp/data technova-mcp technova-mcp token add ana admin
docker run -d -p 8765:8765 -v technova-data:/home/mcp/data technova-mcp
# clientul trimite: Authorization: Bearer tn_...   la  http://localhost:8765/mcp
```

Imaginea rulează ca utilizator neprivilegiat (`uid 10001`), cu datele într-un volum și cu health check. CI-ul construiește imaginea și verifică că pornește.

## Stdio vs. Streamable HTTP

| | stdio | Streamable HTTP |
|---|---|---|
| cine pornește serverul | clientul, ca subproces | rulează separat (Docker, VM) |
| identitatea | un token per proces (`TECHNOVA_TOKEN`) | un token per cerere (`Bearer`) |
| potrivit pentru | un dezvoltator, local | o echipă, un server comun |
| riscuri în plus | puține: procesul e local | rețea: TLS (reverse proxy), CORS, rate limit pe IP |

## Teste

```bash
pip install pytest && pytest -q        # 43 de teste
```

- `test_service.py`: logica de business fără MCP (politica de retur, mașina de stări, vizibilitate).
- `test_server.py`: **prin protocolul MCP**, cu un client în memorie (aceleași mesaje JSON-RPC ca pe stdio).
- `test_stdio.py`: pornește serverul **ca proces separat** și vorbește cu el pe stdio, exact ca Claude Code.
- `test_auth.py`: tokeni, roluri, antete, rate limiter.

## Structura

| Fișier | Rol |
|---|---|
| `technova_mcp/server.py` | tool-uri, resurse, prompt; `guard` = auth → rate limit → execuție → audit |
| `technova_mcp/service.py` | logica de business și politicile, independente de MCP |
| `technova_mcp/auth.py` | `TokenStore`, `Principal`, permisiuni, `RateLimiter` |
| `technova_mcp/db.py` | schema SQLite și date demo deterministe |
| `technova_mcp/__main__.py` | `init`, `token add/list`, `serve [--http] [--read-only]` |

## Ce urmează

- [ ] PostgreSQL (aceeași schemă), cu un utilizator DB read-only pentru deployment-ul `--read-only`
- [ ] OAuth 2.1 în loc de tokeni statici, pentru HTTP (SDK-ul are deja `TokenVerifier`)
- [ ] Confirmare prin elicitation înainte de `update_order_status`
