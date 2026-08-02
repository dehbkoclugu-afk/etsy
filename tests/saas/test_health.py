from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.urls import reverse


def test_liveness_does_not_touch_database(client) -> None:
    response = client.get(reverse("health-live"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_checks_database(client) -> None:
    response = client.get(reverse("health-ready"))
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.django_db
def test_readiness_hides_database_error_details(client) -> None:
    with patch("pinforge_web.views.connections") as mocked_connections:
        mocked_connections.__getitem__.side_effect = DatabaseError(
            "password leaked in driver message"
        )
        response = client.get(reverse("health-ready"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"password" not in response.content
