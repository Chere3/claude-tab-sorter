# Tab Sorter (Claude Code)

Extensión de Chrome/Chromium que agrupa tus pestañas en categorías generadas por IA usando el **Claude Agent SDK** (`@anthropic-ai/claude-agent-sdk`), que internamente reusa tu login OAuth de Claude Code.

```
[ popup ] ── chrome.runtime.sendNativeMessage ──► [ native host (Node ESM) ] ── query() ──► Agent SDK
                                                                                            │ (bundled claude binary, OAuth)
[ chrome.tabs.group ] ◄── { groups:[{name,color,tabIds}] } ◄────────────────────────────────┘
```

Las extensiones no pueden cargar paquetes npm ni ejecutar binarios directamente: usamos **Native Messaging** (un script Node local registrado en `~/Library/Application Support/.../NativeMessagingHosts/`).

## Requisitos

- macOS (los paths del install script son macOS; en Linux ajusta `~/.config/google-chrome/...`)
- Estar **logueado en Claude Code** (`claude /login`) — el SDK reusa esa auth, no necesitas `ANTHROPIC_API_KEY`
- `node` en PATH

## Instalación

0. **Instalar dependencias del host:**
   ```bash
   cd native-host && npm install
   ```

1. **Cargar la extensión sin empaquetar:**
   - Abre `chrome://extensions`
   - Activa *Modo de desarrollador*
   - *Cargar sin empaquetar* → selecciona la carpeta `extension/`
   - Copia el **ID** de la extensión (cadena tipo `abcdefghijklmnop...`)

2. **Registrar el native messaging host:**
   ```bash
   ./install.sh <EXTENSION_ID>            # Chrome
   ./install.sh <EXTENSION_ID> brave      # Brave
   ./install.sh <EXTENSION_ID> arc        # Arc
   ./install.sh <EXTENSION_ID> edge       # Edge
   ```

3. **Reinicia el navegador** (cierra todas las ventanas para que recargue los hosts).

4. Abre el popup, pulsa **Categorizar pestañas**.

## Uso

- *Categorizar pestañas* → llama a `claude -p` con el listado de pestañas y crea grupos nativos de Chrome.
- *Deshacer grupos* → quita todos los grupos de la ventana actual.
- Selector de modelo: `haiku` (más barato/rápido), `sonnet`, `opus`.
- Selector de ámbito: ventana actual o todas las ventanas.

## Logs / debug

- Native host: `~/.claude-tab-sorter.log`
- Extensión: clic derecho sobre el icono → *Inspeccionar popup* → consola
- Service worker: `chrome://extensions` → *Inspeccionar vista: background worker*

## Test manual del host (sin extensión)

```bash
node -e '
  const m = JSON.stringify({
    type: "categorize",
    model: "haiku",
    tabs: [
      {id:1,title:"GitHub",url:"https://github.com"},
      {id:2,title:"YouTube",url:"https://youtube.com"},
      {id:3,title:"MDN docs",url:"https://developer.mozilla.org"}
    ]
  });
  const b = Buffer.from(m);
  const h = Buffer.alloc(4); h.writeUInt32LE(b.length, 0);
  process.stdout.write(Buffer.concat([h, b]));
' | node native-host/host.js | node -e '
  let buf = Buffer.alloc(0);
  process.stdin.on("data", c => buf = Buffer.concat([buf, c]));
  process.stdin.on("end", () => {
    const len = buf.readUInt32LE(0);
    console.log(JSON.stringify(JSON.parse(buf.subarray(4, 4 + len).toString()), null, 2));
  });
'
```

## Estructura

```
claude-tab-sorter/
├── extension/
│   ├── manifest.json        Manifest V3, pide tabs + tabGroups + nativeMessaging
│   ├── popup.html / .css    UI mínima
│   ├── popup.js             query tabs → sendNativeMessage → tabs.group / tabGroups.update
│   └── background.js        service worker (placeholder)
├── native-host/
│   ├── host.js              ESM, native messaging stdin/stdout + Agent SDK query() con json_schema
│   ├── package.json         Declara @anthropic-ai/claude-agent-sdk
│   └── node_modules/        (tras `npm install`)
├── install.sh                Registra el host en NativeMessagingHosts/
└── README.md
```

## Notas

- El host llama a `query()` del Agent SDK con `allowedTools: []`, `maxTurns: 3` y `outputFormat: { type: 'json_schema', schema }`. El SDK valida el output contra el schema y lo devuelve en `message.structured_output`.
- El SDK de TS empaqueta el binario nativo de Claude Code (`@anthropic-ai/claude-agent-sdk-darwin-arm64`) como optional dependency, así que reusa la sesión OAuth que ya tienes con `claude` — sin API key.
- El ID de extensión cambia entre cargas sin empaquetar; si lo recargas con otra carpeta tendrás que re-ejecutar `install.sh`.
- A partir del 15-jun-2026 el uso del SDK con suscripción consumirá del "Agent SDK credit" mensual ([detalles](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)).
