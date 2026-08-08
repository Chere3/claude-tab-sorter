"""
Filtro de sensibilidad para todo lo que se publica en el repo.

El corpus es navegación personal real. Cualquier artefacto que salga a un repo público
(prototipos, informe) pasa por aquí. Conservador por diseño: ante la duda, redacta.

Criterio: un item es publicable solo si (a) su host es un sitio masivo de uso general,
y (b) su título es estructural (navegación del propio sitio), no contenido consultado.
Un título de contenido revela qué leyó/buscó/compró la persona; uno estructural no.
"""
import re

# Dominios de uso masivo cuya sola visita no revela nada particular.
# Deliberadamente NO incluye: banca, cripto, salud, empleo, corporativos, educativos,
# mensajería, gobierno, ni nada self-hosted.
HOSTS_PUBLICOS = {
    "youtube.com", "music.youtube.com", "x.com", "twitter.com", "reddit.com",
    "linkedin.com", "github.com", "stackoverflow.com", "developer.mozilla.org",
    "amazon.com", "amazon.com.mx", "ebay.com", "mercadolibre.com.mx",
    "listado.mercadolibre.com.mx", "articulo.mercadolibre.com.mx",
    "aliexpress.com", "es.aliexpress.com", "alibaba.com", "temu.com",
    "twitch.tv", "netflix.com", "wikipedia.org", "es.wikipedia.org",
    "claude.ai", "chatgpt.com", "gemini.google.com", "huggingface.co",
    "npmjs.com", "pypi.org", "docs.python.org", "chromewebstore.google.com",
}

# Un título que casa con esto revela contenido consultado, no navegación.
CONTENIDO = re.compile(
    r"google\s*search|buscar\s+con\s+google|"          # queries literales
    r"\b(u\d+\s*a\d+|tarea|actividad\s+\d|examen|calific)|"  # trabajos de la universidad
    r"\b(pedido|order\s*#|factura|invoice|recibo|envío|tracking|rastrear)|"  # transacciones
    r"\b(saldo|transferencia|estado de cuenta|tarjeta)|"                     # finanzas
    r"@|\+\d{7,}|\b\d{6,}\b",                                                # correos, teléfonos, ids
    re.I,
)

# Títulos estructurales conocidos: navegación del propio sitio, sin contenido.
ESTRUCTURAL = re.compile(
    r"^\(?\d*\)?\s*(home|inicio|feed|notificaciones?|notifications?|mensajes?|messages?|"
    r"buscar|search|explorar|explore|perfil|profile|configuraci[óo]n|settings|"
    r"biblioteca|library|suscripciones|subscriptions|tendencias|trending|"
    r"carrito|cart|mi cuenta|my account|historial|history|gestor de invitaciones|"
    r"gestor de red|mi red|gestor)\b",
    re.I,
)

# Títulos de marca a secas ("YouTube", "LinkedIn", "YouTube Music"): no revelan nada.
MARCA = re.compile(
    r"^(youtube( music)?|linkedin|x|twitter|reddit|github|amazon|ebay|"
    r"mercado\s?libre|aliexpress|alibaba|temu|twitch|netflix|claude|chatgpt)"
    r"(\s*[|\-—/]\s*.{0,24})?$",
    re.I,
)


def es_publicable(host, titulo):
    """True solo si el item puede aparecer en un artefacto público."""
    h = (host or "").lower().strip()
    t = (titulo or "").strip()
    if h not in HOSTS_PUBLICOS:
        return False
    if CONTENIDO.search(t):
        return False
    # nombres propios: dos palabras capitalizadas seguidas (perfiles de terceros)
    if re.search(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}", t) \
       and not MARCA.match(t):
        return False
    return bool(ESTRUCTURAL.match(t) or MARCA.match(t))


def redacta(host, titulo, placeholder="(redactado)"):
    """Devuelve el título si es publicable; si no, un marcador."""
    return titulo if es_publicable(host, titulo) else placeholder


def redacta_host(host):
    """Host si es público; si no, una etiqueta de categoría de host."""
    h = (host or "").lower().strip()
    if h in HOSTS_PUBLICOS:
        return h
    if h in ("localhost", "127.0.0.1") or h.endswith(".local"):
        return "(desarrollo local)"
    if re.search(r"instructure|blackboard|moodle|\.edu|uag", h):
        return "(campus universitario)"
    if re.search(r"jira|confluence|sharepoint|powerbi|okta|microsoftonline|eightfold", h):
        return "(herramienta corporativa)"
    if re.search(r"bank|banco|bbva|hsbc|binance|okx|paypal|stripe|polymarket|nu\.com", h):
        return "(finanzas)"
    return "(otro dominio)"
