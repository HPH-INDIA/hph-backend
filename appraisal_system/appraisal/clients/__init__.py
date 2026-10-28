from flask import current_app

import common.errors as errors


def directory_client():
    """Return the Access directory client configured on the app.

    Raises
    ------
    common.errors.Unavailable
        If ``ACCESS_SERVICE_URL`` was not configured.
    """
    client = current_app.extensions.get("directory_client")
    if client is None:
        raise errors.Unavailable("the Access directory is not configured (ACCESS_SERVICE_URL)")

    return client


def forms_client():
    """Return the Form Builder client configured on the app.

    Raises
    ------
    common.errors.Unavailable
        If ``FORM_BUILDER_SERVICE_URL`` was not configured.
    """
    client = current_app.extensions.get("forms_client")
    if client is None:
        raise errors.Unavailable("the Form Builder is not configured (FORM_BUILDER_SERVICE_URL)")

    return client
