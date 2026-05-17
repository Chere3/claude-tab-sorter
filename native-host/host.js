#!/usr/bin/env node
import { query } from "@anthropic-ai/claude-agent-sdk";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const LOG_FILE = path.join(os.homedir(), ".claude-tab-sorter.log");

function log(...args) {
  try {
    fs.appendFileSync(
      LOG_FILE,
      `[${new Date().toISOString()}] ${args
        .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
        .join(" ")}\n`
    );
  } catch {}
}

let stdinBuf = Buffer.alloc(0);
let stdinEnded = false;
const stdinReaders = [];

process.stdin.on("data", (chunk) => {
  stdinBuf = Buffer.concat([stdinBuf, chunk]);
  drainReaders();
});
process.stdin.on("end", () => {
  stdinEnded = true;
  drainReaders();
});

function drainReaders() {
  while (stdinReaders.length) {
    const r = stdinReaders[0];
    if (stdinBuf.length < 4) {
      if (stdinEnded) {
        stdinReaders.shift();
        r.resolve(null);
        continue;
      }
      return;
    }
    const length = stdinBuf.readUInt32LE(0);
    if (stdinBuf.length < 4 + length) {
      if (stdinEnded) {
        stdinReaders.shift();
        r.reject(new Error("truncated message"));
        continue;
      }
      return;
    }
    const body = stdinBuf.subarray(4, 4 + length).toString("utf8");
    stdinBuf = stdinBuf.subarray(4 + length);
    stdinReaders.shift();
    try {
      r.resolve(JSON.parse(body));
    } catch (e) {
      r.reject(e);
    }
  }
}

function readMessage() {
  return new Promise((resolve, reject) => {
    stdinReaders.push({ resolve, reject });
    drainReaders();
  });
}

function sendMessage(msg) {
  const body = Buffer.from(JSON.stringify(msg), "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length, 0);
  process.stdout.write(header);
  process.stdout.write(body);
}

const COLORS = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"];

function shortTitle(t) {
  return (t || "").replace(/\s+/g, " ").trim().slice(0, 120);
}

function shortUrl(u) {
  try {
    const url = new URL(u);
    return url.hostname + url.pathname.slice(0, 60);
  } catch {
    return (u || "").slice(0, 80);
  }
}

function buildBatchPrompt(tabs) {
  const lines = tabs.map((t) => `- [${t.id}] ${shortTitle(t.title)} — ${shortUrl(t.url)}`);
  return `Clasifica estas pestañas en 2-7 grupos temáticos.

${lines.join("\n")}

Devuelve JSON: {"groups":[{"name":"<emoji> <1-2 palabras>","color":"<color>","tabIds":[id,...]}]}.
Cada tabId aparece una vez. Colores: ${COLORS.join(", ")}.
El nombre DEBE empezar con un emoji representativo seguido de un espacio y 1-2 palabras (ej: "💻 Desarrollo", "🎬 Video", "📰 Noticias"). Usa el idioma dominante.`;
}

function buildIncrementalPrompt(tabs, existingGroups) {
  const groupsBlock = existingGroups.length
    ? existingGroups
        .map((g) => `- "${g.name}" (${g.color}): ${(g.sampleTitles || []).slice(0, 3).map(shortTitle).join(" | ") || "vacío"}`)
        .join("\n")
    : "(no hay grupos todavía)";

  const tabsBlock = tabs
    .map((t) => `- [${t.id}] ${shortTitle(t.title)} — ${shortUrl(t.url)}`)
    .join("\n");

  return `Asigna cada pestaña a un grupo: reusa uno existente si encaja, o crea uno nuevo.

Grupos existentes:
${groupsBlock}

Pestañas a clasificar:
${tabsBlock}

Reglas:
- Prefiere grupos existentes. Solo crea uno nuevo si la pestaña no encaja en ninguno.
- Si reusas un grupo, usa EXACTAMENTE su nombre tal cual está arriba (incluyendo el emoji si lo tiene).
- Para grupos nuevos: nombre con formato "<emoji> <1-2 palabras>" (ej: "💻 Desarrollo") y color de [${COLORS.join(", ")}].

JSON: {"assignments":[{"tabId":<id>,"group":"<emoji> <nombre>","isNew":<bool>,"color":"<color si isNew>"}]}`;
}

const BATCH_SCHEMA = {
  type: "object",
  required: ["groups"],
  additionalProperties: false,
  properties: {
    groups: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        required: ["name", "color", "tabIds"],
        additionalProperties: false,
        properties: {
          name: { type: "string", minLength: 1 },
          color: { type: "string", enum: COLORS },
          tabIds: { type: "array", items: { type: "integer" }, minItems: 1 }
        }
      }
    }
  }
};

const INCREMENTAL_SCHEMA = {
  type: "object",
  required: ["assignments"],
  additionalProperties: false,
  properties: {
    assignments: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        required: ["tabId", "group", "isNew"],
        additionalProperties: false,
        properties: {
          tabId: { type: "integer" },
          group: { type: "string", minLength: 1 },
          isNew: { type: "boolean" },
          color: { type: "string", enum: COLORS }
        }
      }
    }
  }
};

function extractJson(text) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = fenced ? fenced[1] : text;
  const match = candidate.match(/\{[\s\S]*\}/);
  if (!match) throw new Error("No JSON object found in response");
  return JSON.parse(match[0]);
}

const SYSTEM_PROMPT =
  "Eres un clasificador de pestañas. Respondes SOLO con un objeto JSON válido que cumpla el schema indicado, sin prosa ni markdown.";

async function runQuery({ prompt, model, schema }) {
  const t0 = Date.now();
  log("sdk query", { model, promptLen: prompt.length });
  let structured = null;
  let resultText = null;
  let resultSubtype = null;

  for await (const message of query({
    prompt,
    options: {
      model: model || "haiku",
      allowedTools: [],
      maxTurns: 1,
      systemPrompt: SYSTEM_PROMPT,
      settingSources: []
    }
  })) {
    if (message.type === "result") {
      if (message.structured_output) structured = message.structured_output;
      if (typeof message.result === "string") resultText = message.result;
      resultSubtype = message.subtype;
    }
  }

  log("sdk done", { ms: Date.now() - t0, hasStructured: !!structured });

  if (structured && typeof structured === "object") return structured;
  if (resultText && resultText.trim()) return extractJson(resultText);
  throw new Error(`SDK returned no usable output (subtype=${resultSubtype})`);
}

async function categorizeBatch(tabs, model) {
  return runQuery({
    prompt: buildBatchPrompt(tabs),
    model,
    schema: BATCH_SCHEMA
  });
}

async function classifyIncremental(tabs, existingGroups, model) {
  return runQuery({
    prompt: buildIncrementalPrompt(tabs, existingGroups || []),
    model,
    schema: INCREMENTAL_SCHEMA
  });
}

async function handle(msg) {
  if (msg.type === "ping") {
    return { ok: true, pong: true, runtime: "agent-sdk" };
  }
  if (msg.type === "categorize") {
    if (!Array.isArray(msg.tabs) || !msg.tabs.length) {
      return { ok: false, error: "tabs array missing or empty" };
    }
    const result = await categorizeBatch(msg.tabs, msg.model);
    return { ok: true, result };
  }
  if (msg.type === "classify-incremental") {
    if (!Array.isArray(msg.tabs) || !msg.tabs.length) {
      return { ok: false, error: "tabs array missing or empty" };
    }
    const result = await classifyIncremental(
      msg.tabs,
      msg.existingGroups || [],
      msg.model
    );
    return { ok: true, result };
  }
  return { ok: false, error: `unknown message type: ${msg.type}` };
}

let serial = Promise.resolve();

(async () => {
  log("host started", { pid: process.pid });
  while (true) {
    let msg;
    try {
      msg = await readMessage();
    } catch (e) {
      log("read error", e?.message);
      break;
    }
    if (!msg) {
      log("EOF, exiting");
      break;
    }
    const requestId = msg.requestId;
    log("recv", { type: msg.type, requestId, n: msg.tabs?.length });

    serial = serial.then(async () => {
      try {
        const response = await handle(msg);
        sendMessage({ ...response, requestId });
      } catch (e) {
        log("handler error", e?.message);
        sendMessage({ ok: false, error: e?.message || String(e), requestId });
      }
    });
  }
})();
