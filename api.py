"""
REST API — OFIMA Celulares
Expone inventario, plan retoma y promociones.

GET  /stock                  → stock en bodegas (SQL Server OFIMA)
GET  /retoma                 → precios de retoma (PostgreSQL Railway)
GET  /promociones            → promociones cargadas (PostgreSQL Railway)
POST /promociones/importar   → reemplaza las promociones desde CSV/XLSX
GET  /promociones/plantilla  → descarga Plantilla.csv
GET  /promociones/ui         → interfaz web simple para importar y listar

Autenticación: header  X-API-Key: <API_REST_KEY>
               o query  ?key=<API_REST_KEY>

Arranque standalone:  uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
import logging
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Security, Query, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

from db_sqlserver import get_sqlserver_conn, rows_to_dicts
import busqueda
import promociones

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Auth ──────────────────────────────────────────────────────────────────────

API_KEY_NAME   = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def verify_api_key(
    header_key: Optional[str] = Security(api_key_header),
    key:        Optional[str] = Query(default=None, include_in_schema=False),
):
    expected = os.environ.get("API_REST_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_REST_KEY no configurada en el servidor.")
    token = header_key or key
    if not token or token != expected:
        raise HTTPException(status_code=403, detail="API Key inválida.")
    return token


# ── Conexión retoma ───────────────────────────────────────────────────────────

def get_precios_conn():
    url = (
        os.environ.get("PRECIOS_DATABASE_URL")
        or os.environ.get("POSTGRESQLCONNSTR_BASE_LISTA_PRECIOS")
    )
    if not url:
        raise HTTPException(
            status_code=500,
            detail="PRECIOS_DATABASE_URL no configurada.",
        )
    return psycopg2.connect(url.strip(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Constantes stock ──────────────────────────────────────────────────────────

BODEGAS_STOCK = ("BM", "TM", "TB", "BB", "BCAL", "BNQS")

ESTADOS = {
    "NU": "Nuevo",
    "A":  "Grado A",
    "B":  "Grado B",
    "C":  "Grado C",
    "D":  "Grado D",
}

COLORES = {
    "NG":  "Negro",
    "BL":  "Blanco",
    "AZ":  "Azul",
    "RS":  "Rosa",
    "VE":  "Verde",
    "DO":  "Dorado",
    "LI":  "Lila/Morado",
    "AM":  "Amarillo",
    "RO":  "Rojo",
    "GR":  "Gris",
    "TTN": "Titanio Natural",
    "TTB": "Titanio Blanco",
    "TTK": "Titanio Negro",
    "0":   "Sin color",
}

CLASIFICACION_NOMBRES = {
    "A":  "Seminuevo Grado A",
    "A1": "Seminuevo Grado A1",
    "B":  "Usado Grado B",
    "B1": "Usado Grado B1",
    "C":  "Usado Grado C",
    "NU": "Nuevo",
}

# ── SQL stock (todos los registros, sin filtro de nombre) ─────────────────────

STOCK_SQL = """
    SELECT
        RTRIM(MvPrecio.[CODPRODUC])         AS codigo,
        RTRIM(MvPrecio.[CODPRECIO])         AS clasificacion,
        MvPrecio.[PRECIO]                   AS precio,
        RTRIM(MtMercia.[DESCRIPCIO])        AS descripcion,
        RTRIM(MtMercia.[CLASIFICA2])        AS marca,
        RTRIM(MtMercia.[CODLINEA])          AS categoria_general,
        RTRIM(MtMercia.[CODSBLIN])          AS categoria_especifica,
        MtMercia.[HABILITADO]               AS habilitado,
        RTRIM(MtMercia.[UBICACION])         AS ano_salida,
        RTRIM(ts.[XCOLOR])                  AS color,
        COUNT(s.[SERIE])                    AS stock,
        STRING_AGG(RTRIM(s.[BODEGA]), ', ') AS bodegas
    FROM
        MvPrecio
    INNER JOIN
        MtMercia ON RTRIM(MvPrecio.[CODPRODUC]) = RTRIM(MtMercia.[CODIGO])
    LEFT JOIN
        MTSERIES s WITH (NOLOCK) ON RTRIM(s.[CODIGO]) = RTRIM(MvPrecio.[CODPRODUC])
                                  AND s.[EXISTE] = 1
                                  AND s.[BODEGA] IN ('BM','TM','TB','BB','BCAL','BNQS')
    LEFT JOIN
        XMYCT_TECNICO_SERIES ts WITH (NOLOCK) ON s.[SERIE] = ts.[XSERIE]
                                              AND RTRIM(ts.[XESTADO]) = RTRIM(MvPrecio.[CODPRECIO])
    WHERE
        RTRIM(MtMercia.[CODSBLIN]) = 'SMPH'
        AND MvPrecio.[PRECIO] > 0
    GROUP BY
        RTRIM(MvPrecio.[CODPRODUC]),
        RTRIM(MvPrecio.[CODPRECIO]),
        MvPrecio.[PRECIO],
        RTRIM(MtMercia.[DESCRIPCIO]),
        RTRIM(MtMercia.[CLASIFICA2]),
        RTRIM(MtMercia.[CODLINEA]),
        RTRIM(MtMercia.[CODSBLIN]),
        MtMercia.[HABILITADO],
        RTRIM(MtMercia.[UBICACION]),
        RTRIM(ts.[XCOLOR])
    ORDER BY
        RTRIM(MtMercia.[DESCRIPCIO]), RTRIM(MvPrecio.[CODPRECIO])
"""

def _formato_cop(valor) -> str:
    """Formatea 2350000 como $2.350.000 (sin redondear centavos reales)."""
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if texto.endswith(",00"):
        texto = texto[:-3]
    return "$" + texto


def _promos_para(codigo: str, descripcion: str, promos: list[dict]) -> list[dict]:
    """
    Busca promociones para un producto de stock.

    Prioridad:
      1. SKU igual al código OFIMA (normalizado).
      2. Nombre igual a la descripción OFIMA (normalizado).
      3. Nombre específico (3+ palabras) contenido en la descripción.
    """
    codigo_n = busqueda.normalizar(codigo)
    descripcion_n = busqueda.normalizar(descripcion)
    descripcion_tokens = set(busqueda.tokenizar(descripcion))

    candidatas = []
    for promo in promos:
        sku_n = busqueda.normalizar(promo["sku"])
        nombre_n = busqueda.normalizar(promo["nombre"])
        nombre_tokens = set(busqueda.tokenizar(promo["nombre"]))

        if sku_n and codigo_n and sku_n == codigo_n:
            prioridad = 0
        elif nombre_n and descripcion_n and nombre_n == descripcion_n:
            prioridad = 1
        elif len(nombre_tokens) >= 3 and nombre_tokens.issubset(descripcion_tokens):
            prioridad = 2
        else:
            continue

        candidatas.append((prioridad, -len(nombre_tokens), promo))

    candidatas.sort(key=lambda item: (item[0], item[1]))
    return [promo for _, _, promo in candidatas]


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OFIMA Celulares API",
    description="Expone inventario, precios de retoma y promociones de Micelu.",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── GET /stock ────────────────────────────────────────────────────────────────

@app.get(
    "/stock",
    tags=["Stock"],
    summary="Stock disponible en bodegas (con búsqueda)",
    description=(
        "Devuelve los equipos con stock en las bodegas de OFIMA, agrupados por "
        "código + estado + color, con precio de venta, unidades y bodegas.\n\n"
        "Búsqueda con el parámetro `q` (opcional): todos los términos deben aparecer "
        "como palabras completas e insensible a mayúsculas/acentos.\n"
        "- `q=iphone 15` -> iPhone 15, 15 Plus, 15 Pro y 15 Pro Max (no el 14 ni el 16)\n"
        "- `q=iphone 16` -> solo la familia 16\n"
        "- `q=iphone pro max` -> solo los Pro Max\n"
        "- `q=iphone` -> toda la familia iPhone\n"
        "Sin `q` devuelve todo el stock.\n\n"
        "Cada variante incluye `en_promo` y `promociones` cuando el equipo tiene una "
        "promoción cargada. Las promociones que no tienen stock se devuelven en "
        "`promociones_sin_stock`."
    ),
)
def get_stock(
    q: Optional[str] = Query(
        default=None,
        description="Nombre o parte del nombre del equipo. Ej: 'iphone 15', 'iphone pro max'.",
    ),
    incluir_promociones: bool = Query(
        default=True,
        description="Cruza el stock con las promociones cargadas.",
    ),
    key: str = Security(verify_api_key),
):
    conn = get_sqlserver_conn()
    cur  = conn.cursor()
    try:
        cur.execute(STOCK_SQL)
        raw = rows_to_dicts(cur)
    except Exception as e:
        logging.error(f"[/stock] {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando stock: {e}")
    finally:
        cur.close()
        conn.close()

    result = []
    for row in raw:
        bodegas_raw    = row.get("bodegas") or ""
        bodegas_unicas = sorted(set(b.strip() for b in bodegas_raw.split(",") if b.strip()))
        clasificacion  = (row["clasificacion"] or "").strip() or "0"
        color          = (row["color"]         or "").strip() or "0"
        result.append({
            "codigo":        (row["codigo"]      or "").strip() or "0",
            "descripcion":   (row["descripcion"] or "").strip() or "0",
            "estado":        clasificacion,
            "estado_nombre": CLASIFICACION_NOMBRES.get(clasificacion, clasificacion),
            "color":         color,
            "color_nombre":  COLORES.get(color, color),
            "stock":         row["stock"] if row["stock"] is not None else 0,
            "bodegas":       bodegas_unicas,
            "precio":        float(row["precio"]) if row["precio"] is not None else 0,
        })

    promos: list[dict] = []
    if incluir_promociones:
        try:
            promos = promociones.listar(usar_cache=True)
        except Exception as e:
            logging.warning(f"[/stock] No se pudieron cargar promociones: {e}")

    cache: dict[str, list[dict]] = {}
    promos_match: set[str] = set()
    for row in result:
        codigo = row["codigo"]
        if codigo not in cache:
            cache[codigo] = _promos_para(codigo, row["descripcion"], promos)
        promos_match.update(promo["sku"] for promo in cache[codigo])

    result = busqueda.filtrar(result, q, lambda r: r["descripcion"])

    for row in result:
        coincidencias = cache.get(row["codigo"], [])
        row["en_promo"] = bool(coincidencias)
        row["promociones"] = coincidencias
        row["promo_mensaje"] = None
        if coincidencias:
            promo = coincidencias[0]
            row["promo_mensaje"] = (
                f'Está en promo: antes {_formato_cop(promo["valor_antes"])} '
                f'y ahora {_formato_cop(promo["valor_descuento"])}.'
            )

    promos_sin_stock: list[dict] = []
    if q and promos:
        terminos = busqueda.tokenizar(q)
        for promo in promos:
            if not terminos or promo["sku"] in promos_match:
                continue
            if not busqueda.coincide(f'{promo["sku"]} {promo["nombre"]}', terminos):
                continue
            promos_sin_stock.append({**promo, "en_stock": False})

    promociones_vigentes: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for row in result:
        if not row.get("en_promo"):
            continue
        if row.get("estado") not in ("NU", "A") or (row.get("stock") or 0) <= 0:
            continue
        for promo in row["promociones"]:
            clave = (row["codigo"], promo["sku"])
            if clave in vistos:
                continue
            vistos.add(clave)
            promociones_vigentes.append({
                "codigo": row["codigo"],
                "descripcion": row["descripcion"],
                "sku": promo["sku"],
                "nombre": promo["nombre"],
                "valor_antes": promo["valor_antes"],
                "valor_descuento": promo["valor_descuento"],
                "mensaje": (
                    f'{promo["nombre"]} está en promo: antes '
                    f'{_formato_cop(promo["valor_antes"])} y ahora '
                    f'{_formato_cop(promo["valor_descuento"])}.'
                ),
            })

    disponible = bool(result)
    respuesta = {
        "query":                 q or None,
        "disponible":            disponible,
        "total_variantes":       len(result),
        "total_unidades":        sum(row["stock"] for row in result),
        "total_promociones":     len(promociones_vigentes) + len(promos_sin_stock),
        "data":                  result,
        "promociones_vigentes":  promociones_vigentes,
        "resumen_promociones":   " ".join(p["mensaje"] for p in promociones_vigentes) or None,
        "promociones_sin_stock": promos_sin_stock,
    }
    if not disponible and promos_sin_stock:
        respuesta["mensaje"] = (
            f"No hay stock en bodegas para '{q}', pero hay promociones registradas."
        )
    elif not disponible and q:
        respuesta["mensaje"] = (
            f"No hay stock ni promociones para '{q}'. Verifica el nombre del equipo."
        )

    return respuesta


# ── GET /retoma ───────────────────────────────────────────────────────────────

@app.get(
    "/retoma",
    tags=["Retoma"],
    summary="Precios de recepción del plan retoma (con búsqueda)",
    description=(
        "Devuelve los equipos habilitados con precio de recepción configurado "
        "(plan retoma), con nombre, marca, categoría, año de salida, condición y "
        "cuánto recibe el cliente al entregar el equipo como parte de pago.\n\n"
        "Búsqueda con el parámetro `q` (opcional): todos los términos deben aparecer "
        "como palabras completas e insensible a mayúsculas/acentos (busca en nombre "
        "y marca).\n"
        "- `q=iphone 15` -> iPhone 15, 15 Plus, 15 Pro y 15 Pro Max (no el 14 ni el 16)\n"
        "- `q=iphone pro max` -> solo los Pro Max\n"
        "- `q=iphone` -> toda la familia iPhone\n"
        "Sin `q` devuelve todos los precios de retoma."
    ),
)
def get_retoma(
    q: Optional[str] = Query(
        default=None,
        description="Nombre o parte del nombre del equipo. Ej: 'iphone 15', 'iphone pro max'.",
    ),
    key: str = Security(verify_api_key),
):
    conn = get_precios_conn()
    cur  = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                "NOMBRE_LARGO"              AS nombre,
                "MARCA"                     AS marca,
                "CATEGORIA_ESPECIFICA"      AS categoria,
                "CLASIFICACION_SEMI"        AS clasificacion,
                "PRECIO_RECIBE"             AS precio_recibe,
                TRIM("ANO_SALIDA")          AS ano_salida
            FROM producto
            WHERE "HABILITADO" = true
              AND "PRECIO_RECIBE" IS NOT NULL
              AND "PRECIO_RECIBE" != 0
            ORDER BY TRIM("ANO_SALIDA") DESC, "NOMBRE_LARGO", "CLASIFICACION_SEMI"
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"[/retoma] {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando retoma: {e}")
    finally:
        cur.close()
        conn.close()

    result = []
    for row in rows:
        clf = (row["clasificacion"] or "").strip()
        result.append({
            "nombre":                 row["nombre"],
            "marca":                  row["marca"],
            "categoria":              row["categoria"],
            "ano_salida":             row["ano_salida"],
            "clasificacion":          clf,
            "clasificacion_nombre":   CLASIFICACION_NOMBRES.get(clf, clf),
            "precio_recibe":          row["precio_recibe"],
        })

    result = busqueda.filtrar(result, q, lambda r: f'{r["nombre"]} {r["marca"] or ""}')

    return {
        "query": q or None,
        "total": len(result),
        "data":  result,
    }


# ── Promociones ───────────────────────────────────────────────────────────────

def _html_promociones() -> str:
    ruta = Path(__file__).with_name("promociones_ui.html")
    if not ruta.exists():
        raise HTTPException(status_code=500, detail="promociones_ui.html no encontrado.")
    return ruta.read_text(encoding="utf-8")


async def _importar_archivo(file: UploadFile) -> dict:
    try:
        contenido = await file.read()
        productos = promociones.parsear(file.filename or "", contenido)
        total = promociones.reemplazar(productos)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logging.error(f"[promociones/importar] {e}")
        raise HTTPException(status_code=500, detail=f"Error importando promociones: {e}")

    return {
        "ok": True,
        "total": total,
        "data": [
            {
                "sku": p["sku"],
                "nombre": p["nombre"],
                "valor_antes": float(p["valor_antes"]),
                "valor_descuento": float(p["valor_descuento"]),
            }
            for p in productos
        ],
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/promociones/ui", response_class=HTMLResponse, include_in_schema=False)
def interfaz_promociones():
    return HTMLResponse(_html_promociones())


@app.get("/promociones/plantilla", include_in_schema=False)
def descargar_plantilla():
    ruta = Path(__file__).with_name("Plantilla.csv")
    if not ruta.exists():
        raise HTTPException(status_code=404, detail="Plantilla.csv no encontrada.")
    return FileResponse(ruta, media_type="text/csv", filename="Plantilla.csv")


@app.get(
    "/promociones",
    tags=["Promociones"],
    summary="Promociones cargadas",
    description=(
        "Devuelve las promociones guardadas (SKU, nombre, valor antes y valor descuento). "
        "Búsqueda opcional con `q`: todos los términos deben aparecer como palabras "
        "completas en el SKU o el nombre."
    ),
)
def get_promociones(
    q: Optional[str] = Query(
        default=None,
        description="Nombre, SKU o parte de ellos. Ej: 'iphone 15'.",
    ),
    key: str = Security(verify_api_key),
):
    try:
        data = promociones.listar()
    except Exception as e:
        logging.error(f"[/promociones] {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando promociones: {e}")

    data = busqueda.filtrar(data, q, lambda r: f'{r["sku"]} {r["nombre"]}')

    return {
        "query": q or None,
        "total": len(data),
        "data":  data,
    }


@app.post(
    "/promociones/importar",
    tags=["Promociones"],
    summary="Reemplaza las promociones desde CSV/XLSX",
    description=(
        "Recibe un archivo con encabezado SKU ;NOMBRE ;VALOR ANTES ;VALOR DESCUENTO. "
        "Borra todas las promociones actuales y guarda las del archivo en una sola "
        "transacción."
    ),
)
async def importar_promociones(
    file: UploadFile = File(
        ...,
        description="Archivo CSV o XLSX con columnas SKU;NOMBRE;VALOR ANTES;VALOR DESCUENTO.",
    ),
    key: str = Security(verify_api_key),
):
    return await _importar_archivo(file)


@app.get("/promociones/ui/lista", include_in_schema=False)
def ui_lista_promociones():
    try:
        data = promociones.listar()
    except Exception as e:
        logging.error(f"[/promociones/ui/lista] {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando promociones: {e}")
    return {"total": len(data), "data": data}


@app.post("/promociones/ui/importar", include_in_schema=False)
async def ui_importar_promociones(file: UploadFile = File(...)):
    return await _importar_archivo(file)


# ── Entrypoint standalone ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
