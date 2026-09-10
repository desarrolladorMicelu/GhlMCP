# OFIMA Celulares — MCP Server

Servidor MCP que expone dos herramientas para consultar en tiempo real el inventario y el plan retoma de celulares desde OFIMA.

## Herramientas disponibles

### `consultar_stock`
Consulta los equipos disponibles en bodega: modelos, colores, condición, unidades y precio de venta.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `q` | string | Nombre o parte del nombre del equipo. Ej: `iphone 15`, `samsung s24`, `redmi note 13` |
| `solo_con_precio` | bool | Si `true`, filtra solo equipos con precio configurado. Default: `false` |

**Comportamiento de búsqueda:** usa `LIKE` parcial, así que `iphone 15` devuelve iPhone 15, 15 Plus, 15 Pro y 15 Pro Max — pero NO el 14 ni el 16.

**Respuesta:** lista de modelos agrupados, cada uno con `stock_total` y sus `variantes` (estado, color, unidades, bodegas, precio).

---

### `consultar_retoma`
Consulta cuánto recibe un cliente al entregar su equipo usado como parte de pago (plan retoma).

| Parámetro | Tipo | Descripción |
|---|---|---|
| `q` | string | Nombre o parte del nombre del equipo. Ej: `iphone 15`, `pixel 8`, `motorola edge 50` |

**Respuesta:** lista de modelos con sus variantes de condición (Seminuevo Grado A, Usado Grado B…) y el `precio_recibe` correspondiente.

---

## Variables de entorno

Copia `.env.example` a `.env` y completa los valores:

```env
# SQL Server — OFIMA (stock en tiempo real)
SQLSERVER_DRIVER=ODBC Driver 17 for SQL Server
SQLSERVER_HOST=<host>
SQLSERVER_DB=<base_de_datos>
SQLSERVER_USER=<usuario>
SQLSERVER_PASSWORD=<contraseña>

# PostgreSQL — Lista de precios retoma (Railway)
PRECIOS_DATABASE_URL=postgresql://user:pass@host:5432/db

# Puerto del servidor HTTP (Railway lo inyecta automáticamente)
PORT=8000
```

---

## Arranque

### HTTP (Railway / producción)
```bash
python mcp_server.py
```
El servidor queda disponible en `http://0.0.0.0:$PORT/mcp`.

### Stdio (integración local con Claude Desktop, Cursor, etc.)
```bash
fastmcp run mcp_server.py
```

---

## Estructura del proyecto

```
mcpOfima/
├── mcp_server.py      # Servidor MCP — herramientas consultar_stock y consultar_retoma
├── db_sqlserver.py    # Helper de conexión a SQL Server (OFIMA)
├── replicate.py       # Script de replicación OFIMA → PostgreSQL
├── requirements.txt
├── Dockerfile
├── Procfile
└── .env
```
