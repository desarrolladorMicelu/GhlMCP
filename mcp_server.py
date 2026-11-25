"""
MCP Server — OFIMA Celulares (proxy a la REST API)

Expone dos herramientas:
  • consultar_stock   -> GET /stock
  • consultar_retoma  -> GET /retoma

No consulta bases de datos directamente: reutiliza la REST API para devolver
exactamente el mismo JSON que /stock y /retoma, incluidas promociones y gangazos.

Arranque local:
    python mcp_server.py
    (la REST API debe estar en API_BASE_URL, por defecto http://127.0.0.1:8000)

En Railway se despliega como un servicio aparte con:
    python mcp_server.py
y variables:
    API_BASE_URL=https://<servicio-rest>.up.railway.app
    API_REST_KEY=<la misma del API>
"""

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

API_BASE_URL = (
    os.environ.get("API_BASE_URL")
    or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
).rstrip("/")
API_REST_KEY = os.environ.get("API_REST_KEY", "")
TIMEOUT = float(os.environ.get("MCP_API_TIMEOUT", "60"))

mcp = FastMCP(
    "OFIMA Celulares MCP",
    instructions=(
        "Servidor MCP para consultar stock, promociones, gangazos y retoma de "
        "Micelu. Usa 'consultar_stock' para precio de venta, disponibilidad, "
        "promociones y gangazos. Usa 'consultar_retoma' para precio de recepción "
        "o parte de pago. En ambas herramientas el parámetro 'q' debe ser solo "
        "el modelo (ej: 'iphone 15 pro max'), sin capacidad, color ni estado. "
        "La respuesta incluye `data` con precios y, cuando aplica, `en_promo`, "
        "`promo_mensaje`, `es_gangazo`, `nota_resumen` y `gangazo_mensaje`."
    ),
)


def _get(path: str, q: str) -> str:
    if not API_REST_KEY:
        return json.dumps(
            {"error": "API_REST_KEY no configurada en el servidor MCP."},
            ensure_ascii=False,
        )

    params = {"q": q}
    url = f"{API_BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"X-API-Key": API_REST_KEY, "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", errors="replace")
        return json.dumps(
            {"error": f"HTTP {e.code} consultando {path}", "detalle": detalle},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"error": f"No se pudo consultar {path}: {e}"},
            ensure_ascii=False,
        )


@mcp.tool
async def consultar_stock(q: str) -> str:
    """
    Consulta stock, precio de venta, promociones y gangazos de un modelo.

    Args:
        q: Modelo o parte del modelo. Ej: "iphone 15 pro max", "samsung s24".
           No incluyas capacidad, color ni estado.
    """
    return await asyncio.to_thread(_get, "/stock", q)


@mcp.tool
async def consultar_retoma(q: str) -> str:
    """
    Consulta el precio de recepción/retoma/parte de pago de un equipo usado.

    Args:
        q: Modelo o parte del modelo. Ej: "iphone 15 pro max", "pixel 8".
           No incluyas capacidad, color ni estado.
    """
    return await asyncio.to_thread(_get, "/retoma", q)


if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount

    port = int(os.environ.get("PORT", 8001))
    mcp_asgi = mcp.http_app(path="/")
    app = Starlette(
        lifespan=mcp_asgi.lifespan,
        routes=[Mount("/mcp", app=mcp_asgi)],
    )
    uvicorn.run(app, host="0.0.0.0", port=port)
