# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation

A Chrome/Chromium MV3 extension that groups open tabs into named tab groups. Two independent classifiers produce the same output shape (`title → "<emoji> <Category>" + color`) through completely different machinery:

| | Local path | Cloud path |
|---|---|---|
| Trigger | `chrome.tabs.onUpdated` (auto toggle) | popup button, manual |
| Classifier | fine-tuned MiniLM ONNX, cosine vs. category centroids | Claude Agent SDK `query()` |
| Runs in | offscreen document | external Node process |
| Categories | fixed 11, from `extension/prototypes.js` | free-form, model invents them |
| Cost / network | none | consumes the user's Claude subscription |

The unusual shape of this repo follows from two hard platform constraints: MV3 service workers cannot run transformers.js (hence an offscreen document), and extensions cannot load npm packages or spawn binaries (hence the Agent SDK lives behind Native Messaging). Nearly every awkward indirection below traces back to one of those two.

There is **no test runner and no linter**. The only end-to-end check is the manual framed-stdio pipe in README.md. Verify changes by loading the unpacked extension and reading the four consoles listed under *Debugging*.

## Execution contexts

Five contexts, each with different capabilities. Getting these wrong is the most common source of silent breakage.

| Context | File | Module type | Can it touch `chrome.storage`? | Notes |
|---|---|---|---|---|
| Service worker | `background.js` | classic | yes | sole owner of the native port + dataset writes; dies and restarts freely, so all state that must survive lives in storage |
| Offscreen doc | `offscreen.js` | **ESM** | **no — proxies via background** | sole owner of the model + prototype vectors |
| Popup | `popup.js` | **classic** | yes | cannot `import`; this is why it can't reuse `prototypes.js` and duplicates `ALLOWED_COLORS` |
| Dataset viewer | `dataset.js` | **ESM** | reads directly, writes via background | imports `prototypes.js`, so `dataset.html` must keep `type="module"` |
| Native host | `native-host/host.js` | Node ESM | n/a | **never write to stdout** — it is the wire protocol |

In-memory state in `background.js` (`lastHost`, `recentProgrammatic`, `pending`, `tabQueue`) is lost on service-worker eviction. That is tolerated by design; do not "fix" it by promoting it to storage without thinking through the write amplification described below.

## The message bus

Everything crosses a context boundary as a message. Adding a feature almost always means adding a case here.

**Popup / offscreen → background** (`chrome.runtime.onMessage`, `background.js:418`):
`host-request` (proxies to native host), `warmup-local`, `mark-programmatic`, `bump-stats`, `reset-stats`, `log-events`, `dataset-count`, `clear-dataset`, `relabel-entry`, `delete-entry`, and `storage-get`/`storage-set` (guarded by `target: "background"`, used only by the offscreen doc).

**Background → offscreen** (guarded by `target: "offscreen"`, `offscreen.js:170`):
`ping`, `warmup`, `classify-batch`, `reset-prototypes`.

**Background ↔ native host** (framed stdio):
`ping`, `categorize`, `classify-incremental`.

Two protocol rules that are load-bearing:

- **`requestId` round-tripping.** `sendToHost` (`background.js:37`) assigns a monotonic id and resolves the matching entry in `pending`; `host.js` echoes it back on every reply, including errors. A handler that drops `requestId` leaks a pending promise until `REQUEST_TIMEOUT_MS` (120 s).
- **Native messaging framing.** 4-byte little-endian length header, then a UTF-8 JSON body. `host.js` implements its own reader/writer (`host.js:20-77`) because it also has to serve the manual test pipe. Any stray `console.log` in the host corrupts the stream — use `log()`, which appends to `~/.claude-tab-sorter.log`.

## Invariants you will break by accident

These are the non-obvious rules. Each one has already been encoded somewhere and is easy to violate from a new code path.

1. **Any code that calls `chrome.tabs.group()` must first call `markProgrammatic([tabIds])`.** A second `onUpdated` listener (`background.js:409`) watches `changeInfo.groupId` to detect the user manually dragging a tab into a group, and treats that destination as ground-truth training data. The only thing separating "user corrected us" from "we moved it ourselves" is the in-memory `recentProgrammatic` map with a 4 s TTL. `applyAssignments` marks inline; the popup posts `mark-programmatic` and awaits it before grouping (`popup.js:150`). Miss this and you silently poison the dataset with fabricated user corrections.

2. **Group titles are the join key.** There is no group id persisted anywhere. `applyAssignments` matches existing groups by exact title string (`background.js:300`), stats accumulate keyed by title, and the incremental cloud prompt tells the model to reuse names verbatim. Titles are always `"<emoji> <Name>"` — the local path builds them in `displayName()` (`offscreen.js:142`), the cloud path relies on the prompt asking for an emoji prefix. Changing the emoji of a category orphans all prior stats for it.

3. **Colors must be validated at every boundary.** `chrome.tabGroups` rejects anything outside the 9-value palette. The list is duplicated in three places — `background.js:2`, `popup.js:1`, `host.js:79` — and all three must stay in sync. Always fall back to `"grey"` (British spelling).

4. **`PROTO_VERSION` is the cache-busting mechanism for embeddings.** Prototype centroids are computed once, then cached in `chrome.storage.local` under `prototypes_v${PROTO_VERSION}` (`offscreen.js:76`). Any change to the model weights *or* to `PROTOTYPES` examples requires bumping `PROTO_VERSION` in `extension/prototypes.js`, otherwise stale vectors from the previous embedder are silently reused.

5. **Two corpora define the categories and they are not the same file.** `finetune/dataset.py` (~529 examples) trains the embedder; `extension/prototypes.js` (11 categories × ~15–20 examples, each carrying `color` + `emoji`) builds the runtime centroids. Adding a category means editing both, then retraining, re-exporting, and bumping `PROTO_VERSION`.

6. **The extension id is baked into the native host manifest.** `install.sh` writes `allowed_origins: ["chrome-extension://<EXT_ID>/"]`. Loading the unpacked extension from a different folder changes the id and silently breaks the cloud path until `install.sh <NEW_ID>` is re-run and the browser is fully quit and relaunched.

7. **Dataset entries are addressed by array index.** The viewer stamps `_i` from the raw array position (`dataset.js:38`) and `relabel-entry` / `delete-entry` mutate `dataset[i]` by that index (`background.js:490`). Anything that reorders or shifts the array invalidates open viewer state — including the `splice(0, …)` rotation at `DATASET_MAX_ENTRIES` (`background.js:159`). Do not introduce new mutations that shift indices without switching to stable ids.

## Known defects (verified, not yet fixed)

Do not "document around" these — they are real, and prior versions of this file described some of them incorrectly.

- **The JSON schemas are dead code.** `BATCH_SCHEMA` and `INCREMENTAL_SCHEMA` (`host.js:132`, `host.js:154`) are passed as `schema` into `runQuery`, which destructures the parameter and never forwards it to `query()`'s options (`host.js:188-204`). Nothing enforces them. Consequently `message.structured_output` is normally absent and the real parse path is `extractJson(resultText)` — a regex that grabs the first `{…}` block. If you wire the schemas in, verify against the installed SDK version before claiming validation works.

- **`stats.bySource` and `dataset[].source` use different vocabularies for the same event.** The cloud run records `source: "claude"` into the dataset (`popup.js:177`) but reports `source: "popup"` to `bumpStats` (`popup.js:184`), and the popup reads back `stats.bySource.popup` (`popup.js:321`). So `bySource` keys are `auto`/`popup`, while dataset keys are `auto`/`claude`/`manual`. Normalising one side without the other blanks out the popup's local-vs-Claude split.

- **The two paths disagree on which tabs are eligible.** `isClassifiable` (`background.js:165`) requires `http(s)` and excludes pinned and incognito tabs. `isCategorizable` (`popup.js:30`) accepts `https?|file|ftp` and excludes neither. The cloud path therefore groups tabs the auto path would never touch, and can ship `file://` URLs to the model.

- **`stats.runs` conflates two meanings.** `bumpStats` increments it once per call (`background.js:339`), which is once per user-initiated cloud run but also once per debounced auto batch — so the popup's "ejecuciones" counter grows on its own during normal browsing.

- **Every dataset write is a full read-modify-write of the whole array.** `logEvents`, `relabel-entry`, and `delete-entry` each load, mutate, and re-serialise up to `DATASET_MAX_ENTRIES = 50000` entries. This is why `unlimitedStorage` is declared, but it also means dataset writes get slower linearly with usage.

- **The viewer's `dataset.py` export labels categories with emoji; `finetune/dataset.py` does not.** The export emits `CATEGORIES = ["💻 Desarrollo", …]` (`dataset.js:266` uses the display label), while the checked-in corpus uses bare `"Desarrollo"` (`finetune/dataset.py:7`) and `prototypes.js` keeps the emoji in a separate field. The export is structurally drop-in — it provides `CATEGORIES`, `LABEL2ID`, `DATASET`, `build_inputs()`, which is exactly what `train.py:16` and `evaluate.py:26` import — but the label strings do not round-trip into `prototypes.js` keys. It also does not enforce a minimum example count per category, and `train.py` samples 4 positives per example, so a thin category degenerates.

## Pipelines

**Auto (local).** `onUpdated` fires only when `status === "complete"`, the tab is classifiable, `auto` is on in storage, and the tab's host differs from `lastHost` for that tab id (`background.js:190` — this is what stops SPA navigations from re-classifying forever). Matching tabs queue, debounce `DEBOUNCE_MS` (600 ms), batch by `windowId`, then `ensureOffscreen()` (create + ping up to 20 × 250 ms) and `classify-batch`. Below `SIM_THRESHOLD` (0.65, `offscreen.js:11`) `category` is `null` and the tab is left alone — but `fallbackCategory` still carries the argmax, and that is what the dataset records, which is what makes low-confidence cases reviewable later.

**Cloud.** The popup collects tabs for the chosen scope, posts `host-request`, and the service worker forwards over the port. `host.js` serialises handlers through a promise chain (`host.js:260`) so SDK queries never interleave while stdin keeps draining. `query()` runs with `allowedTools: []`, `maxTurns: 1`, `settingSources: []` and a system prompt demanding bare JSON. The SDK reuses the user's `claude /login` OAuth session — there is no `ANTHROPIC_API_KEY` anywhere in this repo, and there should not be one.

Two prompt shapes exist: `categorize` (build groups from scratch) and `classify-incremental` (reuse existing group names verbatim). The incremental handler is fully wired but **currently unreachable from the UI** — the auto path uses the local embedder instead. It is reachable from the manual test pipe.

**Dataset capture (opt-in, off by default).** When `datasetEnabled` is set, all three sources append to `chrome.storage.local.dataset` via `log-events`. Entry shape: `{ ts, title, url, host, category, fallbackCategory, similarity, color, source: "auto"|"claude"|"manual", userCategory, confirmedAt?, manualMove? }`. `url` is truncated to host + 80 chars of path, `title` to 200 chars. On a manual group change, `handleManualGroupChange` scans the last 500 entries for an unconfirmed prediction with the same URL and upgrades it into a labelled pair (`userCategory` + `manualMove`); failing that it logs a fresh `source: "manual"` event.

## Commands

```bash
# Extension bundle — only when the transformers.js glue in src/ changes.
cd extension && npm install && npm run build
```

`extension/lib/transformers.bundle.js` is a **committed build artifact** produced by esbuild from `src/offscreen-entry.js`. `offscreen.js` imports it directly, so editing the entry file without rebuilding has no effect. The ONNX Runtime WASM files sit alongside it in `lib/`, are pointed at by `env.backends.onnx.wasm.wasmPaths`, and are listed in `web_accessible_resources`.

```bash
# Native host.
cd native-host && npm install        # pulls @anthropic-ai/claude-agent-sdk + bundled claude binary
./install.sh <EXTENSION_ID>          # chrome (default) | brave | edge | arc | chromium
```

`host.sh` prepends `/opt/homebrew/bin` and `/usr/local/bin` to `PATH` because Chrome on macOS launches native hosts without an interactive PATH. If `node` lives elsewhere, edit that wrapper. `install.sh` is macOS-only (hardcoded `~/Library/Application Support/…` paths).

```bash
# Fine-tuning — self-contained Python project with its own .venv.
cd finetune && source .venv/bin/activate
python train.py      # MNRL fine-tune → output/finetuned + output/training_metrics.json
python export.py     # encoder → ONNX → int8 → ../extension/models/tab-classifier-v1/
python evaluate.py   # regenerates docs/*.png + docs/evaluation.json
```

Run these from inside `finetune/` — the paths are relative. After `export.py`, reload the extension **and** bump `PROTO_VERSION`.

```bash
# Re-etiquetado del corpus real y re-entrenamiento (finetune/relabel/).
python relabel/consolidate.py   # valida los 14 shards anotados → labeled.json + agreement.json + disagreement.json
python relabel/train_v2.py      # re-entrena y evalúa dos splits → output/metrics_v2.json
python relabel/report.py        # gráficas en docs/relabel/ + RELABEL_REPORT.md
python relabel/export_v2.py     # dataset_v2.py + prototypes_v2.js + models/tab-classifier-v2/
```

`finetune/relabel/` contiene un corpus de **navegación real** (2017 items únicos deduplicados de 4053 eventos exportados por el viewer) re-etiquetado por 14 anotadores LLM independientes trabajando en sectores por host. Puntos que condicionan cómo se lee cualquier métrica de aquí:

- **No hay ground truth humano.** El export original traía `userCategory: null` en el 100% de los eventos. Las etiquetas son consenso de anotadores, no verdad. Cualquier "accuracy" contra ellas mide concordancia con ese consenso; su credibilidad descansa en el Fleiss' κ reportado en `agreement.json`, medido sobre un gold set de 48 items que los 14 anotadores etiquetaron a ciegas.
- **La anotación fue ciega a propósito.** Los shards no incluyen `localCategory` ni `similarity`; mostrar la predicción de MiniLM ancla al anotador y subestima la tasa de error.
- **`train_v2.py` evalúa dos splits y ambos son necesarios.** El split por host (ningún dominio en train y test a la vez) mide generalización a sitios nuevos; el aleatorio mide rendimiento en sitios ya vistos, que es el uso real. `music.youtube.com` y `youtube.com` van forzados a train: juntos son el 38% del corpus y en test lo dominarían. Comparar contra el LOO de `train.py` (v1) no es válido — aquel se mide sobre el propio corpus de entrenamiento.
- **`export_v2.py` no despliega nada.** Escribe `dataset_v2.py`, `prototypes_v2.js` y `models/tab-classifier-v2/` en paralelo a los actuales; activar la v2 exige mover archivos, cambiar `MODEL_NAME` en `offscreen.js` y bumpear `PROTO_VERSION`, y es una decisión explícita.

**Debugging.** Native host → `~/.claude-tab-sorter.log`. Service worker, offscreen doc → `chrome://extensions` → "Inspect views". Popup → right-click the toolbar icon → "Inspect popup". The offscreen console is the only place local classification errors surface.

## Conventions

- **Language split.** User-facing strings (popup, dataset viewer, prompts sent to Claude) are Spanish; identifiers, comments, and log lines are English. Match whichever side you are editing.
- **Storage keys.** `auto`, `model` (`haiku|sonnet|opus`), `scope` (`currentWindow|all`), `modelLoad`, `prototypes_v<N>`, `stats`, `datasetEnabled`, `dataset`.
- **Error style.** Handlers reply `{ ok: true, … }` / `{ ok: false, error }` rather than throwing across a boundary; per-tab failures inside a batch are caught and logged so one bad tab cannot abort the batch (`background.js:322`).
