"""
Búsqueda de productos por tokens para los endpoints /stock y /retoma.

Diseñada para que una IA busque de forma precisa:
  - "iphone 15"       -> iPhone 15, 15 Plus, 15 Pro y 15 Pro Max (NO el 14 ni el 16)
  - "iphone 16"       -> solo la familia 16
  - "iphone pro max"  -> solo los Pro Max
  - "iphone"          -> toda la familia iPhone

Reglas:
  * Normaliza: mayúsculas, sin acentos, separa letras de números ("15PRO" -> "15 PRO").
  * Todos los términos de la consulta deben aparecer como palabras completas.
    Así "15" NO hace match con "150" ni con "1500".
"""

import re
import unicodedata

_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_SPACES_RE = re.compile(r"\s+")


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
    """True si TODOS los términos aparecen como tokens completos del texto."""
    if not terminos:
        return True
    tokens = set(tokenizar(texto))
    return all(t in tokens for t in terminos)


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
