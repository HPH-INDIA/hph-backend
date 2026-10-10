from app.extensions import db


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)

    metric_theme = db.Column(db.JSON, nullable=True)
    metric_theme_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    metric_theme_updated_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self):
        return f"<Project {self.name}>"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    first_name = db.Column(db.String(128), nullable=False)
    last_name = db.Column(db.String(128), nullable=False)
    emp_id = db.Column(db.String(64), unique=True, nullable=False)
    role_id = db.Column(
        db.Integer, db.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    reports_to_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    password_hash = db.Column(db.String(255), nullable=False)
    first_login = db.Column(db.Boolean, nullable=False, default=True)
    temp_password_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    join_date = db.Column(db.Date, nullable=True)
    last_working_day = db.Column(db.Date, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    role = db.relationship("Role")
    project = db.relationship("Project", foreign_keys=[project_id])
    created_by = db.relationship("User", remote_side=[id], foreign_keys=[created_by_id])
    reports_to = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[reports_to_id],
        backref=db.backref("direct_reports", foreign_keys=[reports_to_id]),
    )

    def __repr__(self):
        return f"<User {self.email}>"
