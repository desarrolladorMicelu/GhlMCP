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

## API REST (`api.py`)

Autenticación con header `X-API-Key: <API_REST_KEY>` o query `?key=<API_REST_KEY>`.

| Endpoint | Descripción |
|---|---|
| `GET /stock?q=iphone 15` | Stock en bodegas (SQL Server OFIMA) |
| `GET /retoma?q=iphone 15` | Precios de retoma (PostgreSQL) |
| `GET /promociones?q=iphone` | Promociones cargadas |
| `POST /promociones/importar` | Reemplaza las promociones desde CSV/XLSX (multipart `file`) |
| `GET /promociones/plantilla` | Descarga `Plantilla.csv` |
| `GET /promociones/ui` | Interfaz web para importar y listar |
| `GET /gangazos?q=iphone 13 pro max` | Gangazos cargados |
| `POST /gangazos/importar` | Reemplaza los gangazos desde CSV/XLSX (multipart `file`) |
| `GET /gangazos/plantilla` | Descarga `Plantilla_gangazos.csv` |

`/stock` cruza el inventario con las promociones cargadas sin tocar la base de OFIMA.
Por defecto omite las variantes con `stock = 0` (usa `incluir_sin_stock=true` para
incluirlas). Cada variante trae `precio` como precio final a ofrecer; cuando hay
promoción, `precio_es_promocional` es `true`, `precio_antes` trae el valor anterior y
`promo_mensaje` la frase lista para el cliente. También se devuelven
`promociones_vigentes` y `resumen_promociones`. Las promociones sin unidades salen en
`promociones_sin_stock` cuando se busca con `q`. El cruce es por SKU (normalizado)
contra el código OFIMA, luego por nombre exacto y por nombre específico contenido en
la descripción.

`/stock` también agrega los gangazos que coinciden con `q` a `data`, con
`es_gangazo=true`, `nota` (el defecto), `bateria`, `serie`, `precio` (precio actual),
`precio_antes`, `descuento` y `gangazo_mensaje`. Además devuelve `total_gangazos`,
`gangazos_vigentes` y `resumen_gangazos`.

La búsqueda de `/stock` y `/retoma` ignora la unidad de capacidad y la condición:
`q=iphone 14 pro max 256 gb` se busca como `iphone 14 pro max 256`, y
`q=iphone 17 seminuevo` como `iphone 17`. Si el modelo envía solo el nombre, devuelve
todas las capacidades y la IA filtra la que pidió el cliente.

La búsqueda respeta las variantes: `q=iphone 17 pro` no devuelve el 17 Pro Max;
`q=iphone 17` sí devuelve toda la familia 17.

### Promociones

El archivo debe tener el encabezado exacto:

```csv
SKU ;NOMBRE ;VALOR ANTES ;VALOR DESCUENTO
```

Al importar se borran todas las promociones anteriores y se insertan las nuevas en
una sola transacción. Se aceptan `.csv` (separador `;` o `,`) y `.xlsx`. Los valores
se interpretan como pesos: `1.234.567,89`, `$ 1,234,567.89` y `1234567` son válidos.

La tabla `public.promociones` se crea automáticamente en la base `DATABASE_URL`.
La interfaz incluye un botón **Borrar todo** que vacía la tabla sin importar un archivo.

### Gangazos

El archivo debe tener el encabezado:

```csv
Referencia.1 ;ESTAD ;NOMBRE ;Serie ;NOTA ;Baterí ;BODEG ;PRECIO ANTES ;PRECIO AHOR ;DESCUENTO
```

Al importar se borran todos los gangazos anteriores y se insertan los nuevos en una
sola transacción. Se aceptan `.csv` y `.xlsx`. La `Serie` es única; si se repite en el
archivo, queda la última fila. La tabla `public.gangazos` se crea automáticamente.

Cada gangazo aparece en `/stock` cuando su referencia/color coincide con `q`. El
registro se marca con `es_gangazo: true`, `estado: "G"` y `estado_nombre: "Gangazo"`
(el `estado_original` del archivo queda en `estado_original`). La IA debe explicar
siempre el defecto de `nota`. La interfaz incluye un botón **Borrar todo** que vacía
la tabla de gangazos.

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
├── mcp_server.py        # Servidor MCP — consultar_stock y consultar_retoma
├── api.py               # REST API — stock, retoma, promociones y gangazos + UI
├── busqueda.py          # Búsqueda por tokens (palabras completas)
├── promociones.py       # Carga CSV/XLSX y persistencia en PostgreSQL
├── gangazos.py          # Carga CSV/XLSX de gangazos y persistencia
├── promociones_ui.html  # Interfaz web simple (promociones y gangazos)
├── Plantilla.csv        # Plantilla de promociones
├── Plantilla_gangazos.csv # Plantilla de gangazos
├── db_sqlserver.py      # Helper de conexión a SQL Server (OFIMA)
├── replicate.py         # Script de replicación OFIMA → PostgreSQL
├── requirements.txt
├── Dockerfile
├── Procfile
└── .env
```
