"""Real temporary files prove GUI authorization, planning, and verified moves."""

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
import django

django.setup()
from django.test import RequestFactory, override_settings  # noqa: E402

from scitex_storage._django import logical_move, organize, project_files  # noqa: E402

_ROOTS = {}


def provide_volumes(request):
    return [
        dict(key=name, label="private-host-" + name, path=path, tier=name)
        for name, path in _ROOTS.items()
        if name in {"hot", "cold"}
    ]


def provide_home(request):
    return _ROOTS["home"]


@pytest.fixture
def storage(tmp_path):
    for name in ("hot", "cold", "home"):
        path = tmp_path / name
        path.mkdir()
        _ROOTS[name] = path
    (_ROOTS["hot"] / "experiment").mkdir()
    (_ROOTS["hot"] / "experiment/data.txt").write_text("scientific data\n")
    with override_settings(
        SCITEX_STORAGE_VOLUMES_PROVIDER=__name__ + ".provide_volumes",
        SCITEX_USER_HOME_PROVIDER=__name__ + ".provide_home",
        SCITEX_PROJECT_PROVIDER="",
        SCITEX_PROJECT_STORAGE="",
        SCITEX_STORAGE_TIER_RATES={"cold": {"currency": "USD", "per_gb_month": "2"}},
    ):
        yield _ROOTS
    _ROOTS.clear()


def request(data=None, post=False):
    data = {
        "source_tier": "hot",
        "source_path": "experiment",
        "destination_tier": "cold",
        "destination_path": "experiment",
        "scope": "all",
        **(data or {}),
    }
    req = (RequestFactory().post if post else RequestFactory().get)("/", data)
    req.user = SimpleNamespace(username="scientist", is_authenticated=True)
    return req


def test_plan_is_read_only_and_hides_physical_paths(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    before = sorted(str(p) for root in storage.values() for p in root.rglob("*"))
    response = logical_move.move(request({"plan": "1"}))
    body = response.content.decode()
    observed.append(bool(response.status_code == 200))
    observed.append(bool("Move plan (not executed)." in body))
    observed.append(bool("/home/scientist/hot/experiment" in body))
    observed.append(
        bool(str(storage["hot"]) not in body and "private-host" not in body)
    )
    observed.append(
        bool("Day:" in body and "Week:" in body and "Month (30 days):" in body)
    )
    observed.append(bool("Rate not configured" in body))
    observed.append(
        bool(
            sorted(str(p) for root in storage.values() for p in root.rglob("*"))
            == before
        )
    )
    # Assert
    assert all(observed), observed


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_path", "../other"),
        ("destination_path", "../../other"),
        ("source_path", "/etc"),
        ("destination_path", "/tmp/new"),
        ("source_tier", "scitex-nas-01"),
        ("destination_tier", "unassigned"),
    ],
)
def test_invalid_move_never_touches_files(storage, field, value):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    response = logical_move.move_apply(
        request({field: value, "confirm": "yes"}, post=True)
    )
    observed.append(bool(response.status_code in {200, 403}))
    observed.append(
        bool(
            (storage["hot"] / "experiment/data.txt").read_text() == "scientific data\n"
        )
    )
    observed.append(bool(list(storage["cold"].iterdir()) == []))
    # Assert
    assert all(observed), observed


def test_symlink_escape_refused(storage, tmp_path):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    (storage["cold"] / "escape").symlink_to(tmp_path, target_is_directory=True)
    response = logical_move.move_apply(
        request({"destination_path": "escape/other", "confirm": "yes"}, post=True)
    )
    observed.append(bool(response.status_code == 403))
    observed.append(bool((storage["hot"] / "experiment").exists()))
    # Assert
    assert all(observed), observed


def test_confirmation_required_and_get_disallowed(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    response = logical_move.move_apply(request(post=True))
    observed.append(
        bool(response.status_code == 200 and b"Confirm" in response.content)
    )
    observed.append(bool(logical_move.move_apply(request()).status_code == 405))
    observed.append(bool((storage["hot"] / "experiment").exists()))
    # Assert
    assert all(observed), observed


def test_hosted_missing_persistent_home_fails_closed(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    with override_settings(SCITEX_APP_MODE="hosted", SCITEX_USER_HOME_PROVIDER=""):
        response = logical_move.move_apply(request({"confirm": "yes"}, post=True))
    observed.append(bool(response.status_code == 403))
    observed.append(bool(list(storage["cold"].iterdir()) == []))
    # Assert
    assert all(observed), observed


def test_unknown_explicit_project_cannot_expand_to_all(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    response = organize.usage(request({"project": "other/private", "scope": "project"}))
    observed.append(bool(response.status_code == 403))
    # Assert
    assert all(observed), observed


@pytest.mark.skipif(not shutil.which("rsync"), reason="real rsync required")
def test_confirmed_move_verifies_and_retains_original(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    import json

    response = logical_move.move_apply(request({"confirm": "yes"}, post=True))
    observed.append(bool(response.status_code == 200))
    observed.append(bool(b"Move applied and verified" in response.content))
    observed.append(
        bool(
            (storage["cold"] / "experiment/data.txt").read_text() == "scientific data\n"
        )
    )
    observed.append(bool(not (storage["hot"] / "experiment").exists()))
    receipts = list(
        (storage["home"] / ".scitex/scitex-storage/runtime/move-manifests").glob(
            "*.json"
        )
    )
    observed.append(bool(len(receipts) == 1))
    receipt = json.loads(receipts[0].read_text())
    observed.append(bool(receipt["phase"] == "retired"))
    observed.append(
        bool(
            Path(receipt["staged_path"], "data.txt").read_text() == "scientific data\n"
        )
    )
    observed.append(bool(receipt["verification_method"] != "pending"))
    # Assert
    assert all(observed), observed


def test_project_root_move_refused_even_with_edit_permission(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    project = project_files.ProjectScope(
        storage["hot"] / "experiment", project_id="owner/paper"
    )
    old = project_files._GET_CURRENT_PROJECT
    project_files._GET_CURRENT_PROJECT = lambda request, user=None: SimpleNamespace(
        local_path=str(project.project_dir),
        id=project.project_id,
        can_edit=lambda user: True,
    )
    try:
        response = logical_move.move_apply(
            request(
                {"project": "owner/paper", "scope": "project", "confirm": "yes"},
                post=True,
            )
        )
    finally:
        project_files._GET_CURRENT_PROJECT = old
    observed.append(bool(response.status_code == 403))
    observed.append(bool((storage["hot"] / "experiment/data.txt").exists()))
    # Assert
    assert all(observed), observed


def test_chart_declares_opaque_origin_even_for_failure(storage):
    # Arrange -- filesystem roots and host capabilities come from the fixture.
    observed = []
    # Act
    response = organize.usage_sunburst(
        request({"scope": "project", "project": "missing"})
    )
    observed.append(bool(response.status_code == 403))
    observed.append(
        bool(response["Content-Security-Policy"] == "sandbox allow-scripts")
    )
    # Assert
    assert all(observed), observed
