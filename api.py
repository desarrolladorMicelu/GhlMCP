"""
API REST — OFIMA Data
Expone los mismos datos del MCP como endpoints HTTP convencionales.
Autenticación: API Key en header  X-API-Key: <API_REST_KEY>

Rutas (prefijo /api agregado por el Mount en mcp_server.py):
  GET /tables                          → lista de tablas
  GET /tables/{tabla}                  → describe columnas
  GET /tables/{tabla}/rows             → filas con ?limit=&filters=
  GET /tables/{tabla}/count            → conteo con ?filters=
  POST /query                          → SELECT libre (body JSON)

  GET /mvtrade                         → shortcut mvtrade
  GET /mtmercia                        → shortcut mtmercia
  GET /vseriesutilidad                 → shortcut vseriesutilidad
  GET /mvcuadre                        → shortcut mvcuadre
  GET /vabonos                         → shortcut vabonos
  GET /abocxp                          → shortcut abocxp
  GET /vcxp                            → shortcut vcxp

Standalone (uvicorn api:api):  http://localhost:8000/docs
Montado en mcp_server.py:      http://localhost:8000/api/docs
"""

import os
import json
from datetime import date, datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Security, Query
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from db_sqlserver import get_sqlserver_conn, rows_to_dicts


def get_precios_conn():
    """Conexión a la BD de lista de precios de retoma (Railway)."""
    url = (
        os.environ.get("PRECIOS_DATABASE_URL")
        or os.environ.get("POSTGRESQLCONNSTR_BASE_LISTA_PRECIOS")
    )
    if not url:
        raise HTTPException(
            status_code=500,
            detail="Configura PRECIOS_DATABASE_URL o POSTGRESQLCONNSTR_BASE_LISTA_PRECIOS en el .env",
        )
    return psycopg2.connect(url.strip(), cursor_factory=psycopg2.extras.RealDictCursor)

# ── Constantes ────────────────────────────────────────────────────────────────

PG_SCHEMA = "backups"

AVAILABLE_TABLES = [
    "mvtrade",
    "mtmercia",
    "vseriesutilidad",
    "mvcuadre",
    "vabonos",
    "abocxp",
    "vcxp",
]

# ── Auth ──────────────────────────────────────────────────────────────────────

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def verify_api_key(
    header_key: Optional[str] = Security(api_key_header),
    api_key: Optional[str] = Query(default=None, include_in_schema=False),
):
    expected = os.environ.get("API_REST_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_REST_KEY no configurada en el servidor.")
    token = header_key or api_key
    if not token or token != expected:
        raise HTTPException(status_code=403, detail="API Key inválida.")
    return token


# ── DB helper ─────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def serialize_rows(rows: list) -> list:
    """Convierte tipos Python no-JSON (date, datetime, Decimal) a string/iso."""
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if isinstance(v, (date, datetime)):
                clean[k] = v.isoformat()
            else:
                clean[k] = v
        result.append(clean)
    return result


# ── App ───────────────────────────────────────────────────────────────────────

api = FastAPI(
    title="OFIMA REST API",
    description="Consulta las tablas OFIMA replicadas en PostgreSQL.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ── Modelos ───────────────────────────────────────────────────────────────────

class CustomQueryBody(BaseModel):
    sql: str


# ── Endpoints genéricos ───────────────────────────────────────────────────────

@api.get("/tables", tags=["Meta"], summary="Lista las tablas disponibles")
def list_tables(key: str = Security(verify_api_key)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = ANY(%s)
        ORDER BY table_name
        """,
        (PG_SCHEMA, AVAILABLE_TABLES),
    )
    rows = [r["table_name"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {"tables": rows}


@api.get("/tables/{table_name}", tags=["Meta"], summary="Describe columnas de una tabla")
def describe_table(table_name: str, key: str = Security(verify_api_key)):
    if table_name not in AVAILABLE_TABLES:
        raise HTTPException(status_code=404, detail=f"Tabla '{table_name}' no disponible.")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position
        """,
        (PG_SCHEMA, table_name),
    )
    cols = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {"table": table_name, "columns": cols}


@api.get("/tables/{table_name}/rows", tags=["Consultas"], summary="Filas de una tabla")
def query_table(
    table_name: str,
    limit: int = Query(default=100, ge=1, le=1000, description="Máximo de filas (1-1000)"),
    filters: Optional[str] = Query(default=None, description="Condición SQL WHERE, ej: nit='900123'"),
    key: str = Security(verify_api_key),
):
    if table_name not in AVAILABLE_TABLES:
        raise HTTPException(status_code=404, detail=f"Tabla '{table_name}' no disponible.")

    where = f"WHERE {filters}" if filters else ""
    sql = f'SELECT * FROM "{PG_SCHEMA}"."{table_name}" {where} LIMIT {limit}'

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = serialize_rows([dict(r) for r in cur.fetchall()])
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    cur.close()
    conn.close()
    return {"table": table_name, "count": len(rows), "rows": rows}


@api.get("/tables/{table_name}/count", tags=["Consultas"], summary="Conteo de filas")
def count_rows(
    table_name: str,
    filters: Optional[str] = Query(default=None, description="Condición SQL WHERE"),
    key: str = Security(verify_api_key),
):
    if table_name not in AVAILABLE_TABLES:
        raise HTTPException(status_code=404, detail=f"Tabla '{table_name}' no disponible.")

    where = f"WHERE {filters}" if filters else ""
    sql = f'SELECT COUNT(*) AS total FROM "{PG_SCHEMA}"."{table_name}" {where}'

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        result = dict(cur.fetchone())
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    cur.close()
    conn.close()
    return {"table": table_name, "total": result["total"]}


@api.post("/query", tags=["Consultas"], summary="SELECT libre sobre cualquier tabla")
def run_custom_query(body: CustomQueryBody, key: str = Security(verify_api_key)):
    sql_clean = body.sql.strip().upper()
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE"]
    if not sql_clean.startswith("SELECT"):
        raise HTTPException(status_code=400, detail="Solo se permiten consultas SELECT.")
    for word in forbidden:
        if word in sql_clean:
            raise HTTPException(status_code=400, detail=f"Operación '{word}' no permitida.")

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(body.sql)
        rows = serialize_rows([dict(r) for r in cur.fetchall()])
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    cur.close()
    conn.close()
    return {"count": len(rows), "rows": rows}


# ── Shortcuts por tabla ───────────────────────────────────────────────────────

def _shortcut(table_name: str, limit: int, filters: Optional[str]):
    where = f"WHERE {filters}" if filters else ""
    sql = f'SELECT * FROM "{PG_SCHEMA}"."{table_name}" {where} LIMIT {limit}'
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = serialize_rows([dict(r) for r in cur.fetchall()])
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    cur.close()
    conn.close()
    return {"table": table_name, "count": len(rows), "rows": rows}


@api.get(
    "/mvtrade",
    tags=["Tablas"],
    summary="Movimientos de venta/compra",
    description="Columnas: tipodcto, nrodcto, nit, nombre, producto, vendedor, bodega, fecha, fhcompra, cantidad, vlrventa, costo, descuento, iva, passwordin, origen",
)
def get_mvtrade(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: vendedor='V01' AND fhcompra > '2026-01-01'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("mvtrade", limit, filters)


@api.get(
    "/mtmercia",
    tags=["Tablas"],
    summary="Catálogo de productos",
    description="Columnas: codigo, descripcio, codlinea, codsblin, codgrupo, clasifica1, clasifica2, iva, habilitado, ubicacion, unidadmed, tipoinv",
)
def get_mtmercia(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: habilitado=1 AND codlinea='CEL'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("mtmercia", limit, filters)


@api.get(
    "/vseriesutilidad",
    tags=["Tablas"],
    summary="Series/IMEI con utilidad",
    description="Columnas: producto, serie, referencia, tipo_documento, fecha_inicial, nit, valor, documento",
)
def get_vseriesutilidad(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: nit='900123456'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("vseriesutilidad", limit, filters)


@api.get(
    "/mvcuadre",
    tags=["Tablas"],
    summary="Cuadre de caja / medios de pago",
    description="Columnas: fecha, tipodcto, dcto, documento, mediopag, banco, bancodest, valor, nit, passwordin, origen, tipodctofa",
)
def get_mvcuadre(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: mediopag='EFECTIVO'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("mvcuadre", limit, filters)


@api.get(
    "/vabonos",
    tags=["Tablas"],
    summary="Abonos recibidos (CxC)",
    description="Columnas: tipodcto, dcto, documento, fecha, nit, tipodctoca, valor, banco, concepto, nota, passwordin",
)
def get_vabonos(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: nit='900123456' AND valor > 100000"),
    key: str = Security(verify_api_key),
):
    return _shortcut("vabonos", limit, filters)


@api.get(
    "/abocxp",
    tags=["Tablas"],
    summary="Abonos cuentas por pagar (CxP)",
    description="Columnas: tipodcto, dcto, documento, fecha, nit, beneficia, banco, bancotras, tipodctocp, formapago, valor, passwordin, nota, concepto",
)
def get_abocxp(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: formapago='TRANSFERENCIA'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("abocxp", limit, filters)


@api.get(
    "/vcxp",
    tags=["Tablas"],
    summary="Cuentas por pagar",
    description="Columnas: tipodcto, nrodcto, fecha, fhvencim, nit, clinombre, bruto, descuento, ivabruto, deuda, pagado, mediopag, passwordin, origen, ciudad, canal",
)
def get_vcxp(
    limit: int = Query(100, ge=1, le=1000),
    filters: Optional[str] = Query(None, description="ej: deuda > 0 AND fhvencim < '2026-12-31'"),
    key: str = Security(verify_api_key),
):
    return _shortcut("vcxp", limit, filters)




# ── Stock en tiempo real (SQL Server directo) ─────────────────────────────────

BODEGAS_STOCK = ("BM", "TM", "TB", "BB", "BCAL", "BNQS")

STOCK_QUERY = """
    SELECT
        RTRIM(s.CODIGO)                                    AS codigo,
        RTRIM(m.DESCRIPCIO)                                AS descripcion,
        RTRIM(ts.XESTADO)                                  AS estado,
        RTRIM(ts.XCOLOR)                                   AS color,
        COUNT(s.SERIE)                                     AS stock,
        STRING_AGG(RTRIM(s.BODEGA), ', ')                  AS bodegas,
        (
            SELECT TOP 1 p.PRECIO
            FROM MvPrecio p WITH (NOLOCK)
            WHERE RTRIM(p.CODPRODUC) = RTRIM(s.CODIGO)
              AND RTRIM(p.CODPRECIO) = RTRIM(ts.XESTADO)
        )                                                  AS precio
    FROM MTSERIES s WITH (NOLOCK)
    INNER JOIN XMYCT_TECNICO_SERIES ts WITH (NOLOCK)
           ON s.SERIE = ts.XSERIE
    LEFT JOIN MtMercia m WITH (NOLOCK)
           ON RTRIM(m.CODIGO) = RTRIM(s.CODIGO)
    WHERE s.EXISTE = 1
      AND s.BODEGA IN ({bodegas_ph})
      AND UPPER(m.DESCRIPCIO) LIKE UPPER(?)
    GROUP BY RTRIM(s.CODIGO), RTRIM(m.DESCRIPCIO), RTRIM(ts.XESTADO), RTRIM(ts.XCOLOR)
    {having}
    ORDER BY RTRIM(m.DESCRIPCIO), RTRIM(ts.XESTADO), RTRIM(ts.XCOLOR)
"""

# Nombres legibles para estados y colores
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
    "TTN": "Titanio Negro",
    "0":   "Sin color",
}


def _run_stock_query(q: str, solo_con_precio: bool) -> list[dict]:
    """Ejecuta la query de stock y devuelve lista de dicts limpia y ordenada."""
    bodegas_ph = ", ".join(["?"] * len(BODEGAS_STOCK))
    having = (
        "HAVING (SELECT TOP 1 PRECIO FROM MvPrecio WITH (NOLOCK) "
        "WHERE RTRIM(CODPRODUC) = RTRIM(s.CODIGO) "
        "AND RTRIM(CODPRECIO) = RTRIM(ts.XESTADO)) > 0"
        if solo_con_precio else ""
    )

    sql = STOCK_QUERY.format(bodegas_ph=bodegas_ph, having=having)
    params = list(BODEGAS_STOCK) + [f"%{q}%"]

    conn = get_sqlserver_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        raw = rows_to_dicts(cur)
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Error consultando stock: {e}")
    cur.close()
    conn.close()

    # Enriquecer con nombres legibles y limpiar bodegas duplicadas
    result = []
    for row in raw:
        bodegas_raw = row.get("bodegas") or ""
        bodegas_unicas = sorted(set(b.strip() for b in bodegas_raw.split(",") if b.strip()))
        result.append({
            "codigo":      row["codigo"],
            "descripcion": row["descripcion"] or row["codigo"],
            "estado":      row["estado"],
            "estado_nombre": ESTADOS.get(row["estado"], row["estado"]),
            "color":       row["color"],
            "color_nombre": COLORES.get(row["color"], row["color"]),
            "stock":       row["stock"],
            "bodegas":     bodegas_unicas,
            "precio":      float(row["precio"]) if row["precio"] is not None else None,
        })
    return result


def _group_by_product(rows: list[dict]) -> list[dict]:
    """Agrupa las variantes (estado+color) bajo cada producto."""
    products: dict[str, dict] = {}
    for row in rows:
        key = row["codigo"]
        if key not in products:
            products[key] = {
                "codigo":      row["codigo"],
                "descripcion": row["descripcion"],
                "stock_total": 0,
                "variantes":   [],
            }
        products[key]["stock_total"] += row["stock"]
        products[key]["variantes"].append({
            "estado":        row["estado"],
            "estado_nombre": row["estado_nombre"],
            "color":         row["color"],
            "color_nombre":  row["color_nombre"],
            "stock":         row["stock"],
            "bodegas":       row["bodegas"],
            "precio":        row["precio"],
        })
    return list(products.values())


@api.get(
    "/stock",
    tags=["Stock"],
    summary="Busca productos por nombre y devuelve stock disponible",
    description=(
        "Busca en el catálogo de productos por nombre (case-insensitive). "
        "Retorna todas las variantes disponibles (estado + color) con stock, bodegas y precio. "
        "Ejemplo: `q=iphone 15` devuelve iPhone 15, 15 Plus, 15 Pro — pero NO el 14 ni el 16."
    ),
)
def get_stock(
    q: str = Query(..., min_length=2, description="Nombre o parte del nombre del producto, ej: iphone 15"),
    solo_con_precio: bool = Query(False, description="Si es true, excluye productos sin precio configurado"),
    agrupar: bool = Query(True, description="Si es true, agrupa variantes bajo cada producto"),
    key: str = Security(verify_api_key),
):
    rows = _run_stock_query(q, solo_con_precio)

    if not rows:
        return {"query": q, "total_productos": 0, "total_unidades": 0, "productos": []}

    if agrupar:
        productos = _group_by_product(rows)
    else:
        productos = rows

    total_unidades = sum(r["stock"] for r in rows)

    return {
        "query":           q,
        "total_productos": len(productos) if agrupar else len(set(r["codigo"] for r in rows)),
        "total_unidades":  total_unidades,
        "productos":       productos,
    }


@api.get(
    "/stock/con-precio",
    tags=["Stock"],
    summary="Igual que /stock pero solo productos con precio configurado",
    description="Shortcut de /stock?solo_con_precio=true. Excluye variantes sin precio.",
)
def get_stock_con_precio(
    q: str = Query(..., min_length=2, description="Nombre o parte del nombre del producto"),
    agrupar: bool = Query(True, description="Si es true, agrupa variantes bajo cada producto"),
    key: str = Security(verify_api_key),
):
    rows = _run_stock_query(q, solo_con_precio=True)

    if not rows:
        return {"query": q, "total_productos": 0, "total_unidades": 0, "productos": []}

    if agrupar:
        productos = _group_by_product(rows)
    else:
        productos = rows

    total_unidades = sum(r["stock"] for r in rows)

    return {
        "query":           q,
        "total_productos": len(productos) if agrupar else len(set(r["codigo"] for r in rows)),
        "total_unidades":  total_unidades,
        "productos":       productos,
    }



# ── Precios de retoma (BD Railway lista de precios) ───────────────────────────

CLASIFICACION_NOMBRES = {
    "A":  "Seminuevo Grado A",
    "A1": "Seminuevo Grado A1",
    "B":  "Usado Grado B",
    "B1": "Usado Grado B1",
    "C":  "Usado Grado C",
    "NU": "Nuevo",
}


@api.get(
    "/retoma",
    tags=["Retoma"],
    summary="Precio de recepción para plan retoma (dar equipo como método de pago)",
    description=(
        "Busca cuánto recibe un cliente al entregar su equipo como parte de pago. "
        "Busca por nombre del dispositivo (case-insensitive). "
        "Devuelve todas las variantes de condición (Seminuevo A, Usado B, etc.) con su precio de recepción. "
        "Ejemplo: `q=iphone 15` devuelve iPhone 15, 15 Plus, 15 Pro — pero NO el 14 ni el 16."
    ),
)
def get_retoma(
    q: str = Query(..., min_length=2, description="Nombre o parte del nombre, ej: iphone 15, samsung s24"),
    key: str = Security(verify_api_key),
):
    conn = get_precios_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                "NOMBRE_LARGO"                          AS nombre,
                "MARCA"                                 AS marca,
                "CATEGORIA_ESPECIFICA"                  AS categoria,
                "CLASIFICACION_SEMI"                    AS clasificacion,
                "PRECIO_RECIBE"                         AS precio_recibe,
                TRIM("ANO_SALIDA")                      AS ano_salida
            FROM producto
            WHERE "HABILITADO" = true
              AND "PRECIO_RECIBE" IS NOT NULL
              AND "PRECIO_RECIBE" != 0
              AND UPPER("NOMBRE_LARGO") LIKE UPPER(%s)
            ORDER BY TRIM("ANO_SALIDA") DESC, "NOMBRE_LARGO", "CLASIFICACION_SEMI"
            """,
            (f"%{q}%",),
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Error consultando precios de retoma: {e}")
    cur.close()
    conn.close()

    if not rows:
        return {
            "query": q,
            "total_modelos": 0,
            "total_variantes": 0,
            "modelos": [],
        }

    # Agrupar variantes bajo cada nombre de producto
    modelos: dict[str, dict] = {}
    for row in rows:
        nombre = row["nombre"]
        if nombre not in modelos:
            modelos[nombre] = {
                "nombre":     nombre,
                "marca":      row["marca"],
                "categoria":  row["categoria"],
                "ano_salida": row["ano_salida"],
                "variantes":  [],
            }
        clf = (row["clasificacion"] or "").strip()
        modelos[nombre]["variantes"].append({
            "clasificacion":        clf,
            "clasificacion_nombre": CLASIFICACION_NOMBRES.get(clf, clf),
            "precio_recibe":        row["precio_recibe"],
        })

    return {
        "query":           q,
        "total_modelos":   len(modelos),
        "total_variantes": len(rows),
        "modelos":         list(modelos.values()),
    }


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv()
    uvicorn.run("api:api", host="0.0.0.0", port=8000, reload=True)
