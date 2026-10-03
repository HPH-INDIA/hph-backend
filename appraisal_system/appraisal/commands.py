"""Flask CLI commands, run on a schedule (e.g. a Render cron job).

``flask --app main send-notifications`` delivers queued emails;
``flask --app main queue-reminders`` queues deadline reminders.
"""

import click

from clients import directory_client
from services import notifications
import config


def register_commands(app):
    @app.cli.command("send-notifications")
    @click.option("--limit", default=100, show_default=True, help="Maximum emails to process.")
    def send_notifications_command(limit):
        """Send queued appraisal notifications (no-op until NOTIFICATIONS_ENABLED=true)."""
        if not config.NOTIFICATIONS_ENABLED:
            click.echo("NOTIFICATIONS_ENABLED is false; notifications stay queued.")
            return

        counts = notifications.deliver_pending(
            directory_client(), notifications.SmtpSender(), limit=limit
        )
        click.echo(f"sent={counts['sent']} skipped={counts['skipped']} failed={counts['failed']}")

    @app.cli.command("queue-reminders")
    @click.option(
        "--days-before",
        default=None,
        type=int,
        help="Remind this many days before a due date (default REMINDER_DAYS_BEFORE).",
    )
    def queue_reminders_command(days_before):
        """Queue submission and review reminders for active cycles."""
        counts = notifications.queue_deadline_reminders(days_before=days_before)
        click.echo(f"submission={counts['submission']} review={counts['review']}")
