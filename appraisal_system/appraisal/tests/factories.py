import copy
from datetime import date

import factory
from conftest import (
    EMPLOYEE_DEFINITION,
    EMPLOYEE_FORM_VERSION,
    EMPLOYEE_ID,
    LEAD_ID,
    MANAGER_ID,
)

from common.configdb import get_sql_alchemy
from models import Appraisal, AppraisalStatus, Cycle, CycleStatus


def _factory_session():
    return get_sql_alchemy().session


class BaseModelFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_persistence = "commit"

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        cls._meta.sqlalchemy_session = _factory_session()
        return super()._build(model_class, *args, **kwargs)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        cls._meta.sqlalchemy_session = _factory_session()
        return super()._create(model_class, *args, **kwargs)


class CycleFactory(BaseModelFactory):
    class Meta:
        model = Cycle

    name = factory.Sequence(lambda n: f"Cycle {n}")
    scope = "organization"
    project_id = None
    frequency = "annual"
    start_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)
    submission_due = date(2026, 12, 15)
    review_due = date(2026, 12, 31)
    require_one_to_one = True
    status = CycleStatus.ACTIVE
    created_by = 1


class AppraisalFactory(BaseModelFactory):
    """An Employee's appraisal with a complete Lead -> Manager chain, not started yet."""

    class Meta:
        model = Appraisal

    cycle = factory.SubFactory(CycleFactory)
    user_id = EMPLOYEE_ID
    employee_name = "Employee 30"
    employee_emp_id = "E30"
    project_id = 1
    role_type_code = "employee"
    form_id = EMPLOYEE_FORM_VERSION * 10
    form_version_id = EMPLOYEE_FORM_VERSION
    form_name = "Coding employee"
    form_definition = factory.LazyFunction(lambda: copy.deepcopy(EMPLOYEE_DEFINITION))
    lead_user_id = LEAD_ID
    lead_name = "Lead 20"
    manager_user_id = MANAGER_ID
    manager_name = "Manager 10"
    status = AppraisalStatus.NOT_STARTED
    review_attempt = 1
