# Manual de anotación — Tab Sorter

Etiquetas pestañas de navegador reales con la categoría **que debería** tener. Tu salida entrena un clasificador; la consistencia entre anotadores importa más que tu opinión personal sobre un caso concreto. Cuando dudes, aplica la regla escrita aunque te parezca subóptima.

## Taxonomía (11 categorías, cerradas)

Usa el string EXACTO, con emoji:

| Etiqueta | Cubre |
|---|---|
| `💻 Desarrollo` | Escribir/ejecutar/desplegar software. Repos, PRs, issues, docs de API y lenguajes, Stack Overflow, npm/PyPI, Docker, cloud consoles, `localhost`, `127.0.0.1`, herramientas de dev. |
| `🔬 Investigación` | Buscar información con profundidad sobre un tema: papers, wikis, documentación de referencia no-software, búsquedas informativas, comparativas técnicas, foros de consulta. |
| `🤖 IA` | Productos y contenido de IA/LLM: ChatGPT, Claude, Gemini, Mistral, Perplexity, HuggingFace, generadores de imagen/video, noticias y papers *sobre* IA. Precede a Desarrollo cuando el sujeto es la IA misma. |
| `💬 Redes Sociales` | Feeds y perfiles sociales: X/Twitter, LinkedIn, Reddit, Instagram, Facebook, TikTok, Discord, mensajería. Incluye login/notificaciones de esos sitios. |
| `🎬 Entretenimiento` | Consumo pasivo de ocio: música, videos de ocio, streaming, series/películas, gaming, deportes, memes, humor. `music.youtube.com` es siempre esta categoría. |
| `⚡ Productividad` | Trabajo y organización personal: email, calendario, Notion/Docs/Sheets/Slides, Jira/Asana/Trello, Slack/Teams, CRM/BI empresarial (Power BI), SSO corporativo, gestión de archivos, impresión, trámites. |
| `🛒 Compras` | Comercio: marketplaces, fichas de producto, carrito/checkout, pedidos y envíos, comida a domicilio, reseñas de producto con intención de compra, cupones. |
| `📰 Noticias` | Actualidad y periodismo: diarios, portales de noticias, agregadores de noticias, cobertura de eventos actuales, política, clima. |
| `💰 Finanzas` | Dinero: banca, tarjetas, inversión, bolsa, cripto, exchanges, mercados de predicción, facturación/pagos, impuestos, contabilidad. |
| `📚 Aprendizaje` | Formación estructurada: cursos, LMS/campus universitario, tutoriales paso a paso, certificaciones, práctica de idiomas, tareas escolares, material didáctico. |
| `✈️ Viajes` | Desplazamientos: vuelos, hoteles, reservas, mapas y rutas, check-in, transporte, turismo, destinos. |

## Jerarquía de decisión

Aplica en orden y **detente en la primera que resuelva**:

1. **Intención dominante del usuario**, no el dominio. Un tutorial de React en YouTube es `📚 Aprendizaje`, no `🎬 Entretenimiento`. Una noticia sobre GPT-5 en un diario es `🤖 IA`, no `📰 Noticias`.
2. **El título manda sobre el host** cuando el host es genérico. `google.com` con título `"clima cdmx - Buscar con Google"` es `📰 Noticias`; con `"error handling rust - Buscar"` es `💻 Desarrollo`. Un buscador hereda la categoría de lo buscado.
3. **Host inequívoco cuando el título no informa.** Títulos vacíos, `"(sin título)"`, `"Inicio"`, `"Login"`, `"Redirecting..."`, `"Nueva pestaña"` → categoriza por el host.
4. **Especificidad**: la categoría más específica gana sobre la más general. Comprar un vuelo es `✈️ Viajes`, no `🛒 Compras`. Un curso de trading es `📚 Aprendizaje`, no `💰 Finanzas`.
5. **Última instancia**: si sigue ambiguo, elige la categoría que un usuario esperaría ver en la barra de pestañas, marca `confidence: "low"` y explica la duda en `rationale`.

## Reglas de desempate ya decididas

No las re-litigues; existen para que 14 anotadores coincidan.

- `music.youtube.com` → siempre `🎬 Entretenimiento`, sin importar el título.
- `youtube.com` → decide por título: música/gaming/vlog/humor/deporte → `🎬 Entretenimiento`; tutorial o curso → `📚 Aprendizaje`; charla técnica o conferencia de ingeniería → `💻 Desarrollo`; contenido sobre IA → `🤖 IA`; noticiero → `📰 Noticias`. Si el título es opaco (`"YouTube"`, `"(sin título)"`) → `🎬 Entretenimiento`.
- `x.com`, `reddit.com` → `💬 Redes Sociales` por defecto. Solo cambia si el título identifica sin ambigüedad un tema de otra categoría (p. ej. un hilo `r/learnpython` sobre un error concreto → `💻 Desarrollo`).
- `linkedin.com` → `💬 Redes Sociales` por defecto (feed, perfiles, notificaciones, mensajes). Búsqueda de empleo, postulaciones y portales de reclutamiento (`*.eightfold.ai`, `jobs.*`) → `⚡ Productividad`.
- `google.com/search` y buscadores → hereda la categoría del query en el título (regla 2). `google.com` a secas o Gmail/Drive/Calendar → `⚡ Productividad`.
- `localhost`, `127.0.0.1`, `*.local`, puertos de desarrollo → `💻 Desarrollo`, siempre.
- SSO y logins corporativos (`login.microsoftonline.com`, `*.okta.com`, `accounts.google.com`) → `⚡ Productividad`, salvo que el título nombre el servicio destino, en cuyo caso hereda de ese servicio.
- LMS y campus universitarios (`*.instructure.com`, Canvas, Blackboard, Moodle, `*.sharepoint.com` de una universidad) → `📚 Aprendizaje`.
- Comida a domicilio (UberEats, Rappi, DiDi Food) → `🛒 Compras`. No existe categoría de comida.
- Mercados de predicción (Polymarket, Kalshi) y exchanges cripto → `💰 Finanzas`.
- Chatbots de IA (`claude.ai`, `chatgpt.com`, `chat.mistral.ai`, `chat.z.ai`, `gemini.google.com`) → `🤖 IA`, aunque el usuario los use para programar.
- Herramientas BI/empresariales (`app.powerbi.com`, Jira, Confluence, Salesforce) → `⚡ Productividad`, aunque el usuario sea desarrollador.
- Streaming de video (Twitch, Netflix, Disney+, `livetv.sx`) → `🎬 Entretenimiento`.
- Fichas de producto, listados y carritos de cualquier marketplace → `🛒 Compras`, incluso si el producto es un libro técnico o un curso.

## Cuando ninguna de las 11 encaja

La taxonomía es cerrada: **siempre** debes elegir una de las 11 como `category`. Pero si el ajuste es forzado, rellena además `proposedCategory` con el nombre de la categoría que faltaría (formato `emoji Nombre`, 1-2 palabras). Esto es una señal para ampliar la taxonomía después; no cambia tu etiqueta primaria.

## Formato de salida

Escribe **exactamente** un JSON array, un objeto por item de entrada, mismo orden, sin envolturas ni markdown:

```json
[
  {"id":"t00042","category":"🎬 Entretenimiento","confidence":"high","rationale":"video musical en YouTube","proposedCategory":null},
  {"id":"t00043","category":"💻 Desarrollo","confidence":"medium","rationale":"issue de repo","proposedCategory":null}
]
```

- `id`: copiado literal del input. Obligatorio y único.
- `category`: uno de los 11 strings exactos, con emoji. Nunca `null`, nunca inventado.
- `confidence`: `"high"` | `"medium"` | `"low"`. Usa `low` de verdad cuando dudes — se usa para medir calibración.
- `rationale`: máximo 12 palabras, en español.
- `proposedCategory`: `null` salvo el caso descrito arriba.

**Debes emitir una entrada por cada item del input, sin excepción.** Un item omitido invalida el shard y obliga a repetirlo.
