from types import SimpleNamespace

import pytest

from src.services.auth import (
    ANALYST_RUN_ROLES,
    ActorContext,
    AuthorizationDenied,
    _actor_from_verified_user,
    authorize,
    system_actor,
)
from src.services.work_admission import (
    WorkAdmissionController,
    WorkflowDeadline,
    WorkflowDeadlineExceeded,
    WorkRejected,
)


def test_role_comes_from_app_metadata_and_branch_scope_is_preserved():
    user = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="manager@example.test",
        app_metadata={"role": "warehouse_manager", "branch_ids": ["b1", "b2"]},
        user_metadata={"role": "super_admin"},
    )
    actor = _actor_from_verified_user(user)
    assert actor.role == "warehouse_manager"
    assert actor.branch_ids == ("b1", "b2")
    with pytest.raises(AuthorizationDenied):
        authorize(actor, ANALYST_RUN_ROLES, "run analyst")


def test_super_admin_can_trigger_global_analyst_sync():
    actor = ActorContext("u-admin", "admin@example.test", "super_admin")
    authorize(actor, ANALYST_RUN_ROLES, "run analyst")


def test_forged_system_actor_is_rejected():
    actor = ActorContext("system:forged", "", "system_agent")
    with pytest.raises(AuthorizationDenied):
        authorize(actor, ANALYST_RUN_ROLES, "run analyst")


def test_trusted_system_actor_can_run_scheduled_workflow():
    authorize(system_actor("analyst-cron"), ANALYST_RUN_ROLES, "run analyst")


def test_user_metadata_cannot_escalate_role():
    user = SimpleNamespace(
        id="u1",
        email="user@example.test",
        app_metadata={},
        user_metadata={"role": "super_admin"},
    )
    with pytest.raises(AuthorizationDenied):
        _actor_from_verified_user(user)


def test_role_guard_rejects_purchasing_staff_for_analyst_trigger():
    actor = ActorContext("u1", "staff@example.test", "purchasing_staff")
    with pytest.raises(AuthorizationDenied):
        authorize(actor, ANALYST_RUN_ROLES, "run analyst")


def test_work_admission_blocks_overlap_and_rate_repeat():
    now = [100.0]
    clock = lambda: now[0]
    controller = WorkAdmissionController(max_concurrent=1, cooldown_seconds=30, clock=clock)
    actor = ActorContext("u1", "manager@example.test", "warehouse_manager")

    with controller.admit(actor, "analyst"), pytest.raises(WorkRejected), controller.admit(actor, "analyst"):
        pass
    with pytest.raises(WorkRejected), controller.admit(actor, "analyst"):
        pass
    now[0] += 31
    with controller.admit(actor, "analyst"):
        pass


def test_workflow_deadline_is_cooperative_and_deterministic():
    now = [10.0]
    deadline = WorkflowDeadline(seconds=5, clock=lambda: now[0])
    deadline.check("start")
    now[0] = 15.0
    with pytest.raises(WorkflowDeadlineExceeded):
        deadline.check("forecast")
