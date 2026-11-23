"""Client for the Form Builder service."""

import common.errors as errors


class FormsClient:
    """Resolves and validates published forms through the Form Builder API.

    Parameters
    ----------
    service_client : common.service_client.ServiceClient
        Client configured with audience ``hph-form-builder``.
    """

    def __init__(self, service_client):
        self.service_client = service_client

    def resolve(self, project_id, role_type):
        """Return the published form for a project and role type, or ``None`` when there is none.

        Returns
        -------
        dict or None
            ``{"form_id", "form_name", "version_id", "version_no", "definition"}``.
        """
        try:
            return self.service_client.get(
                "/forms/resolve",
                params={"project_id": project_id, "role_type": role_type, "purpose": "appraisal"},
            )
        except errors.UpstreamError as exc:
            if exc.status == 404:
                return None
            raise

    def validate(self, version_id, answers, stage, mode):
        """Validate one stage's answers against a pinned form version.

        Returns
        -------
        tuple of (dict, list of dict)
            Normalised answers and the problems found (empty when valid).
        """
        result = self.service_client.post(
            f"/form-versions/{version_id}/validate",
            json_body={"answers": answers, "stage": stage, "mode": mode},
        )

        return result.get("normalized_answers") or {}, result.get("errors") or []
