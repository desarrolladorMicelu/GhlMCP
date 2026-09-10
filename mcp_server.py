"""
MCP Server — OFIMA Celulares
Expone dos herramientas para que una IA consulte en tiempo real:
  • consultar_stock   → disponibilidad de equipos en bodega (SQL Server OFIMA)
  • consultar_retoma  → precio de recepción plan retoma (PostgreSQL Railway)

Arranque:
  python mcp_server.py          (HTTP, puerto $PORT o 8000)
  fastmcp run mcp_server.py     (stdio, para integraciones locales)
"""

import os
import json
import logging
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastmcp import FastMCP
from db_sqlserver import get_sqlserver_conn, rows_to_dicts

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

mcp = FastMCP(
    "OFIMA Celulares",
    instructions=(
        "Servidor MCP para consultar inventario y plan retoma de celulares en OFIMA. "
        "Usa 'consultar_stock' para saber si un equipo está disponible en bodega con su precio de venta. "
        "Usa 'consultar_retoma' para saber cuánto recibe un cliente al entregar su equipo usado como parte de pago. "
        "En ambas herramientas el parámetro 'q' es el nombre o parte del nombre del dispositivo, "
        "por ejemplo: 'iphone 15', 'samsung s24', 'redmi note 13'. "
        "El servidor hace búsqueda parcial (LIKE), así que 'iphone 15' devuelve iPhone 15, 15 Plus, 15 Pro y 15 Pro Max."
    ),
)

# ── Constantes ────────────────────────────────────────────────────────────────

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


# ── Helpers internos ──────────────────────────────────────────────────────────

def _get_precios_conn():
    """Conexión a la BD de lista de precios de retoma (PostgreSQL Railway)."""
    url = (
        os.environ.get("PRECIOS_DATABASE_URL")
        or os.environ.get("POSTGRESQLCONNSTR_BASE_LISTA_PRECIOS")
    )
    if not url:
        raise RuntimeError(
            "Variable de entorno PRECIOS_DATABASE_URL no configurada."
        )
    return psycopg2.connect(url.strip(), cursor_factory=psycopg2.extras.RealDictCursor)


def _run_stock_query(q: str, solo_con_precio: bool) -> list[dict]:
    """Ejecuta la consulta de stock en SQL Server y devuelve lista de variantes."""
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
    finally:
        cur.close()
        conn.close()

    result = []
    for row in raw:
        bodegas_raw = row.get("bodegas") or ""
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
    return result


def _group_by_product(rows: list[dict]) -> list[dict]:
    """Agrupa variantes (estado + color) bajo cada producto."""
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


# ── Herramientas MCP ──────────────────────────────────────────────────────────

@mcp.tool
def consultar_stock(q: str, solo_con_precio: bool = False) -> str:
    """
    Consulta el stock de celulares disponibles en las bodegas de OFIMA.

    Usa esta herramienta para saber si un equipo está disponible, en qué bodega,
    qué colores hay, en qué condición (nuevo, grado A, B…) y cuál es su precio de venta.

    La búsqueda es parcial e insensible a mayúsculas: si envías "iphone 15" devolverá
    iPhone 15, iPhone 15 Plus, iPhone 15 Pro y iPhone 15 Pro Max — pero NO el 14 ni el 16.
    Si quieres solo el modelo exacto, sé más específico: "iphone 15 pro max".

    Args:
        q: Nombre o parte del nombre del equipo a buscar.
           Ejemplos: "iphone 15", "samsung s24", "redmi note 13", "a55", "motorola edge 50"
        solo_con_precio: Si es True, solo devuelve equipos que tienen precio configurado.
                         Por defecto False (devuelve todo el stock aunque no tenga precio).

    Returns:
        JSON con los equipos encontrados agrupados por modelo. Cada modelo incluye:
        - descripcion: nombre completo del equipo
        - stock_total: total de unidades disponibles en todas las bodegas
        - variantes: lista de variantes con estado (Nuevo, Grado A, B…), color,
                     unidades, bodegas donde está y precio de venta
        Si no hay stock, devuelve disponible=false con un mensaje.
    """
    q = q.strip()
    if not q:
        return json.dumps({"error": "El parámetro 'q' no puede estar vacío."}, ensure_ascii=False)

    try:
        rows = _run_stock_query(q, solo_con_precio=solo_con_precio)
    except Exception as e:
        logging.error(f"[consultar_stock] Error: {e}")
        return json.dumps({"error": f"Error consultando stock: {e}"}, ensure_ascii=False)

    if not rows:
        return json.dumps({
            "disponible": False,
            "query": q,
            "mensaje": f"No hay stock disponible para '{q}'. Verifica el nombre del equipo.",
            "productos": [],
        }, ensure_ascii=False)

    productos = _group_by_product(rows)
    return json.dumps({
        "disponible":      True,
        "query":           q,
        "total_modelos":   len(productos),
        "total_unidades":  sum(p["stock_total"] for p in productos),
        "productos":       productos,
    }, ensure_ascii=False)


@mcp.tool
def consultar_retoma(q: str) -> str:
    """
    Consulta el precio de recepción del plan retoma: cuánto recibe un cliente al
    entregar su equipo usado como parte de pago al comprar un equipo nuevo.

    Usa esta herramienta cuando el cliente quiera saber cuánto le pagan por su celular,
    o cuando quiera usarlo como abono al comprar un equipo.

    La búsqueda es parcial e insensible a mayúsculas: "iphone 15" devuelve
    iPhone 15, iPhone 15 Plus, iPhone 15 Pro y iPhone 15 Pro Max — pero NO el 14 ni el 16.
    Para mayor precisión usa nombres más específicos: "iphone 15 pro max".

    Args:
        q: Nombre o parte del nombre del equipo a buscar.
           Ejemplos: "iphone 15", "samsung s24", "redmi note 13", "pixel 8", "motorola edge 50"

    Returns:
        JSON con los modelos encontrados. Cada modelo incluye:
        - nombre: nombre completo del equipo
        - marca, categoria, ano_salida
        - variantes: lista de condiciones del equipo (Seminuevo Grado A, Usado Grado B…)
                     con su precio_recibe (lo que el cliente recibe por entregar el equipo)
        Si no hay resultados, devuelve total_modelos=0.
    """
    q = q.strip()
    if not q:
        return json.dumps({"error": "El parámetro 'q' no puede estar vacío."}, ensure_ascii=False)

    try:
        conn = _get_precios_conn()
    except RuntimeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                "NOMBRE_LARGO"                      AS nombre,
                "MARCA"                             AS marca,
                "CATEGORIA_ESPECIFICA"              AS categoria,
                "CLASIFICACION_SEMI"                AS clasificacion,
                "PRECIO_RECIBE"                     AS precio_recibe,
                TRIM("ANO_SALIDA")                  AS ano_salida
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
        logging.error(f"[consultar_retoma] Error: {e}")
        return json.dumps({"error": f"Error consultando retoma: {e}"}, ensure_ascii=False)
    finally:
        cur.close()
        conn.close()

    if not rows:
        return json.dumps({
            "query":           q,
            "total_modelos":   0,
            "total_variantes": 0,
            "mensaje":         f"No se encontraron precios de retoma para '{q}'. Verifica el nombre del equipo.",
            "modelos":         [],
        }, ensure_ascii=False)

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

    return json.dumps({
        "query":           q,
        "total_modelos":   len(modelos),
        "total_variantes": len(rows),
        "modelos":         list(modelos.values()),
    }, ensure_ascii=False)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    port = int(os.environ.get("PORT", 8000))

    mcp_asgi = mcp.http_app(path="/")
    app = Starlette(
        lifespan=mcp_asgi.lifespan,
        routes=[Mount("/mcp", app=mcp_asgi)],
    )

    logging.getLogger("uvicorn").info(
        f"MCP Server iniciado → http://0.0.0.0:{port}/mcp"
    )
    uvicorn.run(app, host="0.0.0.0", port=port)
