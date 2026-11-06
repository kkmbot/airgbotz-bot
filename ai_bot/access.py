"""Who may use the bot. Admins always can; everyone else needs ALLOW_ALL=true or an /allow entry."""
from __future__ import annotations

from .config import Settings
from .store import Store


class Access:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_ids

    def is_allowed(self, user_id: int) -> bool:
        if self.is_admin(user_id) or self.settings.allow_all:
            return True
        return self.store.is_allowed(user_id)

    def allow(self, user_id: int) -> None:
        self.store.allow(user_id)

    def deny(self, user_id: int) -> None:
        self.store.deny(user_id)
