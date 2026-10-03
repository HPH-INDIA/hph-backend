"""Read-only client for the Access service's directory (users, reporting lines, projects).

Services never read the monolith's tables; they ask the directory API instead.
"""

from dataclasses import dataclass
from datetime import date


ACCESS_AUDIENCE = "hph-access"


@dataclass(frozen=True)
class DirectoryUser:
    """One HPH user as the directory reports it."""

    id: int
    emp_id: str
    first_name: str
    last_name: str
    email: str
    role_type_code: str
    role_title: str
    project_id: int
    reports_to_id: int
    is_active: bool
    last_working_day: date = None

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @classmethod
    def from_payload(cls, payload):
        last_working_day = payload.get("lastWorkingDay")
        return cls(
            id=payload["id"],
            emp_id=payload.get("empId"),
            first_name=payload.get("firstName") or "",
            last_name=payload.get("lastName") or "",
            email=payload.get("email"),
            role_type_code=payload.get("roleTypeCode"),
            role_title=payload.get("roleTitle"),
            project_id=payload.get("projectId"),
            reports_to_id=payload.get("reportsToId"),
            is_active=bool(payload.get("isActive")),
            last_working_day=date.fromisoformat(last_working_day) if last_working_day else None,
        )


@dataclass(frozen=True)
class DirectoryProject:
    id: int
    name: str


class DirectoryClient:
    """Wraps a ``ServiceClient`` pointed at the Access/BFF service.

    Parameters
    ----------
    service_client : common.service_client.ServiceClient
        Client configured with ``unwrap_envelope=True`` and audience ``hph-access``.
    """

    def __init__(self, service_client):
        self.service_client = service_client

    def users(self, ids=None, project_id=None, role_types=None, active=None):
        """Return users matching every given filter.

        Parameters
        ----------
        ids : iterable of int, optional, default = None
            Restrict to these user IDs; an empty iterable returns no users.
        project_id : int, optional, default = None
            Restrict to one project.
        role_types : iterable of str, optional, default = None
            Restrict to these role type codes.
        active : bool, optional, default = None
            Restrict to active (``True``) or inactive (``False``) users.

        Returns
        -------
        list of DirectoryUser
        """
        params = {}
        if ids is not None:
            ids = sorted({int(user_id) for user_id in ids})
            if not ids:
                return []
            params["ids"] = ",".join(str(user_id) for user_id in ids)
        if project_id is not None:
            params["projectId"] = project_id
        if role_types:
            params["roleTypes"] = ",".join(sorted(role_types))
        if active is not None:
            params["active"] = "true" if active else "false"

        payload = self.service_client.get("/api/directory/users", params=params) or []

        return [DirectoryUser.from_payload(item) for item in payload]

    def projects(self, ids=None):
        """Return projects, optionally restricted to *ids*, as ``DirectoryProject`` values."""
        params = {}
        if ids is not None:
            ids = sorted({int(project_id) for project_id in ids})
            if not ids:
                return []
            params["ids"] = ",".join(str(project_id) for project_id in ids)

        payload = self.service_client.get("/api/directory/projects", params=params) or []

        return [DirectoryProject(id=item["id"], name=item["name"]) for item in payload]
