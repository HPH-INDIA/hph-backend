"""Feature checks for the appraisal service (§9.3).

``appraisal_cycle_admin`` lets Admins run cycles and monitor every appraisal,
``appraisal_review`` lets Leads and Managers review the appraisals assigned to them, and
``appraisal_self`` lets Employees and Leads fill their own.
"""

import common.errors as errors


CYCLE_ADMIN_FEATURE = "appraisal_cycle_admin"
REVIEW_FEATURE = "appraisal_review"
SELF_FEATURE = "appraisal_self"


def require_user(caller):
    if caller.is_service:
        raise errors.PermissionDenied("this endpoint is only for signed-in users")


def require_feature(caller, feature, access="read"):
    """Raise unless *caller* holds *feature* with *access*."""
    require_user(caller)
    if not caller.can(feature, access):
        raise errors.PermissionDenied(f"requires the {feature} feature ({access})")


def is_cycle_admin(caller, access="read"):
    return not caller.is_service and caller.can(CYCLE_ADMIN_FEATURE, access)


def is_reviewer(caller, access="read"):
    return not caller.is_service and caller.can(REVIEW_FEATURE, access)
