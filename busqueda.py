"""
Búsqueda de productos por tokens para los endpoints /stock y /retoma.

Diseñada para que una IA busque de forma precisa:
  - "iphone 15"         -> iPhone 15, 15 Plus, 15 Pro y 15 Pro Max (NO el 14 ni el 16)
  - "iphone 15 pro"     -> solo 15 Pro (NO el 15 Pro Max)
  - "iphone 15 pro max" -> solo 15 Pro Max
  - "iphone 16"         -> solo la familia 16
  - "iphone"            -> toda la familia iPhone

Reglas:
  * Normaliza: mayúsculas, sin acentos, separa letras de números ("15PRO" -> "15 PRO").
  * Todos los términos de la consulta deben aparecer como palabras completas.
    Así "15" NO hace match con "150" ni con "1500".
  * Si la consulta pide una variante (Pro, Max, Plus, Ultra, Mini), no se mezcla
    con otra: "17 pro" no devuelve "17 pro max".
"""

import re
import unicodedata

_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_SPACES_RE = re.compile(r"\s+")

_STORAGE_UNIT_RE = re.compile(r"(?<=\d)\s*(?:GB|TB)\b", re.IGNORECASE)
_STANDALONE_UNIT_RE = re.compile(r"\b(?:GB|TB)\b", re.IGNORECASE)
_CONDITION_RE = re.compile(
    r"\b(?:NUEVO|NUEVA|SEMINUEVO|SEMINUEVA|USADO|USADA|GANGAZO)\b",
    re.IGNORECASE,
)

_VARIANTES_SUFIJO = {"MAX", "PLUS", "ULTRA", "MINI"}
_VARIANTES = {"PRO"} | _VARIANTES_SUFIJO


def limpiar_consulta(texto: str) -> str:
    """
    Limpia la consulta para buscar por modelo.

    - Quita la unidad de capacidad pero conserva el número:
      "iphone 14 pro max 256 gb" -> "iphone 14 pro max 256"
    - Quita la condición (nuevo/seminuevo/usado) porque se filtra con `estado`:
      "iphone 17 seminuevo" -> "iphone 17"
    """
    if not texto:
        return ""
    t = _STORAGE_UNIT_RE.sub("", str(texto))
    t = _STANDALONE_UNIT_RE.sub(" ", t)
    t = _CONDITION_RE.sub(" ", t)
    return _SPACES_RE.sub(" ", t).strip()


def normalizar(texto: str) -> str:
    """Mayúsculas, sin acentos, sin signos, con separación letra/número."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.upper()
    t = _NON_ALNUM_RE.sub(" ", t)
    t = _BOUNDARY_RE.sub(" ", t)
    return _SPACES_RE.sub(" ", t).strip()


def tokenizar(texto: str) -> list[str]:
    """Convierte un texto en su lista de tokens normalizados."""
    n = normalizar(texto)
    return n.split(" ") if n else []


def coincide(texto: str, terminos: list[str]) -> bool:
    """
    True si TODOS los términos aparecen como tokens completos del texto.

    Además respeta las variantes del modelo para que "17 pro" no haga match con
    "17 pro max", pero "17" sí devuelva toda la familia:

    - "iphone 17 pro"     -> solo 17 Pro (no Pro Max)
    - "iphone 17 pro max" -> solo 17 Pro Max
    - "iphone 17"         -> 17, 17 Pro y 17 Pro Max
    """
    if not terminos:
        return True

    tokens = set(tokenizar(texto))
    if not all(t in tokens for t in terminos):
        return False

    variantes_query = set(terminos) & _VARIANTES
    if not variantes_query:
        return True

    sufijos_query = variantes_query & _VARIANTES_SUFIJO
    sufijos_producto = tokens & _VARIANTES_SUFIJO
    return not (sufijos_producto - sufijos_query)


def filtrar(items, texto, get_nombre):
    """
    Filtra `items` quedándose con los que contienen todos los términos de `texto`.

    Args:
        items: iterable de elementos.
        texto: consulta del usuario (ej. "iphone 15"). Vacía o None devuelve todo.
        get_nombre: callable que extrae el texto buscable de cada item.
    """
    terminos = tokenizar(texto)
    if not terminos:
        return list(items)
    return [item for item in items if coincide(get_nombre(item), terminos)]
