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
