"""Admin commands for Microsoft account links (admins are never linked automatically)."""

import click
from flask import current_app

from appraisal_system.access_bff.entra.services import link_identity, unlink_identity


def register_commands(app):
    @app.cli.command("entra-link")
    @click.option("--email", required=True, help="The HPH user's email (as stored in HPH).")
    @click.option("--object-id", required=True, help="The person's Microsoft object id (oid).")
    @click.option("--tenant-id", default=None, help="Defaults to ENTRA_TENANT_ID.")
    @click.option("--replace", is_flag=True, help="Replace an existing link for this user.")
    def entra_link_command(email, object_id, tenant_id, replace):
        """Link an HPH user to their Microsoft account."""
        tenant_id = tenant_id or current_app.config["ENTRA_TENANT_ID"]
        if not tenant_id:
            raise click.UsageError("Pass --tenant-id or set ENTRA_TENANT_ID.")

        try:
            link_identity(email, object_id, tenant_id, replace=replace)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"Linked {email} to Microsoft account {object_id}.")

    @app.cli.command("entra-unlink")
    @click.option("--email", required=True, help="The HPH user's email (as stored in HPH).")
    def entra_unlink_command(email):
        """Remove a user's Microsoft link."""
        try:
            unlink_identity(email)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(f"Unlinked {email} from Microsoft sign-in.")
