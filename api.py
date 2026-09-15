"""
REST API — OFIMA Celulares
Dos endpoints que exponen el inventario completo y el plan retoma completo.

GET /stock        → todo el stock en bodegas (SQL Server OFIMA)
GET /retoma       → todos los precios de retoma (PostgreSQL Railway)

Autenticación: header  X-API-Key: <API_REST_KEY>
               o query  ?key=<API_REST_KEY>

Arranque standalone:  uvicorn api:app --host 0.0.0.0 --port 8000
"""

import os
import logging
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Security, Query
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

from db_sqlserver import get_sqlserver_conn, rows_to_dicts

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
        RTRIM(s.CODIGO)                                     AS codigo,
        RTRIM(m.DESCRIPCIO)                                 AS descripcion,
        RTRIM(ts.XESTADO)                                   AS estado,
        RTRIM(ts.XCOLOR)                                    AS color,
        COUNT(s.SERIE)                                      AS stock,
        STRING_AGG(RTRIM(s.BODEGA), ', ')                   AS bodegas,
        (
            SELECT TOP 1 p.PRECIO
            FROM MvPrecio p WITH (NOLOCK)
            WHERE RTRIM(p.CODPRODUC) = RTRIM(s.CODIGO)
              AND RTRIM(p.CODPRECIO) = RTRIM(ts.XESTADO)
        )                                                   AS precio
    FROM MTSERIES s WITH (NOLOCK)
    INNER JOIN XMYCT_TECNICO_SERIES ts WITH (NOLOCK)
           ON s.SERIE = ts.XSERIE
    LEFT JOIN MtMercia m WITH (NOLOCK)
           ON RTRIM(m.CODIGO) = RTRIM(s.CODIGO)
    WHERE s.EXISTE = 1
      AND s.BODEGA IN ({bodegas_ph})
    GROUP BY RTRIM(s.CODIGO), RTRIM(m.DESCRIPCIO), RTRIM(ts.XESTADO), RTRIM(ts.XCOLOR)
    ORDER BY RTRIM(m.DESCRIPCIO), RTRIM(ts.XESTADO), RTRIM(ts.XCOLOR)
"""

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OFIMA Celulares API",
    description="Expone el inventario completo y los precios de retoma de Micelu.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── GET /stock ────────────────────────────────────────────────────────────────

@app.get(
    "/stock",
    tags=["Stock"],
    summary="Todo el stock disponible en bodegas",
    description=(
        "Devuelve todos los equipos con stock en las bodegas de OFIMA, "
        "agrupados por código + estado + color. Incluye precio de venta, "
        "cantidad de unidades y bodegas donde se encuentra cada variante."
    ),
)
def get_stock(key: str = Security(verify_api_key)):
    bodegas_ph = ", ".join(["?"] * len(BODEGAS_STOCK))
    sql = STOCK_SQL.format(bodegas_ph=bodegas_ph)

    conn = get_sqlserver_conn()
    cur  = conn.cursor()
    try:
        cur.execute(sql, list(BODEGAS_STOCK))
        raw = rows_to_dicts(cur)
    except Exception as e:
        logging.error(f"[/stock] {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando stock: {e}")
    finally:
        cur.close()
        conn.close()

    result = []
    for row in raw:
        bodegas_raw   = row.get("bodegas") or ""
        bodegas_unicas = sorted(set(b.strip() for b in bodegas_raw.split(",") if b.strip()))
        result.append({
            "codigo":        row["codigo"],
            "descripcion":   row["descripcion"] or row["codigo"],
            "estado":        row["estado"],
            "estado_nombre": ESTADOS.get(row["estado"], row["estado"]),
            "color":         row["color"],
            "color_nombre":  COLORES.get(row["color"], row["color"]),
            "stock":         row["stock"],
            "bodegas":       bodegas_unicas,
            "precio":        float(row["precio"]) if row["precio"] is not None else None,
        })

    return {
        "total_variantes": len(result),
        "data": result,
    }


# ── GET /retoma ───────────────────────────────────────────────────────────────

@app.get(
    "/retoma",
    tags=["Retoma"],
    summary="Todos los precios de recepción del plan retoma",
    description=(
        "Devuelve todos los equipos habilitados con precio de recepción configurado "
        "(plan retoma). Incluye nombre, marca, categoría, año de salida, condición "
        "y cuánto recibe el cliente al entregar el equipo como parte de pago."
    ),
)
def get_retoma(key: str = Security(verify_api_key)):
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

    return {
        "total": len(result),
        "data":  result,
    }


# ── Entrypoint standalone ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
