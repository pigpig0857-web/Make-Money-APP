"""PostgreSQL connections configured through local Streamlit secrets."""

from contextlib import contextmanager

import streamlit as st


class DatabaseConfigurationError(Exception):
    """Raised when local connection settings are incomplete."""


@contextmanager
def database_connection():
    """Open one connection per operation and close it on completion."""
    try:
        settings = dict(st.secrets["postgres"])
    except (FileNotFoundError, KeyError) as exc:
        raise DatabaseConfigurationError("請先設定 .streamlit/secrets.toml 的 [postgres] 區塊。") from exc
    if not settings.get("password"):
        raise DatabaseConfigurationError("請在 .streamlit/secrets.toml 填入 PostgreSQL 密碼。")

    import psycopg

    # Pass separate parameters so spaces or punctuation need no URL escaping.
    with psycopg.connect(
        host=settings.get("host", "127.0.0.1"),
        port=int(settings.get("port", 5432)),
        dbname=settings.get("dbname", "Make Money APP"),
        user=settings.get("user", "postgres"),
        password=settings["password"],
        connect_timeout=5,
        application_name="MakeMoney App",
        options="-c statement_timeout=10000 -c lock_timeout=3000",
    ) as connection:
        yield connection


def check_database_connection():
    """Check the configured database with a read-only query."""
    with database_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            return cursor.fetchone()
