# The monolith's `pytest` (run from the repository root) collects access_bff/tests, which use the
# monolith's fixtures. The microservices and common/ have their own pytest.ini and dependencies
# (Flask 2.2 / Connexion 2) and are run from their own directories instead.
collect_ignore = ["appraisal", "form_builder", "common"]
