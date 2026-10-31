# Browserless WAA Engine Refactoring — Agent Handoff Document

> **Timestamp**: 2026-10-31T14:20:00+08:00  
> **Current Branch**: `feat/browserless-waa-engine`  
> **Working Tree Status**: Clean (Commits: `e02b605`, `662d22c`)  
> **User Data Backup**: `/data/data/com.termux/files/home/aistudio_api_backup_20261031_134046.tar.gz`  
> **Verified Environment**: Android Termux (Linux aarch64), Python 3.14.8 (`uv`), Bun v1.4.4, Node.js v24.22.1

---

## 1. Objective & Directives

### Core Goal
Migrate the project completely from the heavy CloakBrowser + proot Chromium automation architecture to a pure, browserless WAA (Web Authentication / Attestation) challenge and request signing engine.

### Strict User Directives
1. **Dedicated Branch**: All work takes place on `feat/browserless-waa-engine`.
2. **No Compatibility Shims**: Legacy browser code (`CDPClient`, `BrowserEngine`, CloakBrowser proot scripts, DOM textarea scrapers) must be cleanly eliminated without maintaining dead shim layers.
3. **Data Safety**: User data has been completely backed up to `$HOME/aistudio_api_backup_20261031_134046.tar.gz` before any deletions.
4. **Reference Implementation Alignment**: Protocols must be strictly checked and verified against:
   - [`Mag1cFall/AIStudio2API`](https://github.com/Mag1cFall/AIStudio2API) (SpiderMonkey / Go WAA engine & wire protocols)
   - [`Lianues/aistudio-pg-bridge`](https://github.com/Lianues/aistudio-pg-bridge) (Node.js WAA VM proof server & gateway client)

---

## 2. Completed Work & Current Status

### 2.1 Complete Legacy Browser Purge (Commit `e02b605`)
- **Deleted packages/files**:
  - `src/aistudio_api/infrastructure/browser/` (entire directory: `browser_engine.py`, `cdp_client.py`, `scripts.py`, `js/`)
  - `src/aistudio_api/infrastructure/gateway/botguard_bootstrap.py` (DOM textarea extraction)
  - `scripts/cloakbrowser_termux/`, `scripts/setup-browser.sh`, `scripts/install-browser.bat`, `scripts/install-browser.ps1`
  - Obsolete browser test suites: `tests/unit/test_browser_engine.py`, `tests/unit/test_browser_scripts_syntax.py`, `tests/unit/test_cdp_client.py`
- **Result**: Net `-2,870` lines of legacy code and shell scripts removed.

### 2.2 WAA Engine Implementation (`src/aistudio_api/infrastructure/waa/`)
- [**`challenge.py`**](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/challenge.py):
  - Fetches WAA challenge from Google RPC (`https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create`).
  - Implements Caesar cipher shift decoding: `(b + 97) % 256` after Base64 decoding.
  - Downloads and caches interpreter scripts from Google CDN (`https://www.google.com/js/bg/{hash}.js`) with SHA-256 base64url validation.
- [**`signer.py`**](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/signer.py):
  - Implements [`RemoteProofSigner`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/signer.py#L60) connecting to remote/local proof servers (e.g. `https://waa.spinsnow.fun/v1/proof/mint`).
  - Generates content digests by space-joining user prompt contents and computing SHA-256 (matches `Mag1cFall` and `Lianues`).
  - Passes `cookies`, `authUser`, `model`, `prompt`, and serialized `wireContents` for 100% digest alignment.
- **Assets (`assets/`)**:
  - Vendored DOM emulation environment (`dom.js`, `smhost.js`), Firefox fingerprints (`firefox152.json.gz`), and WebGL state tables (`windows_webgl.json`).

### 2.3 Direct HTTP Gateway & Transport
- [**`transport.py`**](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/transport.py):
  - Rewritten as [`HTTPStreamTransport`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/transport.py#L32) utilizing `httpx.AsyncClient` with HTTP/2, connection pooling, and asynchronous byte chunk streaming (`resp.aiter_bytes()`).
- [**`session.py`**](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/session.py):
  - Completely decoupled from CDP. Manages in-memory auth cookies, instant account switching without browser page reloads, and calls `WaaSigner.generate_snapshot` directly.
- [**`cookie_parser.py`**](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/cookie_parser.py):
  - Implements `calculate_sapisid_hash` for `SAPISIDHASH`, `SAPISID1PHASH`, and `SAPISID3PHASH`.

### 2.4 Test Suite & Quality Status
- **Pytest**: `uv run pytest` -> **206 passed** in 3.81s (100% pass rate).
- **Linter**: `uv run ruff check .` -> **Clean** (All checks passed).
- **Type Checker**: `bun x pyright src tests` -> **0 errors, 0 warnings, 0 informations**.

---

## 3. Protocol Alignment & Cross-Verification Matrix

Detailed comparison between our implementation and the reference projects (`Mag1cFall/AIStudio2API` and `Lianues/aistudio-pg-bridge`):

| Protocol Specification | Reference Repo (`Mag1cFall` / `Lianues`) | Our Implementation (`aistudio-api`) | Status |
|---|---|---|---|
| **Upstream Endpoint** | `https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent` | [`session.py`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/session.py#L35) | ✅ Verified Exact |
| **Wire Index Layout** | `[model, contents, safety, genCfg, proof, sysInstruction, tools]` | [`wire_codec.py`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/wire_codec.py) (`SNAPSHOT_INDEX = 4`) | ✅ Verified Exact |
| **Proof Token Slot** | Wire Index 4 (5th field, starts with `!`) | `wire[4] = proof` in `wire_codec.py` | ✅ Verified Exact |
| **Digest Algorithm** | SHA-256 of space-joined (`" "`) text, inlineData base64, and file URIs | [`extract_content_digest`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/signer.py#L25) | ✅ Verified Exact |
| **WAA Proof Mint API** | `POST /v1/proof/mint` (`waa.spinsnow.fun`) | [`RemoteProofSigner.mint_proof`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/signer.py#L90) | ✅ Verified Exact |
| **Authorization Header** | `SAPISIDHASH <ts>_<sha1>` (+ 1P & 3P hashes) | [`cookie_parser.py:calculate_sapisid_hash`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/cookie_parser.py#L36) | ✅ Verified Exact |
| **Required Headers** | `x-user-agent: grpc-web-javascript/0.1`, `x-goog-ext-519733851-bin`, `origin`, `referer` | [`session.py:DEFAULT_BOOTSTRAP_TEMPLATE`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/session.py#L40) | ✅ Verified Exact |
| **Waa/Create Caesar Shift** | `(b + 97) % 256` after base64 decode | [`challenge.py:parse_challenge_payload`](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/waa/challenge.py#L42) | ✅ Verified Exact |

---

## 4. Key Architectural Files

```
src/aistudio_api/
├── domain/                    # Pure domain models (Content, Part, Message, Account)
├── infrastructure/
│   ├── gateway/
│   │   ├── session.py         # GatewaySession (browserless auth state & snapshot orchestration)
│   │   ├── transport.py       # HTTPStreamTransport (httpx AsyncClient HTTP/2 streaming)
│   │   ├── wire_codec.py      # Protobuf wire array encoder/decoder
│   │   ├── wire_parser.py     # Streaming chunk parser (SSE & grpc-web JSON frames)
│   │   ├── cookie_parser.py   # SAPISIDHASH generator and cookie parsing
│   │   └── session_auth.py    # Account auth file loader and storage sync
│   └── waa/
│       ├── challenge.py       # Google Waa/Create RPC & Caesar shift decoder
│       ├── signer.py          # RemoteProofSigner & WaaSigner facade
│       └── assets/            # SpiderMonkey/DOM/WebGL signatures for WAA challenge
└── interfaces/api/            # FastAPI HTTP routes (/v1/chat/completions, /v1beta/models, etc.)
```

---

## 5. Next Steps for Incoming Agent

### Priority 1: Double Check `wire_codec.py` Optional Fields
Verify that when `system_instruction` and `tools` are passed:
- `wire[0]` = model
- `wire[1]` = contents
- `wire[2]` = safety settings
- `wire[3]` = generation config
- `wire[4]` = WAA proof token (`!xxx`)
- `wire[5]` = system instruction (or `None` if tools present)
- `wire[6]` = tools array (if tools present)
Check [wire_codec.py](file:///data/data/com.termux/files/home/aistudio-api/src/aistudio_api/infrastructure/gateway/wire_codec.py) to ensure wire array bounds and padding match Lianues's `buildGenerateWireBody`.

### Priority 2: Cookie Refresh on Upstream Response
`Lianues` captures Google `Set-Cookie` in the upstream response headers and updates the stored cookies in real-time, preventing session decay. Check `transport.py` or `session.py` to ensure `Set-Cookie` headers from Google `GenerateContent` are captured and persisted back to the active account file if present.

### Priority 3: Live End-to-End Test (Optional)
If a valid Google account is available in `~/.config/aistudio-api/accounts/` or the backup archive:
- Test a live query against `GenerateContent` to verify real Google response streaming.

### Priority 4: Documentation Update
- Update `README.md` to remove old references to CloakBrowser, proot, and Chromium flags.
- Document `waa.proof_server_url` in `README.md`.

---

## 6. Verification Commands

```bash
# Run pytest suite
uv run pytest

# Check code formatting & lint
uv run ruff check .

# Type checking
bun x pyright src tests
```
