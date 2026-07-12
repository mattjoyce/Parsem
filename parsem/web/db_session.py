"""Per-request SQLite connection provisioning for the stateless routes.

Background — the corruption this fixes
--------------------------------------
The store layer is connection-agnostic: every function takes a `conn`.
The one architectural flaw was *ownership* — a single shared connection
on `app.state.db`, used by every request thread at once. FastAPI runs
sync handlers (the ductile ingest callbacks) in a worker threadpool, so
a batch drop firing 6 concurrent `POST /ingest/raw-arrived` calls put 6
threads on that one connection. sqlite3 serialises individual statements
but not multi-statement sequences, so:

- `cur.lastrowid` (a *connection*-level property) read after an insert
  returned another request's rowid → orphaned docs / wrong ids.
- two implicit transactions collided → "cannot start a transaction
  within a transaction".

The fix — two connection strategies, mapped to a real seam
----------------------------------------------------------
- **Stateless request/response routes** (ingest, arrivals, library,
  assets) acquire a **fresh connection per request** via the `get_db`
  dependency. Fresh connections mean isolated `lastrowid` + transaction
  state, so the corruption is impossible by construction. WAL + a 5s
  `busy_timeout` (see `store.db.connect`) let these coexist.
- **The reader** keeps its durable `app.state.db` connection — it's a
  single-session stateful component whose `EventLog` holds a live
  connection, and multi-tab reader concurrency is deferred (Parsem-2rp).
  Binding that event log to a per-request connection would use it after
  close.

Test / `:memory:` mode reuses the one connection the app was built with
(a fresh `:memory:` connection is a fresh empty database), so the whole
existing suite is unaffected.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from parsem.store.db import connect


class SharedConnectionProvider:
    """Tests / `:memory:` — every request reuses the one connection the
    app was built with. Never closes it; the caller owns its lifetime."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @contextmanager
    def acquire(self) -> Iterator[sqlite3.Connection]:
        yield self._conn


class PerRequestConnectionProvider:
    """Production / file-backed — each request gets its own connection,
    closed when the request ends. Isolated connections are what kill the
    concurrent-ingest corruption."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)

    @contextmanager
    def acquire(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self._db_path)
        try:
            yield conn
        finally:
            conn.close()


def build_provider(
    db: sqlite3.Connection, db_path: str | Path | None
) -> SharedConnectionProvider | PerRequestConnectionProvider:
    """Choose the strategy. A real file path → per-request; otherwise
    (no path, or an in-memory database) → shared. The `:memory:` guard
    matters because `build_app` in a paths-only test build can hand us a
    `:memory:` path, and opening a second `:memory:` connection would be
    a different, empty database."""
    if db_path is not None and str(db_path) != ":memory:":
        return PerRequestConnectionProvider(db_path)
    return SharedConnectionProvider(db)


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency for the stateless routes. FastAPI runs this
    sync generator in the worker thread, so open / use / close all happen
    on one thread. The reader routes deliberately do NOT use this — they
    read `request.app.state.db` (the durable connection) directly."""
    with request.app.state.db_provider.acquire() as conn:
        yield conn


# Handler annotation: `conn: DbConn`. Using Annotated (not a `= Depends()`
# default) keeps ruff's B008 quiet and reads cleanly at every call site.
DbConn = Annotated[sqlite3.Connection, Depends(get_db)]
