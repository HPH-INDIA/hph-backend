import common.errors as errors
from config import APPRAISAL_SERVICE_ISSUER


FORM_BUILDER_FEATURE = "form_builder"


def require_form_builder(caller, access="read"):
    """Raise unless the caller is a user holding the ``form_builder`` feature with *access*."""
    if not caller.can(FORM_BUILDER_FEATURE, access):
        raise errors.PermissionDenied(f"requires the {FORM_BUILDER_FEATURE} feature ({access})")


def is_appraisal_service(caller):
    return caller.is_service and caller.issuer == APPRAISAL_SERVICE_ISSUER


def require_appraisal_service_or_form_reader(caller):
    """Allow the appraisal service, or a user who can read forms (e.g. to preview them)."""
    if is_appraisal_service(caller):
        return

    require_form_builder(caller, "read")
