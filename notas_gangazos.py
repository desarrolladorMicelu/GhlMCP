"""
Catálogo de notas de gangazos.

Cada nota trae:
  - codigo: etiqueta corta (DG, VR, PF...)
  - titulo: descripción corta
  - resumen: frase corta para el cliente
  - texto: explicación comercial completa
  - alias: formas en que puede venir escrita la nota

La búsqueda normaliza mayúsculas/acentos/signos, busca alias exactos, frases
contenidas, prefijos de códigos combinados y, si nada coincide, deja la nota
literal para que la IA no invente.
"""

import difflib
import re
import unicodedata

CATALOGO = [
    {
        "codigo": "CC",
        "titulo": "CAMARA CAMBIADA",
        "resumen": "Cámara reemplazada, 100% funcional.",
        "texto": (
            "Cámara reemplazada, 100% funcional. Este iPhone tiene un módulo de "
            "cámara cambiado: toma fotos y graba video con normalidad. Al ser una "
            "pieza no original de fábrica, iOS puede mostrar un aviso de \"pieza "
            "desconocida\" y la calidad puede variar ligeramente frente a la "
            "original. Es un detalle estético/informativo que no afecta el resto "
            "del equipo ni genera daños a futuro. Te lo contamos para que compres "
            "con total claridad."
        ),
        "alias": [
            "CC", "CAMARA CAMBIADA", "CÁMARA CAMBIADA", "CAMARA REEMPLAZADA",
            "CÁMARA REEMPLAZADA", "MODULO DE CAMARA CAMBIADO",
            "MÓDULO DE CÁMARA CAMBIADO",
        ],
    },
    {
        "codigo": "DG",
        "titulo": "DISPLAY GENERICO",
        "resumen": "Pantalla genérica de buena calidad.",
        "texto": (
            "Pantalla genérica de buena calidad. La pantalla de este equipo fue "
            "reemplazada por una genérica (no original de Apple). Funciona "
            "perfectamente para el uso diario: táctil, brillo y visualización "
            "correctos. Puedes notar una pequeña diferencia en colores, brillo "
            "máximo o consumo de batería frente a la original, y iOS puede mostrar "
            "un aviso de pantalla no original. No compromete el funcionamiento "
            "interno del iPhone. Transparencia total para que elijas informado."
        ),
        "alias": [
            "DG", "DISPLAY GENERICO", "DISPLAY GENÉRICO", "PANTALLA GENERICA",
            "PANTALLA GENÉRICA", "DISPLAY CAMBIADO", "PANTALLA CAMBIADA",
        ],
    },
    {
        "codigo": "VR",
        "titulo": "VISOR RAYADO",
        "resumen": "Rayones leves en el visor de la pantalla.",
        "texto": (
            "Rayones leves en el visor de la pantalla. El vidrio frontal de la "
            "pantalla presenta rayones o marcas de uso, visibles sobre todo con la "
            "pantalla apagada o bajo luz directa. La pantalla se ve, responde al "
            "tacto y funciona al 100%; los rayones son superficiales y no avanzan "
            "ni afectan otros componentes. Con un vidrio templado o protector "
            "quedan prácticamente disimulados. Un iPhone funcional a mejor precio, "
            "con total transparencia."
        ),
        "alias": [
            "VR", "VISOR RAYADO", "VIDRIO RAYADO", "RAYONES EN VISOR",
            "RAYON EN VISOR", "RAYÓN EN VISOR",
        ],
    },
    {
        "codigo": "BACKOVER",
        "titulo": "BACKOVER MALTRATADO",
        "resumen": "Tapa trasera con detalles de uso.",
        "texto": (
            "Tapa trasera con detalles de uso. La parte posterior (vidrio o "
            "carcasa trasera) presenta marcas, rayones o desgaste visibles. Es un "
            "tema 100% estético: no afecta la batería, la carga, la cámara ni "
            "ninguna función. Con un forro o estuche queda totalmente cubierto. "
            "Pagas menos por un equipo que funciona igual de bien."
        ),
        "alias": [
            "BACKOVER", "BACK COVER", "BACKOVER MALTRATADO", "GOLPE EN BACKOVER",
            "TAPA TRASERA MALTRATADA",
        ],
    },
    {
        "codigo": "MC",
        "titulo": "MANCHA EN CAMARA",
        "resumen": "Pequeña mancha en la cámara.",
        "texto": (
            "Pequeña mancha en la cámara. Uno de los lentes de la cámara presenta "
            "una mancha que puede aparecer en algunas fotos, sobre todo en fondos "
            "claros o con zoom. La cámara enfoca, graba y funciona con normalidad, y "
            "el detalle no se extiende ni afecta otras partes del equipo. Te lo "
            "explicamos para que sepas exactamente qué esperar al tomar fotos."
        ),
        "alias": [
            "MC", "MCX", "MANCHA EN CAMARA", "MANCHA EN CÁMARA",
            "MANCHA CAMARA", "MANCHA CÁMARA", "MANCHA EN LA CAMARA",
        ],
    },
    {
        "codigo": "BC",
        "titulo": "BATERIA CAMBIADA",
        "resumen": "Batería reemplazada, lista para rendir.",
        "texto": (
            "Batería reemplazada, lista para rendir. Este iPhone tiene una batería "
            "cambiada, lo que significa que no arrastra el desgaste de la batería "
            "anterior. Al no ser una pieza instalada por Apple, iOS puede mostrar un "
            "mensaje de \"batería no original\" y no indicar el porcentaje de salud "
            "en Ajustes. El equipo carga y funciona con normalidad; es un aviso "
            "informativo que no daña el teléfono."
        ),
        "alias": [
            "BC", "BATERIA CAMBIADA", "BATERÍA CAMBIADA", "BATERIA REEMPLAZADA",
            "BATERÍA REEMPLAZADA",
        ],
    },
    {
        "codigo": "SFID",
        "titulo": "SIN FACE ID",
        "resumen": "Equipo sin Face ID, todo lo demás funciona.",
        "texto": (
            "Equipo sin Face ID, todo lo demás funciona. El reconocimiento facial "
            "no está activo en este iPhone. Puedes desbloquearlo y autorizar "
            "compras, apps bancarias y Apple Pay usando tu código de acceso. No "
            "afecta la cámara frontal, las llamadas ni ninguna otra función, y no "
            "genera daños adicionales. Una excelente opción si priorizas precio y "
            "te sientes cómodo usando contraseña."
        ),
        "alias": [
            "SFID", "SIN FACE ID", "SIN FACEID", "FACE ID NO FUNCIONA",
            "FALLA FACE ID",
        ],
    },
    {
        "codigo": "STT",
        "titulo": "SIN TRUE TONE",
        "resumen": "Pantalla sin True Tone.",
        "texto": (
            "Pantalla sin True Tone. True Tone es la función que ajusta "
            "automáticamente la temperatura de color de la pantalla según la luz "
            "del ambiente. En este equipo no está disponible, pero la pantalla se ve "
            "y funciona perfectamente; simplemente mantiene un tono de color fijo. "
            "Puedes ajustar el brillo y activar Night Shift sin problema. Es un "
            "detalle de confort visual, no un daño."
        ),
        "alias": [
            "STT", "SIN TRUE TONE", "TRUE TONE NO FUNCIONA", "FALLA TRUE TONE",
        ],
    },
    {
        "codigo": "MD",
        "titulo": "MANCHA EN DISPLAY",
        "resumen": "Pequeña mancha en la pantalla.",
        "texto": (
            "Pequeña mancha en la pantalla. La pantalla presenta una mancha o zona "
            "con tono diferente, visible principalmente en fondos blancos o de un "
            "solo color. El táctil y la visualización funcionan correctamente y el "
            "uso diario (videos, redes, apps) no se ve afectado. Es un detalle "
            "visual que te contamos de frente para que no haya sorpresas."
        ),
        "alias": [
            "MD", "MANCHA EN DISPLAY", "MANCHA EN PANTALLA", "MANCHA DISPLAY",
            "MANCHA PANTALLA",
        ],
    },
    {
        "codigo": "FAUD",
        "titulo": "FALLA EN AUDIO",
        "resumen": "Detalle en el audio.",
        "texto": (
            "Detalle en el audio. Uno de los altavoces (auricular de llamadas o "
            "parlante) puede tener menor volumen o sonar con leve distorsión. Puedes "
            "usar sin problema audífonos, Bluetooth, AirPods o el altavoz alterno "
            "según el caso. El resto del equipo funciona al 100% y el detalle no "
            "causa daños en otros componentes. Precio ajustado por total "
            "transparencia."
        ),
        "alias": [
            "FAUD", "FALLA EN AUDIO", "FALLA AUDIO", "AUDIO CON FALLA",
            "PARLANTE CON FALLA",
        ],
    },
    {
        "codigo": "FMIC",
        "titulo": "FALLA MICROFONO",
        "resumen": "Detalle en el micrófono.",
        "texto": (
            "Detalle en el micrófono. Uno de los micrófonos puede presentar fallas "
            "en ciertas funciones (por ejemplo, en notas de voz, video o manos "
            "libres), mientras que otros usos pueden funcionar con normalidad. Se "
            "soluciona fácilmente usando audífonos o accesorios Bluetooth con "
            "micrófono. No afecta el resto del iPhone ni genera daños mayores. Te lo "
            "informamos claro para que decidas con seguridad."
        ),
        "alias": [
            "FMIC", "FALLA MICROFONO", "FALLA MICRÓFONO", "MICROFONO CON FALLA",
            "MICRÓFONO CON FALLA",
        ],
    },
    {
        "codigo": "TCTL",
        "titulo": "FALLA TACTIL",
        "resumen": "Detalle en el táctil.",
        "texto": (
            "Detalle en el táctil. La pantalla puede presentar una zona con "
            "respuesta menos precisa o momentánea al toque. El equipo es "
            "completamente utilizable para el día a día y el resto de funciones "
            "operan con normalidad. Es un detalle localizado que no compromete los "
            "componentes internos. Lo comunicamos con transparencia para que sepas "
            "exactamente cómo se comporta el equipo."
        ),
        "alias": [
            "TCTL", "FALLA TACTIL", "FALLA TÁCTIL", "TACTIL CON FALLA",
            "TÁCTIL CON FALLA",
        ],
    },
    {
        "codigo": "TAPA",
        "titulo": "TAPA",
        "resumen": "Detalle en la tapa trasera.",
        "texto": (
            "Detalle en la tapa trasera. La tapa trasera de este iPhone presenta un "
            "detalle (desgaste, marca o reemplazo no original). Es un aspecto "
            "estético que no afecta la carga, la cámara ni el rendimiento del "
            "equipo, y con un estuche queda totalmente cubierto. Un iPhone funcional "
            "a un precio más conveniente."
        ),
        "alias": ["TAPA", "TAPA TRASERA", "CARCASA TRASERA", "TAPA CON DETALLE"],
    },
    {
        "codigo": "PXL",
        "titulo": "PIXEL",
        "resumen": "Pixel con detalle en la pantalla.",
        "texto": (
            "Pixel con detalle en la pantalla. La pantalla tiene uno o pocos pixeles "
            "que no cambian de color (pueden verse como un punto fijo), visibles "
            "sobre todo en fondos de un solo color. No afecta el táctil, el brillo ni "
            "el uso normal, y es un detalle puntual que no compromete el equipo. Te "
            "lo mostramos claro para que compres sin sorpresas."
        ),
        "alias": ["PXL", "PIXEL", "PIXELES", "PÍXELES", "PIXEL DAÑADO", "PIXEL CON DETALLE"],
    },
    {
        "codigo": "ADP",
        "titulo": "AVISO DE DISPLAY",
        "resumen": "Aviso de pantalla en Ajustes.",
        "texto": (
            "Aviso de pantalla en Ajustes. Este iPhone muestra en Ajustes un mensaje "
            "indicando que no se pudo verificar que la pantalla sea original de "
            "Apple. Es una notificación informativa del sistema: la pantalla funciona "
            "correctamente y el equipo opera con normalidad. Algunas funciones como "
            "True Tone o la calibración de brillo podrían variar. No representa un "
            "daño ni afecta el resto del teléfono."
        ),
        "alias": [
            "ADP", "AVISO DE DISPLAY", "AVISO PANTALLA", "PANTALLA NO ORIGINAL",
            "DISPLAY NO ORIGINAL",
        ],
    },
    {
        "codigo": "PF",
        "titulo": "PANTALLA FANTASMA",
        "resumen": "Pantalla con leve efecto fantasma.",
        "texto": (
            "Pantalla con leve efecto fantasma. Por el uso, la pantalla puede "
            "mostrar pequeños detalles o sombras tenues, visibles principalmente en "
            "fondos claros o de un solo color. En el uso diario (videos, redes, "
            "apps) casi no se percibe, y el táctil, el brillo y la visualización "
            "funcionan con normalidad. Es un detalle visual propio del uso que no "
            "daña el equipo ni afecta otros componentes. Te lo contamos claro para "
            "que compres sin sorpresas."
        ),
        "alias": ["PF", "PANTALLA FANTASMA", "EFECTO FANTASMA", "GHOSTING"],
    },
    {
        "codigo": "FISICO",
        "titulo": "DETALLE FISICO",
        "resumen": "Detalle físico.",
        "texto": (
            "El equipo presenta un detalle físico. Te lo contamos para que compres "
            "con claridad."
        ),
        "alias": ["FISICO", "FÍSICO", "DETALLE FISICO", "DETALLE FÍSICO"],
    },
    {
        "codigo": "BURBUJA",
        "titulo": "BURBUJA EN VISOR",
        "resumen": "Burbujas en el visor.",
        "texto": (
            "El visor presenta burbujas. Es un detalle visual; te lo mostramos para "
            "que compres con claridad."
        ),
        "alias": [
            "BURBUJA", "BURBUJAS", "BURBUJA EN VISOR", "BURBUJAS EN VISOR",
        ],
    },
]


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _compacto(texto: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", _normalizar(texto))


for _entry in CATALOGO:
    _entry["_alias_norm"] = [_normalizar(alias) for alias in _entry["alias"]]
    _entry["_alias_compact"] = [_compacto(alias) for alias in _entry["alias"]]


def _match_entry(entry, normalizada, compacta, tokens):
    """Devuelve (tipo, longitud) del mejor alias, o None. tipo: 3 exacto, 2 frase, 1 prefijo."""
    mejor = None
    for alias_n, alias_c in zip(entry["_alias_norm"], entry["_alias_compact"]):
        if not alias_n:
            continue

        if normalizada == alias_n or (compacta and compacta == alias_c):
            return (3, len(alias_n))

        if f" {alias_n} " in f" {normalizada} ":
            mejor = max(mejor or (2, 0), (2, len(alias_n)))

        if " " not in alias_n and len(alias_n) <= 3:
            for token in tokens:
                if token == alias_n:
                    mejor = max(mejor or (2, 0), (2, len(alias_n)))
                elif token.startswith(alias_n) and any(ch.isdigit() for ch in token):
                    mejor = max(mejor or (1, 0), (1, len(alias_n)))

    return mejor


def analizar_nota(nota: str) -> dict:
    """Traduce la nota a un resumen y a sus detalles comerciales."""
    texto = (nota or "").strip()
    if not texto:
        return {
            "match": "vacio",
            "codigos": [],
            "titulos": [],
            "resumen": "",
            "cliente": "",
            "detalles": [],
        }

    normalizada = _normalizar(texto)
    compacta = _compacto(texto)
    tokens = normalizada.split(" ") if normalizada else []

    encontrados = []
    for entry in CATALOGO:
        score = _match_entry(entry, normalizada, compacta, tokens)
        if score:
            encontrados.append((score, entry))

    encontrados.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)

    aproximado = False
    if not encontrados and len(normalizada) >= 6:
        mejor = None
        for entry in CATALOGO:
            for alias_n in entry["_alias_norm"]:
                if not alias_n:
                    continue
                ratio = difflib.SequenceMatcher(None, normalizada, alias_n).ratio()
                if ratio >= 0.86 and (mejor is None or ratio > mejor[0]):
                    mejor = (ratio, entry)
        if mejor:
            encontrados = [((1, len(normalizada)), mejor[1])]
            aproximado = True

    detalles = []
    vistos = set()
    for score, entry in encontrados:
        if entry["codigo"] in vistos:
            continue
        vistos.add(entry["codigo"])
        detalles.append({
            "codigo": entry["codigo"],
            "titulo": entry["titulo"],
            "resumen": entry["resumen"],
            "texto": entry["texto"],
        })

    if not detalles:
        return {
            "match": "sin_match",
            "codigos": [],
            "titulos": [],
            "resumen": texto,
            "cliente": texto,
            "detalles": [],
        }

    resumen = " ".join(d["resumen"] for d in detalles)
    if len(detalles) > 1:
        match = "multiple"
    elif aproximado:
        match = "aproximado"
    else:
        match = "exacto"

    return {
        "match": match,
        "codigos": [d["codigo"] for d in detalles],
        "titulos": [d["titulo"] for d in detalles],
        "resumen": resumen,
        "cliente": detalles[0]["texto"],
        "detalles": detalles,
    }
