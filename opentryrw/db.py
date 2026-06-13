from __future__ import annotations

from pathlib import Path
from typing import Any

from tinydb import Query, TinyDB
from tinydb.storages import MemoryStorage

from .settings import settings


class Database:
    def __init__(self, path: str) -> None:
        if path == ":memory:":
            self.db = TinyDB(storage=MemoryStorage)
        else:
            db_path = Path(path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db = TinyDB(db_path)
        self.users = self.db.table("users")
        self.auth_sessions = self.db.table("auth_sessions")
        self.auth_states = self.db.table("auth_states")
        self.deployments = self.db.table("deployments")
        self.cooldowns = self.db.table("cooldowns")
        self.notifications = self.db.table("notifications")

    def close(self) -> None:
        self.db.close()


database = Database(settings.db_path)
Record = dict[str, Any]
Q = Query()
