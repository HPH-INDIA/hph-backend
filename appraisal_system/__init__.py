"""HPH appraisal system: the Appraisal and Form Builder microservices, their shared library, and
the Access/BFF plugin the existing monolith loads (``appraisal_system.access_bff``).

The services run as their own processes from their own directories; only ``access_bff`` and
``common`` are imported by the monolith.
"""
