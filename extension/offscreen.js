import { pipeline, env } from "./lib/transformers.bundle.js";
import { PROTOTYPES, PROTO_VERSION } from "./prototypes.js";

env.allowLocalModels = true;
env.allowRemoteModels = false;
env.useBrowserCache = false;
env.localModelPath = chrome.runtime.getURL("models/");
env.backends.onnx.wasm.wasmPaths = chrome.runtime.getURL("lib/");

const MODEL_NAME = "tab-classifier-v1";
const SIM_THRESHOLD = 0.65;

let extractor = null;
let prototypeVectors = null;
let initPromise = null;

function log(...args) {
  console.log("[tab-sorter/offscreen]", ...args);
}

function err(...args) {
  console.error("[tab-sorter/offscreen]", ...args);
}

async function bgStorageGet(key) {
  try {
    const reply = await chrome.runtime.sendMessage({
      target: "background",
      type: "storage-get",
      key
    });
    return reply?.value;
  } catch (e) {
    err("storage-get failed", e?.message);
    return undefined;
  }
}

async function bgStorageSet(key, value) {
  try {
    await chrome.runtime.sendMessage({
      target: "background",
      type: "storage-set",
      key,
      value
    });
  } catch (e) {
    err("storage-set failed", e?.message);
  }
}

async function embed(text) {
  const out = await extractor(text, { pooling: "mean", normalize: true });
  return Array.from(out.data);
}

function meanNormalize(vectors) {
  const dim = vectors[0].length;
  const out = new Float32Array(dim);
  for (const v of vectors) for (let i = 0; i < dim; i++) out[i] += v[i];
  for (let i = 0; i < dim; i++) out[i] /= vectors.length;
  let norm = 0;
  for (const x of out) norm += x * x;
  norm = Math.sqrt(norm) || 1;
  for (let i = 0; i < dim; i++) out[i] /= norm;
  return Array.from(out);
}

function cosine(a, b) {
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot;
}

async function loadOrComputePrototypes() {
  const cacheKey = "prototypes_v" + PROTO_VERSION;
  const cached = await bgStorageGet(cacheKey);
  if (cached) {
    log("prototypes from cache");
    prototypeVectors = cached;
    return;
  }
  log("computing prototypes from scratch");
  prototypeVectors = {};
  for (const [cat, def] of Object.entries(PROTOTYPES)) {
    const embs = [];
    for (const ex of def.examples) {
      embs.push(await embed(ex));
    }
    prototypeVectors[cat] = {
      vector: meanNormalize(embs),
      color: def.color
    };
    log("proto", cat, def.examples.length, "examples");
  }
  await bgStorageSet(cacheKey, prototypeVectors);
}

async function init() {
  if (extractor) return;
  log("loading model", MODEL_NAME);
  extractor = await pipeline("feature-extraction", MODEL_NAME, {
    dtype: "q8",
    progress_callback: (p) => {
      if (p.status === "progress" && p.file) {
        log("dl", p.file, Math.round(p.progress || 0) + "%");
        bgStorageSet("modelLoad", {
          status: "downloading",
          file: p.file,
          progress: p.progress,
          loaded: p.loaded,
          total: p.total
        });
      }
    }
  });
  log("model loaded");
  await bgStorageSet("modelLoad", { status: "ready" });
  await loadOrComputePrototypes();
  log("ready");
}

function ensureInit() {
  if (!initPromise) initPromise = init().catch(async (e) => {
    err("init failed", e);
    await bgStorageSet("modelLoad", { status: "error", error: e?.message || String(e) });
    initPromise = null;
    throw e;
  });
  return initPromise;
}

function urlHostPath(u) {
  try {
    const url = new URL(u);
    return url.hostname.replace(/^www\./, "") + url.pathname.slice(0, 80);
  } catch {
    return "";
  }
}

function displayName(cat) {
  const emoji = PROTOTYPES[cat]?.emoji;
  return emoji ? `${emoji} ${cat}` : cat;
}

async function classifyOne(title, url) {
  await ensureInit();
  const text = `${title || ""} ${urlHostPath(url || "")}`.trim().slice(0, 256);
  if (!text) return { category: null, similarity: 0, color: "grey" };
  const v = await embed(text);
  let bestCat = null;
  let bestSim = -Infinity;
  for (const [cat, p] of Object.entries(prototypeVectors)) {
    const s = cosine(v, p.vector);
    if (s > bestSim) {
      bestSim = s;
      bestCat = cat;
    }
  }
  const color = prototypeVectors[bestCat]?.color || "grey";
  return {
    category: bestSim >= SIM_THRESHOLD ? displayName(bestCat) : null,
    fallbackCategory: displayName(bestCat),
    similarity: bestSim,
    color
  };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.target !== "offscreen") return;

  (async () => {
    try {
      if (msg.type === "ping") {
        sendResponse({ ok: true, ready: !!extractor });
        return;
      }
      if (msg.type === "warmup") {
        await ensureInit();
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "classify-batch") {
        await ensureInit();
        const results = [];
        for (const t of msg.tabs) {
          const r = await classifyOne(t.title, t.url);
          results.push({ tabId: t.id, ...r });
        }
        sendResponse({ ok: true, results });
        return;
      }
      if (msg.type === "reset-prototypes") {
        await chrome.storage.local.remove("prototypes_v" + PROTO_VERSION);
        prototypeVectors = null;
        initPromise = null;
        sendResponse({ ok: true });
        return;
      }
      sendResponse({ ok: false, error: "unknown type: " + msg.type });
    } catch (e) {
      err("handler error", e);
      sendResponse({ ok: false, error: e?.message || String(e) });
    }
  })();
  return true;
});

ensureInit().catch((e) => err("startup init failed", e?.message));
