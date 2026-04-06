from __future__ import annotations

import asyncpg

pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS queries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    query_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS results (
    id BIGSERIAL PRIMARY KEY,
    query_id BIGINT REFERENCES queries(id) NOT NULL,
    raw_response TEXT NOT NULL,
    model_used TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_seconds FLOAT,
    completed_at TIMESTAMPTZ DEFAULT NOW()
);
"""


async def init_db(database_url: str):
    global pool
    new_pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    try:
        async with new_pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
    except Exception:
        await new_pool.close()
        raise

    pool = new_pool


async def close_db():
    global pool
    if pool:
        await pool.close()
        pool = None


def _get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool is not initialized")
    return pool


async def save_query(telegram_id: int, username: str | None, first_name: str | None,
                     query_text: str) -> int:
    async with _get_pool().acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO users (telegram_id, username, first_name) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (telegram_id) DO UPDATE SET username=$2, first_name=$3 "
            "RETURNING id",
            telegram_id, username, first_name,
        )
        query_id = await conn.fetchval(
            "INSERT INTO queries (user_id, query_text) "
            "VALUES ($1, $2) RETURNING id",
            user_id, query_text,
        )
        return query_id


async def save_result(query_id: int, raw_response: str, model: str,
                      input_tokens: int, output_tokens: int,
                      duration: float):
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO results (query_id, raw_response, model_used, "
            "input_tokens, output_tokens, duration_seconds) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            query_id, raw_response, model, input_tokens, output_tokens, duration,
        )
