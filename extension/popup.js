const ALLOWED_COLORS = [
  "grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"
];

function callHost(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      { type: "host-request", payload },
      (reply) => {
        if (chrome.runtime.lastError) {
          return reject(new Error(chrome.runtime.lastError.message));
        }
        if (!reply) return reject(new Error("no reply from background"));
        if (!reply.ok) return reject(new Error(reply.error || "host error"));
        resolve(reply.response);
      }
    );
  });
}

const $ = (id) => document.getElementById(id);

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.classList.remove("error", "ok");
  if (kind) el.classList.add(kind);
}

function isCategorizable(tab) {
  if (!tab.url) return false;
  return /^(https?|file|ftp):/.test(tab.url);
}

async function getTabs() {
  const scope = $("scope").value;
  const query = scope === "all" ? {} : { currentWindow: true };
  const tabs = await chrome.tabs.query(query);
  return tabs.filter(isCategorizable);
}

async function updateTabCount() {
  const tabs = await getTabs();
  $("tab-count").textContent = `${tabs.length} tabs`;
}

function bumpStats(byCategory, source = "popup") {
  if (!byCategory || !Object.keys(byCategory).length) return Promise.resolve();
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "bump-stats", byCategory, source },
      () => resolve()
    );
  });
}

async function categorize() {
  const button = $("categorize");
  button.disabled = true;
  $("groups").innerHTML = "";
  $("results-section").hidden = true;

  try {
    const tabs = await getTabs();
    if (!tabs.length) {
      setStatus("No hay pestañas categorizables.", "error");
      return;
    }
    setStatus(`Leyendo ${tabs.length} pestañas…`);

    const payload = tabs.map((t) => ({
      id: t.id,
      title: t.title || "(sin título)",
      url: t.url,
      windowId: t.windowId
    }));

    setStatus(`Consultando Claude (${payload.length} tabs)…`);

    let response;
    try {
      response = await callHost({
        type: "categorize",
        model: $("model").value,
        tabs: payload
      });
    } catch (e) {
      setStatus("Error: " + e.message, "error");
      return;
    }

    const groups = response.result?.groups || [];
    setStatus(`Aplicando ${groups.length} grupos…`);

    const created = [];
    const byCategory = {};

    for (const g of groups) {
      const validIds = (g.tabIds || []).filter((id) =>
        tabs.some((t) => t.id === id)
      );
      if (!validIds.length) continue;

      const byWindow = new Map();
      for (const id of validIds) {
        const t = tabs.find((x) => x.id === id);
        if (!byWindow.has(t.windowId)) byWindow.set(t.windowId, []);
        byWindow.get(t.windowId).push(id);
      }

      const color = ALLOWED_COLORS.includes(g.color) ? g.color : "grey";
      const name = g.name || "Grupo";

      for (const [winId, ids] of byWindow) {
        const groupId = await chrome.tabs.group({
          tabIds: ids,
          createProperties: { windowId: winId }
        });
        await chrome.tabGroups.update(groupId, {
          title: name,
          color,
          collapsed: false
        });
        created.push({ name, count: ids.length, color });
        byCategory[name] = (byCategory[name] || 0) + ids.length;
      }
    }

    await bumpStats(byCategory, "popup");

    renderGroups(created);
    setStatus(`Listo. ${created.length} grupos · ${Object.values(byCategory).reduce((a, b) => a + b, 0)} pestañas.`, "ok");
    renderStats();
  } catch (e) {
    const msg = e?.message || String(e);
    console.error("[tab-sorter] error:", e);
    setStatus("Error: " + msg, "error");
  } finally {
    button.disabled = false;
  }
}

function renderGroups(groups) {
  const ol = $("groups");
  ol.innerHTML = "";
  if (!groups.length) {
    $("results-section").hidden = true;
    return;
  }
  for (const g of groups) {
    const li = document.createElement("li");
    const left = document.createElement("span");
    left.className = "name";
    const dot = document.createElement("span");
    dot.className = `dot ${g.color || "grey"}`;
    left.appendChild(dot);
    left.appendChild(document.createTextNode(g.name));
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${g.count} tab${g.count === 1 ? "" : "s"}`;
    li.appendChild(left);
    li.appendChild(count);
    ol.appendChild(li);
  }
  $("results-section").hidden = false;
}

async function ungroup() {
  try {
    const tabs = await getTabs();
    const ids = tabs.filter((t) => t.groupId && t.groupId !== -1).map((t) => t.id);
    if (!ids.length) {
      setStatus("No hay grupos que deshacer.");
      return;
    }
    await chrome.tabs.ungroup(ids);
    $("groups").innerHTML = "";
    $("results-section").hidden = true;
    setStatus(`Deshechos ${ids.length} agrupados.`, "ok");
  } catch (e) {
    setStatus("Error: " + (e?.message || e), "error");
  }
}

$("categorize").addEventListener("click", categorize);
$("ungroup").addEventListener("click", ungroup);
$("scope").addEventListener("change", updateTabCount);

chrome.storage.local.get(["model", "scope", "auto"]).then((s) => {
  if (s.model) $("model").value = s.model;
  if (s.scope) $("scope").value = s.scope;
  $("auto").checked = !!s.auto;
  updateTabCount();
});

for (const id of ["model", "scope"]) {
  $(id).addEventListener("change", () => {
    chrome.storage.local.set({ [id]: $(id).value });
  });
}

$("auto").addEventListener("change", () => {
  const enabled = $("auto").checked;
  chrome.storage.local.set({ auto: enabled });
  if (enabled) {
    chrome.runtime.sendMessage({ type: "warmup-local" });
  }
});

function renderModelStatus(s) {
  const el = $("model-status");
  if (!s) {
    el.textContent = "Modelo: aún no cargado";
    return;
  }
  if (s.status === "ready") el.textContent = "Modelo local: listo ✓";
  else if (s.status === "downloading") {
    const pct = s.progress ? ` ${Math.round(s.progress)}%` : "";
    el.textContent = `Modelo: descargando ${s.file || ""}${pct}`;
  } else if (s.status === "error") el.textContent = "Modelo: error — " + s.error;
  else el.textContent = "Modelo: " + s.status;
}

chrome.storage.local.get("modelLoad").then((s) => renderModelStatus(s.modelLoad));
chrome.storage.onChanged.addListener((changes) => {
  if (changes.modelLoad) renderModelStatus(changes.modelLoad.newValue);
  if (changes.stats) renderStats();
});

function topCategory(byCategory) {
  let bestName = null;
  let bestN = 0;
  for (const [k, v] of Object.entries(byCategory || {})) {
    if (v > bestN) {
      bestN = v;
      bestName = k;
    }
  }
  return bestName;
}

async function renderStats() {
  const { stats } = await chrome.storage.local.get({
    stats: { total: 0, byCategory: {}, runs: 0, lastRun: null, bySource: {} }
  });
  $("stat-total").textContent = stats.total || 0;
  $("stat-runs").textContent = stats.runs || 0;
  const top = topCategory(stats.byCategory);
  $("stat-top").textContent = top || "—";
  $("stat-top").title = top || "";
  $("src-auto").textContent = stats.bySource?.auto || 0;
  $("src-popup").textContent = stats.bySource?.popup || 0;

  const ol = $("breakdown");
  ol.innerHTML = "";
  const entries = Object.entries(stats.byCategory || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (!entries.length) {
    $("breakdown-section").hidden = true;
    return;
  }
  for (const [name, n] of entries) {
    const li = document.createElement("li");
    const left = document.createElement("span");
    left.className = "name";
    left.textContent = name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${n}`;
    li.appendChild(left);
    li.appendChild(count);
    ol.appendChild(li);
  }
  $("breakdown-section").hidden = false;
}

$("reset-stats").addEventListener("click", async () => {
  if (!confirm("¿Borrar histórico de estadísticas?")) return;
  await new Promise((resolve) =>
    chrome.runtime.sendMessage({ type: "reset-stats" }, () => resolve())
  );
  renderStats();
});

renderStats();
