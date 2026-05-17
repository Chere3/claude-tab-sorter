# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Chrome/Chromium MV3 extension that groups browser tabs by topic. It has two classification paths:

1. **Local (auto mode)** — an offscreen document runs a fine-tuned MiniLM ONNX model via `@huggingface/transformers` (transformers.js) and classifies each new tab by cosine similarity to per-category prototype vectors. Used by the `chrome.tabs.onUpdated` listener in `background.js` for incremental, on-the-fly grouping.
2. **Cloud (button)** — the popup batches all current tabs, the service worker forwards them through Native Messaging to a local Node host (`native-host/host.js`), and the host calls the Claude Agent SDK (`query()` from `@anthropic-ai/claude-agent-sdk`) with a JSON-schema-constrained output. The SDK reuses the user's `claude /login` OAuth session — there is no `ANTHROPIC_API_KEY`.

Chrome extensions cannot load npm packages or spawn binaries, which is why the Agent SDK lives in a separately registered Native Messaging host process.

## Architecture map

```
popup.js  ─┐                                     ┌─► offscreen.js  ─►  transformers.js + ONNX
           ├─► background.js (service worker) ──►┤   (local embeddings, cosine vs PROTOTYPES)
auto via   │                                     │
onUpdated ─┘                                     └─► chrome.runtime.connectNative("com.diego.tabsorter")
                                                          │
                                                          ▼
                                                    native-host/host.js  (Node ESM)
                                                          │  framed stdio (uint32 LE length + JSON body)
                                                          ▼
                                                    @anthropic-ai/claude-agent-sdk query()
                                                          │  bundled `claude` binary, OAuth session
                                                          ▼
                                                    {groups:[{name,color,tabIds}]}
```

Key behavioural details that aren't obvious from any single file:

- **Native messaging framing.** Both `background.js` ↔ `host.js` and the manual test command rely on the Chrome native messaging framing: 4-byte little-endian length header followed by a UTF-8 JSON body. `host.js` implements its own framed reader/writer over stdin/stdout; do not `console.log` from the host — anything on stdout corrupts the protocol. Use the `log()` helper which writes to `~/.claude-tab-sorter.log`.
- **Request multiplexing.** The service worker assigns a monotonic `requestId` per call and maps responses back via `pending` map. `host.js` serialises handler execution with a `Promise` chain so SDK queries don't interleave but reads keep draining. Preserve `requestId` round-tripping when adding new message types.
- **Offscreen document lifecycle.** transformers.js cannot run in an MV3 service worker (no DOM, no WASM threads in some paths), so `background.js` creates an `OFFSCREEN_DOCUMENT` (`offscreen.html` → `offscreen.js`) with reason `WORKERS` and pings it before sending classify batches. The offscreen doc is the single owner of the model + prototype vectors.
- **Prototype caching.** `PROTOTYPES` (in `extension/prototypes.js`) is the seed corpus. On first run, `offscreen.js` embeds each example, mean-normalises per category, and caches the resulting vectors in `chrome.storage.local` under `prototypes_v${PROTO_VERSION}`. Bumping `PROTO_VERSION` invalidates the cache and forces recompute. The `reset-prototypes` offscreen message clears just the cache.
- **Auto-classify pipeline.** `chrome.tabs.onUpdated` only fires the queue when `status === "complete"`, `auto` is enabled in storage, and the tab's host changed since last classify (`lastHost` map). Updates debounce via `DEBOUNCE_MS` (600 ms) and batch per `windowId` before calling `classifyLocal`. Below `SIM_THRESHOLD` (0.65) the tab is left ungrouped.
- **Cloud mode constraints.** `host.js` calls `query()` with `allowedTools: []`, `maxTurns: 1`, `settingSources: []` and a strict JSON schema (`BATCH_SCHEMA` / `INCREMENTAL_SCHEMA`). Group colors are restricted to Chrome's tabGroup palette (see `COLORS` / `ALLOWED_COLORS`); anything else falls back to `"grey"`. The SDK's `message.structured_output` is preferred; `extractJson` is the fallback path.
- **Two prompt shapes.** `categorize` builds groups from scratch; `classify-incremental` reuses existing group names verbatim ("usa EXACTAMENTE su nombre"). The incremental path is wired in `host.js` but is currently only used by tests/manual invocations — the auto pipeline uses the local embedder, not the SDK.
- **Extension ID couples to native host manifest.** `install.sh` writes `~/Library/Application Support/<Browser>/NativeMessagingHosts/com.diego.tabsorter.json` with `allowed_origins: ["chrome-extension://<EXT_ID>/"]`. Reloading the unpacked extension typically changes the ID and breaks the connection until you re-run `install.sh <NEW_ID>`.

## Working in the repo

### Native host
```bash
cd native-host && npm install         # installs @anthropic-ai/claude-agent-sdk (incl. bundled claude binary)
```
The host wrapper (`native-host/host.sh`) prepends `/opt/homebrew/bin` and `/usr/local/bin` to `PATH` because Chrome on macOS launches native hosts without the user's interactive PATH. If `node` lives elsewhere, edit `host.sh`.

### Extension bundle
The offscreen doc imports `lib/transformers.bundle.js`, which is the prebuilt esbuild output of `src/offscreen-entry.js`. Rebuild only if you change the transformers.js entry:
```bash
cd extension && npm install && npm run build
```
The other files under `extension/lib/` (`ort-wasm-simd-threaded.jsep.{mjs,wasm}`) are the ONNX Runtime web WASM artefacts; `offscreen.js` points transformers.js at them via `env.backends.onnx.wasm.wasmPaths`. The model lives at `extension/models/tab-classifier-v1/` and is loaded with `env.allowLocalModels=true` + `env.allowRemoteModels=false`, so the model name in `MODEL_NAME` must match the folder name exactly.

### Installing into the browser
```bash
./install.sh <EXTENSION_ID>          # chrome (default)
./install.sh <EXTENSION_ID> brave    # brave | edge | arc | chromium also supported
```
Restart the browser fully (quit, not just close windows) so it re-reads the native host registry. Logs:
- Native host: `~/.claude-tab-sorter.log`
- Service worker: `chrome://extensions` → "Inspect views: service worker"
- Popup: right-click the toolbar icon → "Inspect popup"
- Offscreen: `chrome://extensions` → "Inspect views: offscreen.html"

### Manual host test (no extension required)
See README.md for the exact `node -e '…'` pipe that frames a `categorize` message into `host.js` and unframes the response. Useful for checking the SDK + OAuth path in isolation.

### Fine-tuning the local classifier
`finetune/` is a self-contained Python project (uses its own `.venv`). The pipeline:
```bash
cd finetune
source .venv/bin/activate            # or recreate with `python -m venv .venv && pip install -r ...`
python train.py                      # fine-tunes MiniLM with MNRL on the labelled corpus in dataset.py
python export.py                     # extracts encoder → ONNX → int8 quantize → copies into extension/models/tab-classifier-v1/
```
`train.py` prints accuracy against a held-out probe set; `export.py` writes the transformers.js layout (`config.json`, `tokenizer*.json`, `vocab.txt`, `onnx/model_quantized.onnx`). After re-exporting, reload the extension; you may also want to bump `PROTO_VERSION` in `extension/prototypes.js` so the cached prototype vectors are recomputed against the new embedder.

## Conventions worth keeping

- **Language.** User-visible strings (popup, prompts) are in Spanish; code identifiers and comments are in English. Match the surrounding style.
- **Colors.** Whenever you accept a color from the model or storage, validate it against the 9-value `ALLOWED_COLORS` / `COLORS` list and fall back to `"grey"`. The Chrome `tabGroups` API rejects anything else.
- **Storage shape.** `chrome.storage.local` keys in use: `auto` (bool), `model` (`haiku|sonnet|opus`), `scope` (`currentWindow|all`), `modelLoad` (offscreen progress object), `prototypes_v<N>` (cached embeddings), `stats` (`{ total, byCategory, runs, lastRun, bySource }`). The offscreen doc reads/writes via `chrome.runtime.sendMessage({target:"background",type:"storage-get|set"})` rather than touching `chrome.storage` directly, because offscreen contexts have limited storage access in some Chrome builds.
- **Stats accounting.** Group titles produced by either path are stored as `"<emoji> <name>"` (e.g. `💻 Desarrollo`). Local prototypes carry their emoji in `PROTOTYPES[cat].emoji`; the cloud prompts instruct Claude to prefix names with an emoji. Both paths funnel into `bumpStats(byCategoryMap, source)` in `background.js` — the popup posts a `bump-stats` message after manual runs, the auto pipeline calls it directly inside `applyAssignments` once per moved/created tab. A tab is counted exactly when its group title changes (creation or move), so re-running with everything already grouped does **not** inflate the counter.
