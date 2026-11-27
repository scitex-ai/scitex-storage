"""Usage uses authorized host roots, never free-form navigation paths."""

import os
from types import SimpleNamespace
from urllib.parse import quote

import pytest

_CATALOG = {}


def usage_test_volumes(request):
    """A registered host capability with only the authenticated owner's volume."""
    user = getattr(request, "user", None)
    owner = getattr(user, "username", "")
    if not getattr(user, "is_authenticated", False) or owner not in _CATALOG:
        return []
    return [
        {
            "key": "workspace",
            "label": "Workspace",
            "path": _CATALOG[owner],
            "machine": "owned-fixture",
        }
    ]


class UsageProjects:
    """Real SDK provider port backed exclusively by temporary fixture roots."""

    def list_projects(self, request):
        from scitex_sdk.ui.project_scope import ProjectEntry

        owner = request.user.username
        if request.user.is_authenticated and owner in _CATALOG:
            return [ProjectEntry(owner + "/paper", "Paper")]
        return []

    def get_current_project(self, request):
        return request.user.username + "/paper"

    def remember_project(self, request, project_id):
        raise AssertionError("Usage GET must not write the remembered project")


class UsageProjectStorage:
    def project_path(self, project_id, request=None):
        from django.core.exceptions import PermissionDenied

        owner = request.user.username
        if project_id != owner + "/paper" or owner not in _CATALOG:
            raise PermissionDenied("Project is not visible to this owner")
        return _CATALOG[owner] / "paper"

    def can_write(self, project_id, request=None):
        return False


@pytest.fixture
def usage_scope(tmp_path):
    import django
    from django.test import override_settings

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
    django.setup()
    from scitex_storage._django import project_files

    if project_files._GET_CURRENT_PROJECT is not None:
        raise RuntimeError("This test must exercise the actual SDK adapter")
    for owner in ("alice", "bob"):
        root = tmp_path / owner
        (root / "paper").mkdir(parents=True)
        (root / (owner + "-only.bin")).write_bytes(b"x" * 1024)
        (root / "paper" / (owner + "-paper-only.bin")).write_bytes(b"x" * 2048)
        _CATALOG[owner] = root
    with override_settings(
        SCITEX_APP_MODE="hosted",
        SCITEX_STORAGE_VOLUMES_PROVIDER=__name__ + ".usage_test_volumes",
        SCITEX_PROJECT_PROVIDER=__name__ + ".UsageProjects",
        SCITEX_PROJECT_STORAGE=__name__ + ".UsageProjectStorage",
    ):
        yield _CATALOG
    _CATALOG.clear()


def _usage(query):
    from django.test import RequestFactory

    from scitex_storage._django import organize

    request = RequestFactory().get("/usage-sunburst/?" + query)
    request.user = SimpleNamespace(username="alice", is_authenticated=True)
    return organize.usage_sunburst(request)


def _contents(catalog):
    return {
        str(p): p.read_bytes()
        for root in catalog.values()
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "query",
    [
        "scope=all&volume=../bob",
        "scope=all&volume=%2e%2e%2fbob",
        "scope=all&volume=%252e%252e%252fbob",
        "scope=all&volume={foreign}",
        "scope=all&volume=foreign",
        "scope=project&project=bob%2Fpaper",
        "scope=project&project=..%2Fbob",
        "scope=project&project=%252e%252e%252fbob",
        "scope=project&project={foreign}",
    ],
)
def test_usage_foreign_and_traversal_selectors_are_refused(usage_scope, query):
    # Arrange -- every possible outside path is another owned temporary fixture.
    before = _contents(usage_scope)
    query = query.replace("{foreign}", quote(str(usage_scope["bob"]), safe=""))
    # Act
    response = _usage(query)
    # Assert
    assert (response.status_code, _contents(usage_scope)) == (403, before)


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "query",
    [
        "scope=all&volume=workspace&path=../bob",
        "scope=all&volume=workspace&path=%2e%2e%2fbob",
        "scope=all&volume=workspace&root={foreign}",
        "scope=all&volume=workspace&path={foreign}",
    ],
)
def test_usage_free_path_queries_cannot_change_authorized_volume(usage_scope, query):
    # Arrange
    query = query.replace("{foreign}", quote(str(usage_scope["bob"]), safe=""))
    before = _contents(usage_scope)
    # Act
    response = _usage(query)
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        "alice-only.bin" in body,
        "bob-only.bin" in body,
        "bob-paper-only.bin" in body,
        _contents(usage_scope),
    ) == (200, True, False, False, before)


@pytest.mark.requires_fd
def test_usage_project_stays_narrower_than_the_owned_volume(usage_scope):
    # Arrange
    before = _contents(usage_scope)
    # Act
    response = _usage("scope=project&project=alice%2Fpaper&path=..%2F..%2Fbob")
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        "alice-paper-only.bin" in body,
        "alice-only.bin" in body,
        "bob-only.bin" in body,
        _contents(usage_scope),
    ) == (200, True, False, False, before)


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "query",
    [
        "scope=all&volume=workspace",
        "scope=project&project=alice%2Fpaper",
    ],
)
def test_usage_does_not_recurse_escaping_directory_symlinks(usage_scope, query):
    # Arrange -- links may be inventoried as entries, never as target subtrees.
    (usage_scope["alice"] / "escape").symlink_to(
        usage_scope["bob"], target_is_directory=True
    )
    (usage_scope["alice"] / "paper" / "escape").symlink_to(
        usage_scope["bob"], target_is_directory=True
    )
    before = _contents(usage_scope)
    # Act
    response = _usage(query)
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        "bob-only.bin" in body,
        "bob-paper-only.bin" in body,
        _contents(usage_scope),
    ) == (200, False, False, before)
