"""Expose one Team permission while reading legacy stage grants safely."""
from app.extensions import db
from app.features.models import Feature
from app.roles.models import role_features


def canonical_permissions(role):
    rows = db.session.execute(db.select(Feature.id, Feature.codename, role_features.c.can_read, role_features.c.can_write)
        .select_from(role_features.join(Feature, Feature.id == role_features.c.feature_id))
        .where(role_features.c.role_id == role.id)).all()
    team = Feature.query.filter_by(codename="team").first()
    result = {}
    for row in rows:
        legacy = row.codename == "stage_view" and team is not None
        feature_id = team.id if legacy else row.id
        permission = result.setdefault(feature_id, {"feature_id": feature_id, "can_read": False, "can_write": False})
        permission["can_read"] |= bool(row.can_read or row.can_write)
        permission["can_write"] |= bool(row.can_write and not legacy and role.role_type.code != "client")
    return sorted(result.values(), key=lambda permission: permission["feature_id"])
