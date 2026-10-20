"""
Promociones: lectura de CSV/XLSX, persistencia y consulta en PostgreSQL.

Formato obligatorio del archivo (la primera fila es el encabezado):
    SKU ;NOMBRE ;VALOR ANTES ;VALOR DESCUENTO

La carga es un reemplazo total: se borran las promociones anteriores y se
insertan las del archivo dentro de la misma transacción.
"""

import csv
import io
import os
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras

TABLA = "public.promociones"
COLUMNAS = ("SKU", "NOMBRE", "VALOR ANTES", "VALOR DESCUENTO")

_CACHE_TTL = 60.0
_cache: list[dict] | None = None
_cache_ts = 0.0

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {TABLA} (
    id              BIGSERIAL PRIMARY KEY,
    sku             TEXT NOT NULL UNIQUE,
    nombre          TEXT NOT NULL,
    valor_antes     NUMERIC(14,2) NOT NULL,
    valor_descuento NUMERIC(14,2) NOT NULL,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


# ── Conexión ──────────────────────────────────────────────────────────────────

def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Variable de entorno DATABASE_URL no configurada.")
    return psycopg2.connect(url.strip(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Lectura y validación del archivo ──────────────────────────────────────────

def _normalizar_encabezado(valor) -> str:
    t = unicodedata.normalize("NFKD", str(valor or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.upper().replace("_", " ")
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _decodificar(contenido: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return contenido.decode(encoding)
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1", errors="replace")


def _filas_csv(texto: str) -> list[list[str]]:
    muestra = texto[:4096]
    delimitador = ";" if ";" in muestra else ","
    return [fila for fila in csv.reader(io.StringIO(texto), delimiter=delimitador)]


def _filas_xlsx(contenido: bytes) -> list[list[str]]:
    try:
        import openpyxl
    except ImportError:
        raise ValueError(
            "Para archivos .xlsx se requiere 'openpyxl'. "
            "Exporta la plantilla como CSV o instala la dependencia."
        )
    libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        hoja = libro.active
        return [
            ["" if valor is None else str(valor) for valor in fila]
            for fila in hoja.iter_rows(values_only=True)
        ]
    finally:
        libro.close()


def parsear_valor(valor):
    """Convierte '1.234.567,89', '$ 1,234,567.89', '1234567' etc. a Decimal."""
    if valor is None:
        return None
    texto = str(valor).strip().replace("\u00a0", " ")
    if not texto:
        return None
    texto = re.sub(r"[^0-9,.\-]", "", texto)
    if not texto or texto in ("-", ".", ","):
        return None

    negativo = texto.startswith("-")
    texto = texto.lstrip("-")

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        if len(partes) == 2 and 1 <= len(partes[1]) <= 2:
            texto = texto.replace(",", ".")
        else:
            texto = texto.replace(",", "")
    else:
        partes = texto.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) > 2):
            texto = texto.replace(".", "")

    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None

    return -numero if negativo else numero


def parsear(nombre_archivo: str, contenido: bytes) -> list[dict]:
    """Valida el archivo y devuelve la lista de productos lista para insertar."""
    extension = os.path.splitext(nombre_archivo or "")[1].lower()
    if extension in (".xlsx", ".xlsm"):
        filas = _filas_xlsx(contenido)
    else:
        filas = _filas_csv(_decodificar(contenido))

    filas = [fila for fila in filas if any(str(celda).strip() for celda in fila)]
    if not filas:
        raise ValueError("El archivo está vacío.")

    encabezado = tuple(_normalizar_encabezado(c) for c in filas[0][:4])
    if encabezado != COLUMNAS:
        esperado = " ;".join(COLUMNAS)
        recibido = " ;".join(str(c).strip() for c in filas[0][:4])
        raise ValueError(
            f"Encabezado inválido. Se esperaba: {esperado}. Se recibió: {recibido}."
        )
    if len(filas[0]) > 4 and any(str(c).strip() for c in filas[0][4:]):
        raise ValueError(
            "El archivo tiene columnas de más. Usa solo: SKU ;NOMBRE ;VALOR ANTES ;VALOR DESCUENTO."
        )

    productos: dict[str, dict] = {}
    for numero, fila in enumerate(filas[1:], start=2):
        fila = list(fila) + [""] * max(0, 4 - len(fila))
        sku = str(fila[0]).strip()
        nombre = str(fila[1]).strip()

        if not sku and not nombre and not str(fila[2]).strip() and not str(fila[3]).strip():
            continue
        if not sku:
            raise ValueError(f"Fila {numero}: el SKU está vacío.")
        if not nombre:
            raise ValueError(f"Fila {numero}: el NOMBRE está vacío.")

        valor_antes = parsear_valor(fila[2])
        valor_descuento = parsear_valor(fila[3])
        if valor_antes is None:
            raise ValueError(f"Fila {numero}: VALOR ANTES inválido ({fila[2]!r}).")
        if valor_descuento is None:
            raise ValueError(f"Fila {numero}: VALOR DESCUENTO inválido ({fila[3]!r}).")

        productos[sku.upper()] = {
            "sku": sku,
            "nombre": nombre,
            "valor_antes": valor_antes,
            "valor_descuento": valor_descuento,
        }

    if not productos:
        raise ValueError("El archivo no tiene productos para importar.")

    return list(productos.values())


# ── Persistencia ──────────────────────────────────────────────────────────────

def listar(usar_cache: bool = False) -> list[dict]:
    """
    Devuelve todas las promociones guardadas, en el orden de carga.

    `usar_cache=True` reutiliza el resultado hasta 60 s (para el cruce con /stock).
    La caché se invalida al importar.
    """
    global _cache, _cache_ts

    ahora = time.monotonic()
    if usar_cache and _cache is not None and (ahora - _cache_ts) < _CACHE_TTL:
        return [dict(promo) for promo in _cache]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(
                f"SELECT sku, nombre, valor_antes, valor_descuento "
                f"FROM {TABLA} ORDER BY id"
            )
            filas = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    data = [
        {
            "sku": fila["sku"],
            "nombre": fila["nombre"],
            "valor_antes": float(fila["valor_antes"]),
            "valor_descuento": float(fila["valor_descuento"]),
        }
        for fila in filas
    ]
    _cache = data
    _cache_ts = ahora
    return [dict(promo) for promo in data]


def reemplazar(productos: list[dict]) -> int:
    """Borra todas las promociones e inserta las nuevas en una sola transacción."""
    global _cache, _cache_ts

    if not productos:
        raise ValueError("No hay productos para guardar.")

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE)
                cur.execute(f"DELETE FROM {TABLA}")
                psycopg2.extras.execute_values(
                    cur,
                    f"INSERT INTO {TABLA} (sku, nombre, valor_antes, valor_descuento) VALUES %s",
                    [
                        (p["sku"], p["nombre"], p["valor_antes"], p["valor_descuento"])
                        for p in productos
                    ],
                )
    finally:
        conn.close()

    _cache = None
    _cache_ts = 0.0
    return len(productos)
