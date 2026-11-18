"""Resolve people named in imported workbooks to HPH users."""
import re

from app.it_tickets.access import full_name, role_type
from app.kairon.services import normalize_analyst_name
from app.users.models import User

_SPLIT_RE = re.compile(r"\s*(?:\||,|;|\n| & | and )\s*", re.IGNORECASE)


def name_key(raw):
    return " ".join(normalize_analyst_name(str(raw or "")).split()).lower()


def split_names(cell):
    """'A | B' / 'A, B' / 'Eric Osorio (2LR)' -> clean display names."""
    names = []
    for part in _SPLIT_RE.split(str(cell or "")):
        part = re.sub(r"\(.*?\)", "", part).strip()
        if part and part.lower() not in ("none", "0", "-") and part not in names:
            names.append(part)
    return names


class NameResolver:
    """Full-name lookup limited to the importer's visibility: a manager's own
    project, or every user for admins. Explicit mappings win."""

    def __init__(self, actor, mappings=None):
        query = User.query
        if role_type(actor) == "manager":
            query = query.filter(User.project_id == actor.project_id)
        self.by_key = {}
        self.by_id = {}
        for user in query.order_by(User.id.asc()).all():
            self.by_id[user.id] = user
            self.by_key.setdefault(" ".join(full_name(user).split()).lower(), user)
        self.mappings = {name_key(raw): user_id for raw, user_id in (mappings or {}).items()}

    def resolve(self, raw):
        key = name_key(raw)
        if not key:
            return None
        mapped = self.mappings.get(key)
        if mapped is not None:
            return self.by_id.get(mapped)
        return self.by_key.get(key)
