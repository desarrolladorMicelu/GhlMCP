"""
Gangazos: equipos usados con defectos, cargados desde CSV/XLSX y persistidos en
PostgreSQL. Se integran en /stock como variantes especiales (`es_gangazo`).

Formato obligatorio (encabezado):
    Referencia.1 ;ESTAD ;NOMBRE ;Serie ;NOTA ;Baterí ;BODEG ;PRECIO ANTES ;PRECIO AHOR ;DESCUENTO

La carga es un reemplazo total: se borran los gangazos anteriores y se insertan
los del archivo dentro de la misma transacción.
"""

import os
import time

import psycopg2
import psycopg2.extras

from promociones import (
    _decodificar,
    _filas_csv,
    _filas_xlsx,
    _normalizar_encabezado,
    parsear_valor,
)

TABLA = "public.gangazos"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {TABLA} (
    id              BIGSERIAL PRIMARY KEY,
    referencia      TEXT NOT NULL,
    estado          TEXT,
    nombre          TEXT,
    serie           TEXT NOT NULL UNIQUE,
    nota            TEXT,
    bateria         TEXT,
    bodega          TEXT,
    precio_antes    NUMERIC(14,2) NOT NULL,
    precio_ahora    NUMERIC(14,2) NOT NULL,
    descuento       NUMERIC(14,2),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_CACHE_TTL = 60.0
_cache: list[dict] | None = None
_cache_ts = 0.0

_ALIASES = {
    "REFERENCIA": "referencia",
    "REFERENCIA 1": "referencia",
    "ESTAD": "estado",
    "ESTADO": "estado",
    "NOMBRE": "nombre",
    "COLOR": "nombre",
    "SERIE": "serie",
    "SERIAL": "serie",
    "NOTA": "nota",
    "NOTAS": "nota",
    "BATERI": "bateria",
    "BATERIA": "bateria",
    "BODEG": "bodega",
    "BODEGA": "bodega",
    "PRECIO ANTES": "precio_antes",
    "VALOR ANTES": "precio_antes",
    "PRECIO AHOR": "precio_ahora",
    "PRECIO AHORA": "precio_ahora",
    "VALOR AHORA": "precio_ahora",
    "DESCUENTO": "descuento",
}

_ENCABEZADO_ESPERADO = (
    "Referencia.1 ;ESTAD ;NOMBRE ;Serie ;NOTA ;Baterí ;BODEG ;"
    "PRECIO ANTES ;PRECIO AHOR ;DESCUENTO"
)


# ── Conexión ──────────────────────────────────────────────────────────────────

def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Variable de entorno DATABASE_URL no configurada.")
    return psycopg2.connect(url.strip(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Lectura y validación del archivo ──────────────────────────────────────────

def _leer_filas(nombre_archivo: str, contenido: bytes) -> list[list[str]]:
    extension = os.path.splitext(nombre_archivo or "")[1].lower()
    if extension in (".xlsx", ".xlsm"):
        return _filas_xlsx(contenido)
    return _filas_csv(_decodificar(contenido))


def parsear(nombre_archivo: str, contenido: bytes) -> list[dict]:
    """Valida el archivo y devuelve la lista de gangazos lista para insertar."""
    filas = _leer_filas(nombre_archivo, contenido)
    filas = [fila for fila in filas if any(str(celda).strip() for celda in fila)]
    if not filas:
        raise ValueError("El archivo está vacío.")

    columnas: dict[str, int] = {}
    for indice, encabezado in enumerate(filas[0]):
        campo = _ALIASES.get(_normalizar_encabezado(encabezado))
        if campo and campo not in columnas:
            columnas[campo] = indice

    requeridos = ("referencia", "serie", "precio_antes", "precio_ahora")
    faltantes = [campo for campo in requeridos if campo not in columnas]
    if faltantes:
        recibido = " ;".join(str(celda).strip() for celda in filas[0][:10])
        raise ValueError(
            f"Encabezado inválido. Se esperaba: {_ENCABEZADO_ESPERADO}. "
            f"Se recibió: {recibido}."
        )

    def valor(fila, campo):
        indice = columnas.get(campo)
        if indice is None or indice >= len(fila):
            return ""
        return str(fila[indice]).strip()

    productos: dict[str, dict] = {}
    for numero, fila in enumerate(filas[1:], start=2):
        if not any(str(celda).strip() for celda in fila):
            continue

        referencia = valor(fila, "referencia")
        serie = valor(fila, "serie")
        if not referencia:
            raise ValueError(f"Fila {numero}: la Referencia está vacía.")
        if not serie:
            raise ValueError(f"Fila {numero}: la Serie está vacía.")

        precio_antes = parsear_valor(valor(fila, "precio_antes"))
        precio_ahora = parsear_valor(valor(fila, "precio_ahora"))
        if precio_antes is None:
            raise ValueError(f"Fila {numero}: PRECIO ANTES inválido.")
        if precio_ahora is None:
            raise ValueError(f"Fila {numero}: PRECIO AHOR inválido.")

        descuento = parsear_valor(valor(fila, "descuento"))
        if descuento is None:
            descuento = precio_antes - precio_ahora

        productos[serie.upper()] = {
            "referencia": referencia,
            "estado": valor(fila, "estado") or "B",
            "nombre": valor(fila, "nombre"),
            "serie": serie,
            "nota": valor(fila, "nota"),
            "bateria": valor(fila, "bateria"),
            "bodega": valor(fila, "bodega"),
            "precio_antes": precio_antes,
            "precio_ahora": precio_ahora,
            "descuento": descuento,
        }

    if not productos:
        raise ValueError("El archivo no tiene gangazos para importar.")

    return list(productos.values())


# ── Persistencia ──────────────────────────────────────────────────────────────

def listar(usar_cache: bool = False) -> list[dict]:
    """Devuelve todos los gangazos, en el orden de carga."""
    global _cache, _cache_ts

    ahora = time.monotonic()
    if usar_cache and _cache is not None and (ahora - _cache_ts) < _CACHE_TTL:
        return [dict(g) for g in _cache]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE)
            cur.execute(
                f"SELECT referencia, estado, nombre, serie, nota, bateria, bodega, "
                f"precio_antes, precio_ahora, descuento FROM {TABLA} ORDER BY id"
            )
            filas = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    data = [
        {
            "referencia": fila["referencia"],
            "estado": fila["estado"],
            "nombre": fila["nombre"],
            "serie": fila["serie"],
            "nota": fila["nota"],
            "bateria": fila["bateria"],
            "bodega": fila["bodega"],
            "precio_antes": float(fila["precio_antes"]),
            "precio_ahora": float(fila["precio_ahora"]),
            "descuento": float(fila["descuento"]) if fila["descuento"] is not None else None,
        }
        for fila in filas
    ]
    _cache = data
    _cache_ts = ahora
    return [dict(g) for g in data]


def reemplazar(gangazos: list[dict]) -> int:
    """Borra todos los gangazos e inserta los nuevos en una sola transacción."""
    global _cache, _cache_ts

    if not gangazos:
        raise ValueError("No hay gangazos para guardar.")

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE)
                cur.execute(f"DELETE FROM {TABLA}")
                psycopg2.extras.execute_values(
                    cur,
                    f"INSERT INTO {TABLA} "
                    f"(referencia, estado, nombre, serie, nota, bateria, bodega, "
                    f"precio_antes, precio_ahora, descuento) VALUES %s",
                    [
                        (
                            g["referencia"],
                            g["estado"],
                            g["nombre"],
                            g["serie"],
                            g["nota"],
                            g["bateria"],
                            g["bodega"],
                            g["precio_antes"],
                            g["precio_ahora"],
                            g["descuento"],
                        )
                        for g in gangazos
                    ],
                )
    finally:
        conn.close()

    _cache = None
    _cache_ts = 0.0
    return len(gangazos)


def borrar() -> int:
    """Borra todos los gangazos y deja la tabla vacía."""
    global _cache, _cache_ts

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE)
                cur.execute(f"DELETE FROM {TABLA}")
                borrados = cur.rowcount
    finally:
        conn.close()

    _cache = None
    _cache_ts = 0.0
    return borrados
