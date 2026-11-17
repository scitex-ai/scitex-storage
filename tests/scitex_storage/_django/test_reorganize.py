"""Reorganize tab: real temp trees prove authorization, preview purity, the
preview-equals-apply guard, apply, and undo through the browser views.

NO MOCKS (PA-306): Django RequestFactory against the real views; hosts are
configured the way test_logical_move configures them.
"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
import django

django.setup()
from django.test import RequestFactory, override_settings  # noqa: E402

from scitex_storage._django import reorganize  # noqa: E402

_ROOTS = {}

PLAN = "moves:\n  - {from: kk/a, to: company/a}\n  - {from: kk, to: company/rest}\n"


def provide_volumes(request):
    return [dict(key="hot", label="private-host-hot", path=_ROOTS["hot"], tier="hot")]


def provide_home(request):
    return _ROOTS["home"]


@pytest.fixture
def storage(tmp_path):
    for name in ("hot", "home"):
        (tmp_path / name).mkdir()
        _ROOTS[name] = tmp_path / name
    repo = _ROOTS["hot"] / "repo"
    (repo / "kk/a").mkdir(parents=True)
    (repo / "kk/a/one.txt").write_text("1")
    (repo / "kk/b.txt").write_text("2")
    (repo / "kk/~$lock.docx").write_text("junk")
    with override_settings(
        SCITEX_STORAGE_VOLUMES_PROVIDER=__name__ + ".provide_volumes",
        SCITEX_USER_HOME_PROVIDER=__name__ + ".provide_home",
        SCITEX_PROJECT_PROVIDER="",
        SCITEX_PROJECT_STORAGE="",
    ):
        yield _ROOTS
    _ROOTS.clear()


def post(data):
    body = {"volume": "hot", "folder": "repo", "plan": PLAN, "scope": "all", **data}
    req = RequestFactory().post("/", body)
    req.user = SimpleNamespace(username="scientist", is_authenticated=True)
    req._dont_enforce_csrf_checks = True
    return req


def snapshot(root):
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if ".old" not in p.parts
    )


def preview_fingerprint(storage):
    resp = reorganize.reorganize_action(post({"action": "preview"}))
    body = resp.content.decode()
    marker = 'name="fingerprint" value="'
    return body.split(marker, 1)[1].split('"', 1)[0]


def test_preview_renders_ok(storage):
    # Arrange
    req = post({"action": "preview"})
    # Act
    resp = reorganize.reorganize_action(req)
    # Assert
    assert resp.status_code == 200


def test_preview_changes_no_files(storage):
    # Arrange
    before = snapshot(storage["hot"])
    # Act
    reorganize.reorganize_action(post({"action": "preview"}))
    # Assert
    assert snapshot(storage["hot"]) == before


def test_preview_never_shows_physical_paths(storage):
    # Arrange
    req = post({"action": "preview"})
    # Act
    body = reorganize.reorganize_action(req).content.decode()
    # Assert
    leaks = [
        body[max(0, i - 160) : i + 60]
        for needle in (str(storage["hot"]), "private-host")
        for i in [body.find(needle)]
        if i != -1
    ]
    assert leaks == [], leaks


def test_volume_root_is_refused(storage):
    # Arrange
    req = post({"action": "preview", "folder": "."})
    # Act
    resp = reorganize.reorganize_action(req)
    # Assert
    assert resp.status_code in (200, 403) and "company" not in snapshot(storage["hot"])


@pytest.mark.parametrize("folder", ["../home", "/etc", "repo/../../home"])
def test_escaping_folder_is_refused(storage, folder):
    # Arrange
    req = post({"action": "apply", "folder": folder, "confirm": "yes"})
    # Act
    resp = reorganize.reorganize_action(req)
    # Assert
    assert (
        resp.status_code in (200, 403)
        and not (storage["hot"] / "repo/company").exists()
    )


def test_unknown_volume_is_forbidden(storage):
    # Arrange
    req = post({"action": "preview", "volume": "scitex-nas-01"})
    # Act
    resp = reorganize.reorganize_action(req)
    # Assert
    assert resp.status_code == 403


def test_apply_without_matching_fingerprint_changes_nothing(storage):
    # Arrange
    before = snapshot(storage["hot"])
    # Act
    reorganize.reorganize_action(
        post({"action": "apply", "confirm": "yes", "fingerprint": "stale"})
    )
    # Assert
    assert snapshot(storage["hot"]) == before


def test_apply_without_confirm_changes_nothing(storage):
    # Arrange
    fp = preview_fingerprint(storage)
    before = snapshot(storage["hot"])
    # Act
    reorganize.reorganize_action(post({"action": "apply", "fingerprint": fp}))
    # Assert
    assert snapshot(storage["hot"]) == before


def test_apply_with_matching_fingerprint_moves_files(storage):
    # Arrange
    fp = preview_fingerprint(storage)
    # Act
    reorganize.reorganize_action(
        post({"action": "apply", "confirm": "yes", "fingerprint": fp})
    )
    # Assert
    assert (storage["hot"] / "repo/company/a/one.txt").read_text() == "1"


def test_apply_writes_manifest_into_jailed_home(storage):
    # Arrange
    fp = preview_fingerprint(storage)
    # Act
    reorganize.reorganize_action(
        post({"action": "apply", "confirm": "yes", "fingerprint": fp})
    )
    # Assert
    assert list(
        (storage["home"] / ".scitex/scitex-storage/runtime/relocate-manifests").glob(
            "*.json"
        )
    )


def test_undo_restores_original_tree(storage):
    # Arrange
    before = snapshot(storage["hot"])
    fp = preview_fingerprint(storage)
    body = reorganize.reorganize_action(
        post({"action": "apply", "confirm": "yes", "fingerprint": fp})
    ).content.decode()
    run_id = body.split('name="run_id" value="', 1)[1].split('"', 1)[0]
    # Act
    reorganize.reorganize_action(post({"action": "undo", "run_id": run_id}))
    # Assert
    assert snapshot(storage["hot"]) == before


def test_undo_rejects_path_like_run_id(storage):
    # Arrange
    req = post({"action": "undo", "run_id": "../../etc/passwd"})
    # Act
    resp = reorganize.reorganize_action(req)
    # Assert
    assert resp.status_code in (200, 403) and b"Run undone" not in resp.content


def test_get_renders_reorganize_tab(storage):
    # Arrange
    req = RequestFactory().get("/", {"tab": "reorganize"})
    req.user = SimpleNamespace(username="scientist", is_authenticated=True)
    # Act
    resp = reorganize.reorganize(req)
    # Assert
    assert b"Reorganize a folder" in resp.content
