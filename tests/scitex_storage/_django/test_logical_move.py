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


@pytest.mark.parametrize("view", ["plan", "apply", "status"])
def test_receipt_symlink_cannot_escape_user_home(storage, tmp_path, view):
    # Arrange
    outside = tmp_path / "other-account"
    outside.mkdir()
    (storage["home"] / ".scitex").symlink_to(outside, target_is_directory=True)
    call = {
        "plan": logical_move.move,
        "apply": logical_move.move_apply,
        "status": logical_move.move_status,
    }[view]
    # Act
    response = call(
        request(
            {"plan": "1", "confirm": "yes", "transaction": "0" * 32},
            post=view == "apply",
        )
    )
    # Assert
    assert (
        response.status_code,
        list(outside.iterdir()),
        (storage["hot"] / "experiment/data.txt").read_text(),
    ) == (403, [], "scientific data\n")


@pytest.mark.parametrize(
    "setting,value",
    [
        ("SCITEX_STORAGE_TIER_RATES", ["hot"]),
        ("SCITEX_STORAGE_TIER_RATES", {"hot": []}),
        ("SCITEX_STORAGE_TIER_BINDINGS", 1),
        ("SCITEX_STORAGE_TIER_BINDINGS", {"hot": {"volume": []}}),
    ],
)
def test_malformed_tier_config_has_typed_unavailable_response(storage, setting, value):
    # Arrange
    # Act
    with override_settings(**{setting: value}):
        result = [
            logical_move.move(request()),
            logical_move.move_apply(request({"confirm": "yes"}, post=True)),
            logical_move.move_status(request()),
        ]
    # Assert
    assert ([r.status_code for r in result], list(storage["cold"].iterdir())) == (
        [503, 503, 503],
        [],
    )


class ReviewProjectProvider:
    def list_projects(self, request):
        from scitex_sdk.ui.project_scope import ProjectEntry

        return [ProjectEntry("scientist/paper", "Paper")]

    def get_current_project(self, request):
        return None

    def remember_project(self, request, project_id):
        raise AssertionError("Read-only resolution must not remember a project")


class ReviewProjectStorage:
    def project_path(self, project_id, request=None):
        return _ROOTS["hot"] / "registered-project"

    def can_write(self, project_id, request=None):
        return False


class MissingProjectRootStorage(ReviewProjectStorage):
    def project_path(self, project_id, request=None):
        return None


@pytest.mark.parametrize("post", [False, True])
def test_actual_sdk_view_only_project_is_403(storage, post):
    # Arrange
    source = storage["hot"] / "registered-project/section"
    source.mkdir(parents=True)
    (source / "data.txt").write_text("keep")
    # Act
    with override_settings(
        SCITEX_PROJECT_PROVIDER=__name__ + ".ReviewProjectProvider",
        SCITEX_PROJECT_STORAGE=__name__ + ".ReviewProjectStorage",
    ):
        response = (logical_move.move_apply if post else logical_move.move)(
            request(
                {
                    "plan": "1",
                    "confirm": "yes",
                    "scope": "project",
                    "project": "scientist/paper",
                    "source_path": "registered-project/section",
                },
                post=post,
            )
        )
    # Assert
    assert (
        response.status_code,
        b"Editing this project is not permitted" in response.content,
        (source / "data.txt").read_text(),
        list(storage["cold"].iterdir()),
    ) == (403, True, "keep", [])


@pytest.mark.parametrize("storage_path", ["", __name__ + ".MissingProjectRootStorage"])
@pytest.mark.parametrize("post", [False, True])
def test_all_scope_without_complete_project_roots_refuses_transfer(
    storage, storage_path, post
):
    # Arrange
    source = storage["hot"] / "registered-project"
    source.mkdir()
    (source / "data.txt").write_text("keep")
    # Act
    with override_settings(
        SCITEX_PROJECT_PROVIDER=__name__ + ".ReviewProjectProvider",
        SCITEX_PROJECT_STORAGE=storage_path,
    ):
        response = (logical_move.move_apply if post else logical_move.move)(
            request(
                {
                    "plan": "1",
                    "confirm": "yes",
                    "source_path": "registered-project",
                },
                post=post,
            )
        )
    # Assert
    assert (
        response.status_code,
        (source / "data.txt").read_text(),
        list(storage["cold"].iterdir()),
    ) == (403, "keep", [])


@pytest.mark.skipif(not shutil.which("fclones"), reason="real fclones required")
def test_duplicate_report_preserves_total_and_labels_display_subset(storage):
    # Arrange
    for index in range(52):
        payload = (f"scientific-series-{index:03d}\n" * 20).encode()
        for copy in ("a", "b"):
            (storage["hot"] / f"series-{index}-{copy}.dat").write_bytes(payload)
    # Act
    response = organize.duplicates(request())
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        "52 duplicate groups" in body,
        "Showing the first 50 of 52 groups" in body,
        str(storage["hot"]) not in body,
    ) == (200, True, True, True)


def test_example_calculator_keeps_real_rates_availability_and_move_draft(storage):
    # Arrange -- observe genuine function calls; do not replace production code.
    import sys

    calls = []

    def observe(frame, event, arg):
        if (event == "call" and frame.f_globals.get("__name__") == "scitex_storage._transfer._move"
                and frame.f_code.co_name in {"plan_move", "apply_move"}):
            calls.append(frame.f_code.co_name)

    previous = sys.getprofile()
    before = sorted(str(p) for root in storage.values() for p in root.rglob("*"))
    req = request({"example_size_gb": "100", "example_rate_cold": "0.50"})
    # Act
    sys.setprofile(observe)
    try:
        response = logical_move.move(req)
    finally:
        sys.setprofile(previous)
    body = response.content.decode()
    observed = [
        not calls,
        response.status_code == 200,
        'name="scope" value="all"' in body,
        'name="source_path" value="experiment"' in body,
        'name="example_rate_cold" type="text" inputmode="decimal" value="0.50"' in body,
        "USD 2 / GB / 30 days" in body,
        "USD 50.000000" in body,
        "Illustrative USD estimates only" in body,
        "No numeric example rate is assumed" in body,
        'value="warm" disabled' in body,
        sorted(str(p) for root in storage.values() for p in root.rglob("*")) == before,
    ]
    # Assert
    assert all(observed), observed


def test_example_get_preserves_real_sdk_project_scope_without_remembering(storage):
    # Arrange -- this real SDK provider refuses every preference write.
    (storage["hot"] / "registered-project").mkdir()
    # Act
    with override_settings(
        SCITEX_PROJECT_PROVIDER=__name__ + ".ReviewProjectProvider",
        SCITEX_PROJECT_STORAGE=__name__ + ".ReviewProjectStorage",
    ):
        response = logical_move.move(
            request({"scope": "project", "project": "scientist/paper", "example_rate_warm": "0.30"})
        )
    body = response.content.decode()
    observed = [
        response.status_code == 200,
        'name="scope" value="project"' in body,
        'name="project" value="scientist/paper"' in body,
        "USD 30.000000" in body,
        not (storage["cold"] / "experiment").exists(),
    ]
    # Assert
    assert all(observed), observed


def test_fresh_move_controls_share_calculator_owner_without_stale_hidden_copies(storage):
    # Arrange -- inspect the real rendered forms without replacing any view code.
    from html.parser import HTMLParser

    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = []

        def handle_starttag(self, tag, attributes):
            if tag in {"form", "input", "select", "button"}:
                self.nodes.append((tag, dict(attributes)))

    # Act
    parser = Controls()
    parser.feed(logical_move.move(request()).content.decode())
    nodes = parser.nodes
    by_id = {attributes.get("id"): attributes for tag, attributes in nodes if attributes.get("id")}
    owner = "storage-example-move-form"
    move_ids = ["move-source-tier", "move-source-path", "move-destination-tier", "move-destination-path"]
    plan = next(attributes for tag, attributes in nodes if tag == "button" and attributes.get("name") == "plan")
    observed = [
        by_id.get(owner, {}).get("method") == "get",
        all(by_id[key].get("form") == owner and "required" in by_id[key] for key in move_ids),
        plan.get("form") == owner and "formnovalidate" not in plan,
        not any(tag == "input" and attributes.get("type") == "hidden" and attributes.get("name") in {"source_path", "destination_path", "source_tier", "destination_tier"} for tag, attributes in nodes),
        "required" not in by_id["example-size-gb"] and by_id["example-size-gb"].get("inputmode") == "decimal",
        any(tag == "button" and "formnovalidate" in attributes and attributes.get("name") != "plan" for tag, attributes in nodes),
    ]
    # Assert
    assert all(observed), observed


def test_invalid_examples_do_not_block_an_explicit_real_read_only_move_plan(storage):
    # Arrange -- only the owned temporary source and destination can be planned.
    before = sorted(str(p) for root in storage.values() for p in root.rglob("*"))
    # Act
    response = logical_move.move(request({"plan": "1", "example_size_gb": "NaN", "example_rate_warm": "-1"}))
    body = response.content.decode()
    observed = [
        response.status_code == 200,
        "Move plan (not executed)." in body,
        'id="example-size-error"' in body,
        'id="example-rate-error-warm"' in body,
        sorted(str(p) for root in storage.values() for p in root.rglob("*")) == before,
    ]
    # Assert
    assert all(observed), observed
