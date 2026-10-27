from datetime import datetime, timezone

import click
from flask.cli import with_appcontext
from sqlalchemy.exc import IntegrityError

from app.entra.models import AuthTransaction
from app.entra.services import link_identity, unlink_identity
from app.extensions import db
from app.users.models import User


@click.command("link-entra-user")
@click.option("--user-id", type=int, required=True)
@click.option("--object-id")
@click.option("--account-type", type=click.Choice(["member", "guest"]), default="member")
@click.option("--operator", required=True, help="Operator identity/ticket for the audit trail.")
@click.option("--unlink", is_flag=True)
@with_appcontext
def link_entra_command(user_id, object_id, account_type, operator, unlink):
    """Audited operator recovery/bootstrap, including Super Admin accounts."""
    user = db.session.get(User, user_id)
    if user is None:
        raise click.ClickException("User not found.")
    if not operator.strip() or len(operator) > 128:
        raise click.ClickException("Supply an operator identifier of 1–128 characters.")
    try:
        if unlink:
            unlink_identity(user, operator=operator)
        else:
            if not object_id:
                raise ValueError("--object-id is required when linking.")
            link_identity(user, object_id, account_type, operator=operator)
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        raise click.ClickException("Invalid or duplicate identity link; verify the tenant, object ID and role.") from exc
    click.echo(f"Updated Entra access for user {user_id}; existing sessions revoked.")


@click.command("cleanup-auth-transactions")
@with_appcontext
def cleanup_auth_command():
    """Remove expired encrypted login transactions (also cleaned at login)."""
    count = AuthTransaction.query.filter(AuthTransaction.expires_at <= datetime.now(timezone.utc)).delete()
    db.session.commit()
    click.echo(f"Removed {count} expired authentication transactions.")
