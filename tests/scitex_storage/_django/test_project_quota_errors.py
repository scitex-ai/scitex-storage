"""Native capacity failures through shipped project APIs; no database."""

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    import django
    django.setup()
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.test import RequestFactory
    from scitex_storage._django import project_files, views

    def forbidden(*args, **kwargs):
        raise AssertionError("Database access forbidden in quota error tests")

    monkeypatch.setattr(BaseDatabaseWrapper, "connect", forbidden)
    root = tmp_path / "synthetic-project"
    root.mkdir()
    (root / "result.txt").write_text("prior valid output")
    project = SimpleNamespace(
        id="synthetic-project", slug="fixture", name="fixture",
        get_local_path=lambda: root,
    )
    monkeypatch.setattr(project_files, "_GET_CURRENT_PROJECT", lambda *a, **k: project)
    factory = RequestFactory()

    def request(body=None):
        result = factory.post("/api/write", data=json.dumps(body or {}),
                              content_type="application/json")
        result.user = SimpleNamespace(is_authenticated=True, username="fixture")
        return result

    return SimpleNamespace(root=root, files=project_files, views=views,
                           request=request, factory=factory)


CAPACITY = [(errno.EDQUOT, "quota_exceeded", 507),
            (errno.ENOSPC, "disk_full", 507)]


@pytest.mark.parametrize("phase", ["mkdir", "mkstemp", "fsync", "replace"])
@pytest.mark.parametrize("number,code,status", CAPACITY + [(errno.EIO, "write_error", 500)])
def test_native_write_failure_is_typed_and_preserves_prior_output(
    scoped, monkeypatch, phase, number, code, status
):
    # Arrange
    def fail(*args, **kwargs):
        raise OSError(number, "synthetic native failure", str(scoped.root / "private-path"))

    if phase == "mkdir":
        original = Path.mkdir
        def fail_parent(path, *args, **kwargs):
            if path == scoped.root:
                return fail()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "mkdir", fail_parent)
    elif phase == "mkstemp":
        monkeypatch.setattr(scoped.files.tempfile, "mkstemp", fail)
    else:
        monkeypatch.setattr(scoped.files.os, {"fsync": "fsync", "replace": "replace"}[phase], fail)
    # Act
    response = scoped.views.project_write(scoped.request({"path": "result.txt", "content": "new output"}))
    payload = json.loads(response.content)
    observed = (response.status_code, payload["error"],
                (scoped.root / "result.txt").read_text(),
                sorted(path.name for path in scoped.root.iterdir()),
                code != "quota_exceeded" or str(scoped.root) not in payload["message"])
    # Assert
    assert observed == (status, code, "prior valid output", ["result.txt"], True)


@pytest.mark.parametrize("number,code,status", CAPACITY + [(errno.EIO, "write_error", 500)])
def test_native_directory_capacity_error_has_typed_response(scoped, monkeypatch, number, code, status):
    # Arrange
    original = Path.mkdir
    def fail_target(path, *args, **kwargs):
        if path == scoped.root / "new-folder":
            raise OSError(number, "synthetic native failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_target)
    # Act
    response = scoped.views.project_mkdir(scoped.request({"path": "new-folder"}))
    payload = json.loads(response.content)
    # Assert
    assert (response.status_code, payload["error"], (scoped.root / "new-folder").exists()) == (status, code, False)


@pytest.mark.parametrize("operation", ["read", "rename"])
@pytest.mark.parametrize("number,code,status", CAPACITY)
def test_native_sdk_capacity_errors_reach_typed_api(scoped, monkeypatch, operation, number, code, status):
    # Arrange
    class Backend:
        def read(self, *args, **kwargs):
            raise OSError(number, "synthetic SDK native failure")
        def rename(self, *args, **kwargs):
            raise OSError(number, "synthetic SDK native failure")
    monkeypatch.setattr(scoped.files, "build_backend", lambda scope: Backend())
    # Act
    if operation == "rename":
        response = scoped.views.project_rename(scoped.request({"old_path": "result.txt", "new_path": "moved.txt"}))
    else:
        request = scoped.factory.get("/api/read", {"path": "result.txt"})
        request.user = SimpleNamespace(is_authenticated=True)
        response = scoped.views.project_read(request)
    # Assert
    assert (response.status_code, json.loads(response.content)["error"], (scoped.root / "result.txt").read_text()) == (status, code, "prior valid output")


@pytest.mark.parametrize("number,code,status", CAPACITY)
def test_native_delete_capacity_error_does_not_claim_success(scoped, monkeypatch, number, code, status):
    # Arrange
    original = os.unlink
    def fail_entry(path, *args, **kwargs):
        if path == "result.txt" and kwargs.get("dir_fd") is not None:
            raise OSError(number, "synthetic native failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", fail_entry)
    # This injected wrapper has the same real dir_fd capability as os.unlink.
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail_entry})
    # Act
    response = scoped.views.project_delete(scoped.request({"path": "result.txt"}))
    # Assert
    assert (response.status_code, json.loads(response.content)["error"], (scoped.root / "result.txt").read_text()) == (status, code, "prior valid output")


@pytest.mark.parametrize("number,code,status", CAPACITY)
def test_delete_staging_capacity_failure_preserves_directory(scoped, monkeypatch, number, code, status):
    # Arrange
    original = os.mkdir
    directory = scoped.root / "folder"
    directory.mkdir()
    (directory / "kept.txt").write_text("prior valid output")
    def fail_stage(path, *args, **kwargs):
        if str(path).startswith(".scitex-storage-delete-"):
            raise OSError(number, "synthetic native staging failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", fail_stage)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {fail_stage})
    # Act
    response = scoped.views.project_delete(scoped.request({"path": "folder"}))
    # Assert
    assert (response.status_code, json.loads(response.content)["error"], (directory / "kept.txt").read_text()) == (status, code, "prior valid output")
