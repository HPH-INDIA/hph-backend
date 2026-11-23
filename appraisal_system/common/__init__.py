"""Helpers shared by the Access/BFF monolith and the HPH microservices.

Limited to transport, authentication and logging concerns: no SQLAlchemy models and no domain
logic live here, so each service keeps ownership of its own data.
"""
