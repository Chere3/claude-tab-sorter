import { PROTOTYPES } from "./prototypes.js";

const PAGE_SIZE = 50;
const $ = (id) => document.getElementById(id);

const LOCAL_CATEGORIES = Object.keys(PROTOTYPES).map(
  (k) => `${PROTOTYPES[k].emoji} ${k}`
);

const state = {
  all: [],
  filtered: [],
  page: 0,
  selected: new Set()
};

function ts(t) {
  const d = new Date(t);
  return d.toLocaleString(undefined, {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function effectiveLabel(e) {
  return e.userCategory || e.category || e.fallbackCategory || null;
}

function isCorrected(e) {
  return e.userCategory && e.userCategory !== e.category;
}

async function loadAll() {
  const { dataset } = await chrome.storage.local.get({ dataset: [] });
  state.all = dataset.map((e, i) => ({ ...e, _i: i }));
  state.all.sort((a, b) => b.ts - a.ts);
  populateCategoryDropdowns();
  applyFilters();
}

function uniqueCategories() {
  const set = new Set(LOCAL_CATEGORIES);
  for (const e of state.all) {
    if (e.category) set.add(e.category);
    if (e.userCategory) set.add(e.userCategory);
    if (e.fallbackCategory) set.add(e.fallbackCategory);
  }
  return Array.from(set).sort();
}

function populateCategoryDropdowns() {
  const cats = uniqueCategories();
  for (const id of ["filter-category", "bulk-target"]) {
    const sel = $(id);
    const keep = sel.value;
    const first = sel.querySelector("option");
    sel.innerHTML = "";
    sel.appendChild(first.cloneNode(true));
    for (const c of cats) {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      sel.appendChild(o);
    }
    if (keep) sel.value = keep;
  }
}

function applyFilters() {
  const q = $("search").value.trim().toLowerCase();
  const src = $("filter-source").value;
  const cat = $("filter-category").value;
  const status = $("filter-status").value;

  state.filtered = state.all.filter((e) => {
    if (q && !((e.title || "").toLowerCase().includes(q) || (e.url || "").toLowerCase().includes(q))) {
      return false;
    }
    if (src && e.source !== src) return false;
    if (cat) {
      const label = effectiveLabel(e);
      if (label !== cat) return false;
    }
    if (status === "confirmed" && !e.userCategory) return false;
    if (status === "unconfirmed" && e.userCategory) return false;
    if (status === "corrected" && !isCorrected(e)) return false;
    if (status === "uncategorized" && e.category) return false;
    return true;
  });

  state.page = 0;
  render();
}

function render() {
  const tbody = $("rows");
  tbody.innerHTML = "";
  state.selected.clear();
  $("check-all").checked = false;

  if (!state.filtered.length) {
    $("empty").hidden = false;
    $("page-info").textContent = "0 eventos";
    return;
  }
  $("empty").hidden = true;

  const start = state.page * PAGE_SIZE;
  const end = Math.min(start + PAGE_SIZE, state.filtered.length);
  const slice = state.filtered.slice(start, end);

  const cats = uniqueCategories();

  for (const e of slice) {
    const tr = document.createElement("tr");

    const check = document.createElement("td");
    check.className = "check-col";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.dataset.i = e._i;
    cb.addEventListener("change", () => {
      if (cb.checked) state.selected.add(e._i);
      else state.selected.delete(e._i);
    });
    check.appendChild(cb);

    const when = document.createElement("td");
    when.className = "sim";
    when.textContent = ts(e.ts);

    const titleCell = document.createElement("td");
    titleCell.className = "title-cell";
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = e.title || "(sin título)";
    t.title = e.title || "";
    const u = document.createElement("div");
    u.className = "u";
    u.textContent = e.url || "";
    u.title = e.url || "";
    titleCell.appendChild(t);
    titleCell.appendChild(u);

    const pred = document.createElement("td");
    pred.className = "pred" + (e.category ? "" : " null");
    pred.textContent = e.category || (e.fallbackCategory ? `(${e.fallbackCategory})` : "—");

    const finalCell = document.createElement("td");
    const sel = document.createElement("select");
    sel.className = "relabel";
    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "— sin etiqueta —";
    sel.appendChild(emptyOpt);
    for (const c of cats) {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      sel.appendChild(o);
    }
    sel.value = e.userCategory || e.category || "";
    if (isCorrected(e)) {
      const dot = document.createElement("span");
      dot.className = "confirmed-dot";
      finalCell.appendChild(dot);
    }
    sel.addEventListener("change", async () => {
      await sendMessage({
        type: "relabel-entry",
        index: e._i,
        userCategory: sel.value || null
      });
      await loadAll();
    });
    finalCell.appendChild(sel);

    const sim = document.createElement("td");
    sim.className = "sim";
    sim.textContent = e.similarity != null ? e.similarity.toFixed(2) : "—";

    const src = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `badge ${e.source || ""}`;
    badge.textContent = e.source || "—";
    src.appendChild(badge);

    const del = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.className = "delete";
    delBtn.title = "Eliminar";
    delBtn.textContent = "×";
    delBtn.addEventListener("click", async () => {
      if (!confirm("¿Eliminar esta entrada?")) return;
      await sendMessage({ type: "delete-entry", index: e._i });
      await loadAll();
    });
    del.appendChild(delBtn);

    tr.appendChild(check);
    tr.appendChild(when);
    tr.appendChild(titleCell);
    tr.appendChild(pred);
    tr.appendChild(finalCell);
    tr.appendChild(sim);
    tr.appendChild(src);
    tr.appendChild(del);
    tbody.appendChild(tr);
  }

  $("page-info").textContent =
    `${start + 1}–${end} de ${state.filtered.length} (total: ${state.all.length})`;
  $("prev").disabled = state.page === 0;
  $("next").disabled = end >= state.filtered.length;
  renderSummary();
}

function renderSummary() {
  const totals = { auto: 0, claude: 0, confirmed: 0, corrected: 0, uncategorized: 0 };
  for (const e of state.all) {
    if (e.source === "auto") totals.auto++;
    if (e.source === "claude") totals.claude++;
    if (e.userCategory) totals.confirmed++;
    if (isCorrected(e)) totals.corrected++;
    if (!e.category) totals.uncategorized++;
  }
  $("summary").textContent =
    `${state.all.length} eventos · 🤖 ${totals.auto} · ✨ ${totals.claude} · ✓ ${totals.confirmed} confirmados · ↻ ${totals.corrected} corregidos · ? ${totals.uncategorized} sin categoría`;
}

function sendMessage(msg) {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage(msg, (r) => resolve(r))
  );
}

function download(filename, content, mime = "application/octet-stream") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function exportJsonl() {
  const lines = state.filtered
    .map((e) => {
      const { _i, ...clean } = e;
      return JSON.stringify(clean);
    })
    .join("\n");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  download(`tab-sorter-dataset-${stamp}.jsonl`, lines + "\n", "application/jsonl");
}

function exportDatasetPy() {
  const byCat = new Map();
  for (const e of state.filtered) {
    const label = e.userCategory || e.category;
    if (!label) continue;
    const text = `${(e.title || "").trim()} ${e.url || ""}`.trim();
    if (!text) continue;
    if (!byCat.has(label)) byCat.set(label, new Set());
    byCat.get(label).add(text);
  }
  const cats = Array.from(byCat.keys()).sort();
  const lines = [
    '"""Auto-exported from Tab Sorter — Tab Sorter dataset.py.',
    "",
    "Eventos confirmados/clasificados por el usuario.",
    '"""',
    "",
    "CATEGORIES = [",
    ...cats.map((c) => `    ${JSON.stringify(c)},`),
    "]",
    "",
    "LABEL2ID = {c: i for i, c in enumerate(CATEGORIES)}",
    "",
    "DATASET = {"
  ];
  for (const c of cats) {
    lines.push(`    ${JSON.stringify(c)}: [`);
    for (const t of byCat.get(c)) {
      lines.push(`        ${JSON.stringify(t)},`);
    }
    lines.push("    ],");
  }
  lines.push("}");
  lines.push("");
  lines.push("def build_inputs():");
  lines.push("    items = []");
  lines.push("    for c, texts in DATASET.items():");
  lines.push("        for t in texts:");
  lines.push("            items.append((t, LABEL2ID[c]))");
  lines.push("    return items");
  lines.push("");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  download(`dataset-${stamp}.py`, lines.join("\n"), "text/x-python");
}

async function bulkApply() {
  const target = $("bulk-target").value;
  if (!target) {
    alert("Elige una categoría destino.");
    return;
  }
  if (!state.selected.size) {
    alert("Selecciona al menos una fila.");
    return;
  }
  if (!confirm(`Reasignar ${state.selected.size} entradas a "${target}"?`)) return;
  for (const i of state.selected) {
    await sendMessage({ type: "relabel-entry", index: i, userCategory: target });
  }
  await loadAll();
}

$("search").addEventListener("input", applyFilters);
$("filter-source").addEventListener("change", applyFilters);
$("filter-category").addEventListener("change", applyFilters);
$("filter-status").addEventListener("change", applyFilters);
$("prev").addEventListener("click", () => { state.page--; render(); });
$("next").addEventListener("click", () => { state.page++; render(); });
$("export-jsonl").addEventListener("click", exportJsonl);
$("export-py").addEventListener("click", exportDatasetPy);
$("bulk-apply").addEventListener("click", bulkApply);
$("check-all").addEventListener("change", (e) => {
  const checks = document.querySelectorAll("#rows input[type=checkbox]");
  for (const cb of checks) {
    cb.checked = e.target.checked;
    const i = Number(cb.dataset.i);
    if (e.target.checked) state.selected.add(i);
    else state.selected.delete(i);
  }
});
$("clear").addEventListener("click", async () => {
  if (!confirm("¿Vaciar todo el dataset? Esto NO se puede deshacer.")) return;
  await sendMessage({ type: "clear-dataset" });
  await loadAll();
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.dataset) loadAll();
});

loadAll();
