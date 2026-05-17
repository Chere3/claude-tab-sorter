const HOST_NAME = "com.diego.tabsorter";
const ALLOWED_COLORS = [
  "grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"
];
const DEBOUNCE_MS = 600;
const REQUEST_TIMEOUT_MS = 120000;

let port = null;
const pending = new Map();
let nextRequestId = 1;

function ensurePort() {
  if (port) return port;
  port = chrome.runtime.connectNative(HOST_NAME);
  port.onMessage.addListener((msg) => {
    const reqId = msg.requestId;
    const r = pending.get(reqId);
    if (!r) return;
    pending.delete(reqId);
    clearTimeout(r.timer);
    if (msg.ok) r.resolve(msg);
    else r.reject(new Error(msg.error || "host error"));
  });
  port.onDisconnect.addListener(() => {
    const e = chrome.runtime.lastError;
    console.warn("[tab-sorter] port disconnected:", e?.message);
    port = null;
    for (const r of pending.values()) {
      clearTimeout(r.timer);
      r.reject(new Error("host disconnected: " + (e?.message || "")));
    }
    pending.clear();
  });
  return port;
}

function sendToHost(msg) {
  return new Promise((resolve, reject) => {
    const requestId = nextRequestId++;
    const timer = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error("host timeout"));
    }, REQUEST_TIMEOUT_MS);
    pending.set(requestId, { resolve, reject, timer });
    try {
      ensurePort().postMessage({ ...msg, requestId });
    } catch (e) {
      pending.delete(requestId);
      clearTimeout(timer);
      reject(e);
    }
  });
}

const OFFSCREEN_URL = "offscreen.html";

async function offscreenAlive() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"]
  });
  return contexts.length > 0;
}

async function pingOffscreen() {
  try {
    const reply = await Promise.race([
      chrome.runtime.sendMessage({ target: "offscreen", type: "ping" }),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("ping timeout")), 500)
      )
    ]);
    return !!reply?.ok;
  } catch {
    return false;
  }
}

async function ensureOffscreen() {
  if (!(await offscreenAlive())) {
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_URL,
        reasons: ["WORKERS"],
        justification: "Run local embedding model for tab classification"
      });
    } catch (e) {
      if (!/already an offscreen/i.test(e.message)) throw e;
    }
  }
  for (let i = 0; i < 20; i++) {
    if (await pingOffscreen()) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("offscreen did not become ready");
}

async function classifyLocal(tabs) {
  await ensureOffscreen();
  const reply = await chrome.runtime.sendMessage({
    target: "offscreen",
    type: "classify-batch",
    tabs
  });
  if (!reply?.ok) throw new Error(reply?.error || "offscreen error");
  return reply.results;
}

const tabQueue = new Map();
const inFlight = new Set();
const lastHost = new Map();
let flushTimer = null;

function urlHost(u) {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function shortUrl(u) {
  try {
    const url = new URL(u);
    return url.hostname.replace(/^www\./, "") + url.pathname.slice(0, 80);
  } catch {
    return (u || "").slice(0, 120);
  }
}

const DATASET_MAX_ENTRIES = 50000;
const PROGRAMMATIC_TTL_MS = 4000;
const recentProgrammatic = new Map();

function markProgrammatic(tabIds, ttl = PROGRAMMATIC_TTL_MS) {
  const exp = Date.now() + ttl;
  for (const id of tabIds) recentProgrammatic.set(id, exp);
}

function isProgrammatic(tabId) {
  const exp = recentProgrammatic.get(tabId);
  if (!exp) return false;
  if (exp < Date.now()) {
    recentProgrammatic.delete(tabId);
    return false;
  }
  return true;
}

async function datasetEnabled() {
  const { datasetEnabled } = await chrome.storage.local.get({ datasetEnabled: false });
  return !!datasetEnabled;
}

async function logEvents(events) {
  if (!events?.length) return;
  if (!(await datasetEnabled())) return;
  const { dataset } = await chrome.storage.local.get({ dataset: [] });
  for (const e of events) dataset.push(e);
  if (dataset.length > DATASET_MAX_ENTRIES) {
    dataset.splice(0, dataset.length - DATASET_MAX_ENTRIES);
  }
  await chrome.storage.local.set({ dataset });
}

function isClassifiable(tab) {
  if (!tab) return false;
  if (tab.pinned) return false;
  if (tab.incognito) return false;
  if (!tab.url) return false;
  return /^https?:/.test(tab.url);
}

async function autoEnabled() {
  const s = await chrome.storage.local.get({ auto: false });
  return !!s.auto;
}

function scheduleFlush() {
  if (flushTimer) clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, DEBOUNCE_MS);
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  if (!isClassifiable(tab)) return;
  if (inFlight.has(tabId)) return;
  if (!(await autoEnabled())) return;

  const host = urlHost(tab.url);
  if (host && lastHost.get(tabId) === host) return;

  tabQueue.set(tabId, {
    id: tab.id,
    title: tab.title || "(sin título)",
    url: tab.url,
    windowId: tab.windowId
  });
  scheduleFlush();
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabQueue.delete(tabId);
  inFlight.delete(tabId);
  lastHost.delete(tabId);
});

async function flush() {
  flushTimer = null;
  if (tabQueue.size === 0) return;

  const byWindow = new Map();
  for (const tab of tabQueue.values()) {
    if (!byWindow.has(tab.windowId)) byWindow.set(tab.windowId, []);
    byWindow.get(tab.windowId).push(tab);
    inFlight.add(tab.id);
  }
  tabQueue.clear();

  for (const [windowId, tabs] of byWindow) {
    try {
      await autoClassifyAndApply(tabs, windowId);
    } catch (e) {
      console.error("[tab-sorter] auto classify failed", e);
    } finally {
      for (const t of tabs) inFlight.delete(t.id);
    }
  }
}

async function autoClassifyAndApply(tabs, windowId) {
  const live = [];
  for (const t of tabs) {
    try {
      const cur = await chrome.tabs.get(t.id);
      if (cur && isClassifiable(cur)) {
        live.push({
          id: cur.id,
          title: cur.title || "(sin título)",
          url: cur.url,
          windowId: cur.windowId,
          currentGroupId: cur.groupId
        });
      }
    } catch {}
  }
  if (!live.length) return;

  const results = await classifyLocal(live);
  console.log("[tab-sorter] local results", results);

  const events = [];
  const now = Date.now();
  for (const r of results) {
    const t = live.find((x) => x.id === r.tabId);
    if (!t) continue;
    lastHost.set(t.id, urlHost(t.url));
    events.push({
      ts: now,
      title: (t.title || "").slice(0, 200),
      url: shortUrl(t.url),
      host: urlHost(t.url),
      category: r.category,
      fallbackCategory: r.fallbackCategory,
      similarity: r.similarity,
      color: r.color,
      source: "auto",
      userCategory: null
    });
  }
  await logEvents(events);

  const tabById = new Map(live.map((t) => [t.id, t]));
  const assignments = results
    .filter((r) => r.category)
    .map((r) => ({
      tabId: r.tabId,
      group: r.category,
      isNew: true,
      color: r.color,
      currentGroupId: tabById.get(r.tabId)?.currentGroupId ?? -1
    }));

  await applyAssignments(assignments, windowId);
}

async function applyAssignments(assignments, windowId) {
  const groupCache = new Map();
  const groupsById = new Map();
  const groups = await chrome.tabGroups.query({ windowId });
  for (const g of groups) {
    groupCache.set(g.title, g);
    groupsById.set(g.id, g);
  }

  const movedByCategory = new Map();

  for (const a of assignments) {
    try {
      const targetName = a.group;
      const existing = groupCache.get(targetName);
      const currentGroup = a.currentGroupId !== -1 ? groupsById.get(a.currentGroupId) : null;

      if (currentGroup && currentGroup.title === targetName) {
        continue;
      }

      markProgrammatic([a.tabId]);
      if (existing) {
        if (a.currentGroupId === existing.id) continue;
        await chrome.tabs.group({ tabIds: a.tabId, groupId: existing.id });
      } else {
        const groupId = await chrome.tabs.group({
          tabIds: a.tabId,
          createProperties: { windowId }
        });
        const color = ALLOWED_COLORS.includes(a.color) ? a.color : "grey";
        await chrome.tabGroups.update(groupId, { title: targetName, color });
        groupCache.set(targetName, { id: groupId, title: targetName, color });
        groupsById.set(groupId, { id: groupId, title: targetName, color });
      }
      movedByCategory.set(targetName, (movedByCategory.get(targetName) || 0) + 1);
    } catch (e) {
      console.error("[tab-sorter] apply failed", a, e);
    }
  }

  if (movedByCategory.size) {
    await bumpStats(movedByCategory, "auto");
  }
}

async function bumpStats(byCategoryMap, source) {
  const total = Array.from(byCategoryMap.values()).reduce((a, b) => a + b, 0);
  if (!total) return;
  const { stats } = await chrome.storage.local.get({
    stats: { total: 0, byCategory: {}, lastRun: null, runs: 0, bySource: {} }
  });
  stats.total += total;
  stats.runs += 1;
  stats.lastRun = Date.now();
  stats.bySource = stats.bySource || {};
  stats.bySource[source] = (stats.bySource[source] || 0) + total;
  for (const [cat, n] of byCategoryMap) {
    stats.byCategory[cat] = (stats.byCategory[cat] || 0) + n;
  }
  await chrome.storage.local.set({ stats });
}

async function handleManualGroupChange(tabId, newGroupId, tab) {
  if (!(await datasetEnabled())) return;
  let group;
  try {
    group = await chrome.tabGroups.get(newGroupId);
  } catch {
    return;
  }
  const groupTitle = (group?.title || "").trim();
  if (!groupTitle) return;

  let liveTab = tab;
  if (!liveTab || !liveTab.url) {
    try {
      liveTab = await chrome.tabs.get(tabId);
    } catch {
      return;
    }
  }
  if (!liveTab?.url || !/^https?:/.test(liveTab.url)) return;

  const url = shortUrl(liveTab.url);
  const host = urlHost(liveTab.url);
  const { dataset } = await chrome.storage.local.get({ dataset: [] });
  const now = Date.now();

  let matchedIndex = -1;
  const tail = Math.max(0, dataset.length - 500);
  for (let i = dataset.length - 1; i >= tail; i--) {
    const e = dataset[i];
    if (e.url === url && e.source !== "manual" && !e.userCategory) {
      matchedIndex = i;
      break;
    }
  }

  if (matchedIndex >= 0) {
    dataset[matchedIndex].userCategory = groupTitle;
    dataset[matchedIndex].confirmedAt = now;
    dataset[matchedIndex].manualMove = true;
    await chrome.storage.local.set({ dataset });
  } else {
    await logEvents([
      {
        ts: now,
        title: (liveTab.title || "").slice(0, 200),
        url,
        host,
        category: null,
        fallbackCategory: null,
        similarity: null,
        color: group.color || null,
        source: "manual",
        userCategory: groupTitle,
        confirmedAt: now
      }
    ]);
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!("groupId" in changeInfo)) return;
  if (changeInfo.groupId === -1) return; // ungrouped — ignore
  if (isProgrammatic(tabId)) return;
  handleManualGroupChange(tabId, changeInfo.groupId, tab).catch((e) =>
    console.error("[tab-sorter] manual-move log failed", e)
  );
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "mark-programmatic") {
    markProgrammatic(msg.tabIds || []);
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.type === "host-request") {
    sendToHost(msg.payload).then(
      (response) => sendResponse({ ok: true, response }),
      (err) => sendResponse({ ok: false, error: err.message })
    );
    return true;
  }
  if (msg?.type === "warmup-local") {
    ensureOffscreen()
      .then(() => chrome.runtime.sendMessage({ target: "offscreen", type: "warmup" }))
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (msg?.target === "background" && msg?.type === "storage-get") {
    chrome.storage.local.get(msg.key).then(
      (obj) => sendResponse({ ok: true, value: obj[msg.key] }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.target === "background" && msg?.type === "storage-set") {
    chrome.storage.local.set({ [msg.key]: msg.value }).then(
      () => sendResponse({ ok: true }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.type === "bump-stats") {
    const map = new Map(Object.entries(msg.byCategory || {}));
    bumpStats(map, msg.source || "popup").then(
      () => sendResponse({ ok: true }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.type === "reset-stats") {
    chrome.storage.local
      .set({ stats: { total: 0, byCategory: {}, lastRun: null, runs: 0, bySource: {} } })
      .then(
        () => sendResponse({ ok: true }),
        (e) => sendResponse({ ok: false, error: e.message })
      );
    return true;
  }
  if (msg?.type === "log-events") {
    logEvents(msg.events || []).then(
      () => sendResponse({ ok: true }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.type === "dataset-count") {
    chrome.storage.local.get({ dataset: [] }).then(
      ({ dataset }) => sendResponse({ ok: true, count: dataset.length }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.type === "clear-dataset") {
    chrome.storage.local.set({ dataset: [] }).then(
      () => sendResponse({ ok: true }),
      (e) => sendResponse({ ok: false, error: e.message })
    );
    return true;
  }
  if (msg?.type === "relabel-entry") {
    chrome.storage.local.get({ dataset: [] }).then(({ dataset }) => {
      const i = Number(msg.index);
      if (i >= 0 && i < dataset.length) {
        dataset[i].userCategory = msg.userCategory || null;
        dataset[i].confirmedAt = Date.now();
      }
      chrome.storage.local.set({ dataset }).then(
        () => sendResponse({ ok: true }),
        (e) => sendResponse({ ok: false, error: e.message })
      );
    });
    return true;
  }
  if (msg?.type === "delete-entry") {
    chrome.storage.local.get({ dataset: [] }).then(({ dataset }) => {
      const i = Number(msg.index);
      if (i >= 0 && i < dataset.length) dataset.splice(i, 1);
      chrome.storage.local.set({ dataset }).then(
        () => sendResponse({ ok: true }),
        (e) => sendResponse({ ok: false, error: e.message })
      );
    });
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  console.log("[tab-sorter] installed");
  ensureOffscreen().catch((e) => console.error("offscreen init failed", e));
});

chrome.runtime.onStartup.addListener(() => {
  ensureOffscreen().catch((e) => console.error("offscreen init failed", e));
});
