"""Sesión y engine de la base de datos."""
from __future__ import annotations

import asyncpg


async def crear_pool(dsn: str) -> asyncpg.Pool:
    # Un pool es un grupo de conexiones ya abiertas a PostgreSQL que se reutilizan, en vez de abrir y cerrar una conexion en cada peticion (abrir conexiones es lento). 
    # min_size/max_size = cuantas mantener.
    return await asyncpg.create_pool(dsn, min_size=2, max_size=10)


async def cerrar_pool(pool: asyncpg.Pool) -> None:
    # Se llama al apagar el servidor para cerrar las conexiones ordenadamente.
    await pool.close()
