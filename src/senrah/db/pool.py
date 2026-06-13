"""
senrah.db.pool — psycopg3 connection pool factories with pgvector registration.

Provides:
- create_pool(dsn, min_size, max_size) → AsyncConnectionPool
  For async contexts (Indexer, MCP server).
- connect_sync(dsn) → psycopg.Connection
  For sync contexts (CLI commands, Alembic env).

Both call register_vector / register_vector_async so psycopg3 knows how to
serialize / deserialize the pgvector `vector` type (list[float] ↔ vector column).
"""

from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector, register_vector_async
from psycopg_pool import AsyncConnectionPool


async def create_pool(
    dsn: str,
    min_size: int = 1,
    max_size: int = 5,
) -> AsyncConnectionPool:
    """Open an async connection pool with pgvector type registration.

    The pool is opened immediately (open=False then await pool.open()) so that
    connection errors surface at startup rather than on the first query.
    register_vector_async is called once to register the vector type adapter
    on a pooled connection; subsequent connections inherit the registration.
    """
    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        open=False,
    )
    await pool.open()

    # Register the pgvector type adapter for the pool
    async with pool.connection() as conn:
        await register_vector_async(conn)

    return pool


def connect_sync(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a synchronous psycopg3 connection with pgvector type registration.

    Suitable for CLI commands and migration scripts.
    The caller is responsible for closing the connection (use as a context manager).

    autocommit: pass True when the caller drives its own per-unit `conn.transaction()`
        blocks and needs each to COMMIT durably (e.g. the Ingester's per-PR
        upsert+advance_cursor — D-B3). With the default autocommit=False, the first
        statement opens an implicit transaction, so a later `conn.transaction()`
        becomes a SAVEPOINT (released, not committed) — nothing is durable until the
        connection closes, and a crash mid-run loses ALL progress (no resume).
    """
    conn = psycopg.connect(dsn, autocommit=autocommit)
    register_vector(conn)
    return conn
