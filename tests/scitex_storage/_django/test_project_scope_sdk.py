#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SDK-registered project-scope path (production resolver).

``resolve_project_scope`` with no injected seam resolves through the
SDK-registered host capabilities: the project provider
(``settings.SCITEX_PROJECT_PROVIDER`` via ``scitex_sdk.ui.project_scope``)
for the authorized project id, and the storage capability
(``settings.SCITEX_PROJECT_STORAGE``) for the on-disk root and the write
permission. These tests prove that contract with fakes -- no database, no
hub repo -- mirroring the reference bar (scitex-stats ``_projects``).

Test discipline (PA-307 STX-TQ002/TQ007): every test carries the three AAA
markers and exactly ONE top-level assertion.
"""

from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("django")


def _boot_django_for_storage_gui():
    """Shared helper -- NOT a test, does not itself need AAA markers."""
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings"
    )
    import django

    django.setup()


class _User:
    def __init__(self, username, authenticated=True):
        self.username = username
        self.is_authenticated = authenticated


class _Entry:
    def __init__(self, id, name, detail=""):
        self.id = id
        self.name = name
        self.detail = detail


class _Provider:
    """Request-aware fake: each caller sees only their own entries."""

    def __init__(self, entries, last_visited):
        self._entries = entries
        self._last_visited = last_visited

    def list_projects(self, request):
        return [e for e in self._entries if e.detail == request.user.username]

    def last_visited(self, request):
        return self._last_visited

    def remember(self, request, project_id):
        pass


class _Storage:
    def __init__(self, root, writable=True):
        self._root = root
        self._writable = writable

    def project_path(self, project_id, request):
        return self._root

    def can_write(self, project_id, request):
        return self._writable


def _request(path="/storage/api/list", params=None, username="alice"):
    # Arrange (helper) -- a GET request with an authenticated user.
    from django.test import RequestFactory

    request = RequestFactory().get(path, params or {})
    request.user = _User(username)
    return request


@pytest.fixture()
def _host_caps(monkeypatch):
    """Register a fake host provider + storage capability for one test."""
    # Arrange (fixture) -- importable fakes behind dotted settings.
    _boot_django_for_storage_gui()
    import sys
    import types

    from django.test import override_settings

    root = tempfile.mkdtemp()
    module = types.ModuleType("test_sdk_host_fakes")
    module.Provider = _Provider(
        [
            _Entry("alice/shared", "Shared", "alice"),
            _Entry("bob/shared", "Shared", "bob"),
        ],
        "alice/shared",
    )
    module.Storage = _Storage(root, writable=True)
    monkeypatch.setitem(sys.modules, "test_sdk_host_fakes", module)
    with override_settings(
        SCITEX_PROJECT_PROVIDER="test_sdk_host_fakes.Provider",
        SCITEX_PROJECT_STORAGE="test_sdk_host_fakes.Storage",
    ):
        yield module, root


def test_sdk_path_resolves_last_visited_project(_host_caps):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    assert project_files._GET_CURRENT_PROJECT is None
    request = _request()
    # Act
    scope = project_files.resolve_project_scope(request)
    # Assert
    assert (scope.project_id, scope.slug, scope.name) == (
        "alice/shared",
        "shared",
        "Shared",
    )


def test_sdk_path_honours_accessible_explicit_pick(_host_caps):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    request = _request(params={"project": "alice/shared"})
    # Act
    scope = project_files.resolve_project_scope(request)
    # Assert
    assert scope is not None and scope.project_id == "alice/shared"


def test_sdk_path_denies_explicit_pick_unlisted_for_caller(_host_caps):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    request = _request(params={"project": "alice/shared"}, username="bob")
    # Act
    scope = project_files.resolve_project_scope(request)
    # Assert
    assert scope is None


def test_sdk_path_require_edit_passes_on_literal_true(_host_caps):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    request = _request()
    # Act
    scope = project_files.resolve_project_scope(request, require_edit=True)
    # Assert
    assert scope is not None and scope.project_id == "alice/shared"


def test_sdk_path_require_edit_denies_truthy_non_true(_host_caps, monkeypatch):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    module, _root = _host_caps
    monkeypatch.setattr(module.Storage, "can_write", lambda s, p, r: "false")
    request = _request()
    # Act
    denied = False
    try:
        project_files.resolve_project_scope(request, require_edit=True)
    except project_files.PermissionDenied:
        denied = True
    # Assert
    assert denied is True


def test_sdk_path_require_edit_denies_false(_host_caps, monkeypatch):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    module, _root = _host_caps
    monkeypatch.setattr(module.Storage, "can_write", lambda s, p, r: False)
    request = _request()
    # Act
    denied = False
    try:
        project_files.resolve_project_scope(request, require_edit=True)
    except project_files.PermissionDenied:
        denied = True
    # Assert
    assert denied is True


def test_sdk_path_reads_stay_open_when_not_writable(_host_caps, monkeypatch):
    # Arrange
    _boot_django_for_storage_gui()
    from scitex_storage._django import project_files

    module, _root = _host_caps
    monkeypatch.setattr(module.Storage, "can_write", lambda s, p, r: False)
    request = _request()
    # Act
    scope = project_files.resolve_project_scope(request)
    # Assert
    assert scope is not None and scope.project_id == "alice/shared"


def test_sdk_path_closed_without_registered_provider():
    # Arrange
    _boot_django_for_storage_gui()
    from django.test import override_settings

    from scitex_storage._django import project_files

    request = _request()
    # Act
    with override_settings(SCITEX_PROJECT_PROVIDER="", SCITEX_PROJECT_STORAGE=""):
        scope = project_files.resolve_project_scope(request)
    # Assert
    assert scope is None


def test_sdk_path_anonymous_is_closed(_host_caps):
    # Arrange
    _boot_django_for_storage_gui()
    from django.test import RequestFactory

    from scitex_storage._django import project_files

    request = RequestFactory().get("/storage/api/list")
    request.user = _User("anon", authenticated=False)
    # Act
    scope = project_files.resolve_project_scope(request)
    # Assert
    assert scope is None


# EOF
