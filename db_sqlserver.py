"""
Helper de conexión a SQL Server (OFIMA) para consultas en tiempo real.
Usado por los endpoints que no pueden replicarse (stock, etc.)
"""

import os
import pyodbc


def get_sqlserver_conn():
    conn_str = (
        f"DRIVER={{{os.environ['SQLSERVER_DRIVER']}}};"
        f"SERVER={os.environ['SQLSERVER_HOST']};"
        f"DATABASE={os.environ['SQLSERVER_DB']};"
        f"UID={os.environ['SQLSERVER_USER']};"
        f"PWD={os.environ['SQLSERVER_PASSWORD']};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;"
    )
    return pyodbc.connect(conn_str)


def rows_to_dicts(cursor) -> list[dict]:
    """Convierte filas pyodbc a lista de dicts usando los nombres de columna."""
    columns = [col[0].lower() for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
