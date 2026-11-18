"""Server-side role checks (FastAPI path) use the database, not the token."""

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser, require_role

from .conftest import ROLES

RUN_ANALYSIS = {"admin", "analyst", "researcher"}  # openapi: POST /jobs
REVIEW = {"admin", "analyst"}  # openapi: POST /findings/{id}/reviews


@pytest.mark.parametrize("role", ROLES)
def test_require_role_matches_membership(world, conn, role):
    user = CurrentUser(id=world.a.users[role], email=None, claims={})
    for allowed in (RUN_ANALYSIS, REVIEW):
        if role in allowed:
            assert require_role(conn, world.a.id, user, allowed) == role
        else:
            with pytest.raises(HTTPException) as e:
                require_role(conn, world.a.id, user, allowed)
            assert e.value.status_code == 403


def test_member_of_other_org_is_forbidden(world, conn):
    user = CurrentUser(id=world.b.users["admin"], email=None, claims={})
    with pytest.raises(HTTPException) as e:
        require_role(conn, world.a.id, user, {"admin", "analyst", "researcher", "viewer"})
    assert e.value.status_code == 403


def test_outsider_is_forbidden(world, conn):
    user = CurrentUser(id=world.outsider, email=None, claims={})
    with pytest.raises(HTTPException):
        require_role(conn, world.a.id, user, {"viewer"})
