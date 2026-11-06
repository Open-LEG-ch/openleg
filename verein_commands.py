# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local provisioning for actual elected reviewers; no public signup for board roles."""

import click
from flask.cli import with_appcontext

import database as db
from security_utils import validate_email_address


def init_app(app):
    @app.cli.command("verein-add-reviewer")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True)
    @click.option(
        "--role", type=click.Choice(["board", "finance", "community"]), default="board"
    )
    @click.password_option(confirmation_prompt=True)
    @with_appcontext
    def add_reviewer(email, name, role, password):
        """Provision one authorised elected reviewer after checking the actual record."""
        valid, normalized, _error = validate_email_address(email)
        if not valid or not 2 <= len(name.strip()) <= 120:
            raise click.ClickException("Invalid reviewer identity")
        try:
            db.provision_verein_reviewer(normalized, name.strip(), password, role)
        except db.VereinDenied as exc:
            raise click.ClickException(str(exc)) from None
        except db.VereinUnavailable:
            raise click.ClickException("Reviewer storage unavailable") from None
        click.echo("Reviewer provisioned. Record the authorising election separately.")

    @app.cli.command("verein-revoke-reviewer")
    @click.argument("identifier", type=click.UUID)
    @with_appcontext
    def revoke_reviewer(identifier):
        """Revoke a former reviewer and all their sessions."""
        try:
            db.revoke_verein_reviewer(str(identifier))
        except db.VereinUnavailable:
            raise click.ClickException("Reviewer storage unavailable") from None
        click.echo("Reviewer access revoked.")
