# Called with a service token, not a browser session, and answered in plain JSON:
# init_access_bff adds exactly these to the monolith's session and encryption exemptions.
DIRECTORY_PATHS = frozenset(
    {
        ("/api/directory/users", "GET"),
        ("/api/directory/projects", "GET"),
    }
)
