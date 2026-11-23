"""seed default appraisal templates

Revision ID: 664ee97468de
Revises: fb3a27ba9f38
Create Date: 2026-10-03 19:18:30.668383

"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "664ee97468de"
down_revision = "fb3a27ba9f38"
branch_labels = None
depends_on = None

templates_table = sa.table(
    "form_templates",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("purpose", sa.String),
    sa.column("definition", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
    sa.column("is_default", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
    schema="forms",
)

RATING_SCALE = {
    "min": 1,
    "max": 5,
    "labels": {"1": "Needs improvement", "3": "Meets expectations", "5": "Outstanding"},
}


def _long_text(key, label, required):
    return {
        "key": key,
        "type": "long_text",
        "label": label,
        "required": required,
        "max_length": 4000,
    }


def _rating(key, label):
    return {"key": key, "type": "rating", "label": label, "required": True, "scale": RATING_SCALE}


MANAGER_SECTION = {
    "key": "manager_assessment",
    "title": "Manager review",
    "description": "Completed by the manager after the one-to-one.",
    "filled_by": "reviewer",
    "review_stage": "manager",
    "fields": [
        _rating("manager_rating", "Final rating"),
        _long_text("manager_comments", "Manager comments", True),
        _long_text("development_plan", "Agreed development plan for the next cycle", False),
    ],
}

# Copied into a draft by admins and adjusted per project; kept static here so later code changes
# never rewrite what was seeded.
TEMPLATES = [
    {
        "name": "Employee appraisal (default)",
        "description": "Self-assessment, then Lead review, then Manager review.",
        "definition": {
            "sections": [
                {
                    "key": "self_assessment",
                    "title": "Self-assessment",
                    "description": "Reflect on your work this cycle.",
                    "filled_by": "employee",
                    "fields": [
                        _long_text(
                            "key_achievements", "What were your key achievements this cycle?", True
                        ),
                        _long_text(
                            "goals_progress", "How did you progress against your goals?", True
                        ),
                        _long_text(
                            "challenges",
                            "What got in your way, and what support would help?",
                            False,
                        ),
                        _rating("self_rating", "Overall self-rating"),
                        _long_text(
                            "development_goals", "What do you want to develop next cycle?", False
                        ),
                    ],
                },
                {
                    "key": "lead_assessment",
                    "title": "Lead review",
                    "description": "Completed by the lead before the appraisal goes to the manager.",
                    "filled_by": "reviewer",
                    "review_stage": "lead",
                    "fields": [
                        _rating("lead_rating", "Lead rating"),
                        _long_text(
                            "lead_comments", "Comments for the manager and the employee", True
                        ),
                    ],
                },
                MANAGER_SECTION,
            ]
        },
    },
    {
        "name": "Lead appraisal (default)",
        "description": "Self-assessment, then Manager review.",
        "definition": {
            "sections": [
                {
                    "key": "self_assessment",
                    "title": "Self-assessment",
                    "description": "Reflect on your work and your team's this cycle.",
                    "filled_by": "employee",
                    "fields": [
                        _long_text(
                            "key_achievements", "What were your key achievements this cycle?", True
                        ),
                        _long_text(
                            "team_outcomes",
                            "How did your team perform, and what did you change?",
                            True,
                        ),
                        _long_text(
                            "challenges",
                            "What got in your way, and what support would help?",
                            False,
                        ),
                        _rating("self_rating", "Overall self-rating"),
                        _long_text(
                            "development_goals", "What do you want to develop next cycle?", False
                        ),
                    ],
                },
                MANAGER_SECTION,
            ]
        },
    },
]


def upgrade():
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        templates_table,
        [
            {
                "name": template["name"],
                "description": template["description"],
                "purpose": "appraisal",
                "definition": template["definition"],
                "is_default": True,
                "created_at": now,
            }
            for template in TEMPLATES
        ],
    )


def downgrade():
    op.execute(
        templates_table.delete().where(
            templates_table.c.name.in_([template["name"] for template in TEMPLATES])
        )
    )
