"""Scoped server-rendered grouping with actual SDK providers and fclones."""

from types import SimpleNamespace

import pytest
from test_organize import _contents
from test_organize import usage_scope as usage_scope


@pytest.fixture
def catalog(usage_scope):
    for owner, root in usage_scope.items():
        for relative in ["paper/a/one", "paper/b/two", "outside/three"]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((owner + "-same-content").encode())
    return usage_scope


def _get(query):
    from django.test import RequestFactory
    from django.test.signals import template_rendered
    from django.test.utils import setup_test_environment

    from scitex_storage._django.views import index

    try:
        setup_test_environment()
    except RuntimeError:
        pass
    contexts = []

    def capture(sender, template, context, **kwargs):
        if template.name == "scitex_storage/organize.html":
            contexts.append(context.flatten())

    template_rendered.connect(capture, weak=False)
    try:
        request = RequestFactory().get("/", {"tab": "duplicates", **query})
        request.user = SimpleNamespace(username="alice", is_authenticated=True)
        response = index(request)
    finally:
        template_rendered.disconnect(capture)
    return response, contexts[0] if contexts else None


@pytest.mark.parametrize("by", ["", "parent", "top-level"])
def test_grouping_retains_authorized_owner_and_display_paths(catalog, by):
    # Arrange
    query = {"scope": "all", "group_by": by}
    # Act
    response, context = _get(query)
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        context["group_total"],
        context["groups"][0]["count"],
        "/home/alice/" in body,
        str(catalog["alice"]) in body,
        "bob-same-content" in body,
        "/home/bob" in body,
    ) == (200, 1, 3, True, False, False, False)


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_grouping_form_preserves_project_and_does_not_expand_scan(catalog, by):
    # Arrange
    query = {"scope": "project", "project": "alice/paper", "group_by": by}
    # Act
    response, context = _get(query)
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        context["groups"][0]["count"],
        'name="scope" value="project"' in body,
        'name="project" value="alice/paper"' in body,
        f'value="{by}" selected' in body,
        "outside/three" in body,
    ) == (200, 2, True, True, True, False)


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_foreign_project_stays_forbidden(catalog, by):
    # Arrange
    query = {"scope": "project", "project": "bob/paper", "group_by": by}
    # Act
    response, _ = _get(query)
    # Assert
    assert response.status_code == 403


def test_invalid_grouping_is_a_bad_request(catalog):
    # Arrange
    query = {"scope": "all", "group_by": "../../bob"}
    # Act
    response, _ = _get(query)
    # Assert
    assert (
        response.status_code,
        b"Unsupported duplicate grouping" in response.content,
    ) == (400, True)


def test_grouping_keeps_the_ten_path_display_limit(catalog):
    # Arrange
    root = catalog["alice"] / "paper"
    for index in range(15):
        (root / f"extra-{index:02d}").write_bytes(b"truncation-content")
    # Act
    response, context = _get(
        {"scope": "project", "project": "alice/paper", "group_by": "parent"}
    )
    group = context["groups"][0]
    # Assert
    assert (
        response.status_code,
        group["count"],
        group["extra"],
        sum(len(loc["paths"]) for loc in group["locations"]),
    ) == (200, 15, 5, 10)


def test_grouping_keeps_the_fifty_group_display_limit(catalog):
    # Arrange
    root = catalog["alice"] / "paper"
    for index in range(51):
        content = f"content-{index:03d}".encode()
        (root / "a" / f"many-{index:03d}").write_bytes(content)
        (root / "b" / f"many-{index:03d}").write_bytes(content)
    # Act
    response, context = _get(
        {"scope": "project", "project": "alice/paper", "group_by": "top-level"}
    )
    # Assert
    assert (
        response.status_code,
        context["group_total"],
        len(context["groups"]),
        context["truncated"],
    ) == (200, 52, 50, True)


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_grouping_does_not_follow_an_escaping_directory_link(catalog, by):
    # Arrange
    foreign = catalog["bob"] / "secret"
    foreign.write_bytes(b"alice-same-content")
    (catalog["alice"] / "paper/escape").symlink_to(
        catalog["bob"], target_is_directory=True
    )
    # Act
    response, context = _get(
        {"scope": "project", "project": "alice/paper", "group_by": by}
    )
    # Assert
    assert (
        response.status_code,
        context["group_total"],
        context["groups"][0]["count"],
        "secret" in response.content.decode(),
    ) == (200, 1, 2, False)


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_location_labels_are_html_escaped(catalog, by):
    # Arrange
    folder = catalog["alice"] / "paper" / "<img onerror=alert(1)>"
    folder.mkdir()
    (folder / "data").write_bytes(b"alice-same-content")
    # Act
    response, _ = _get({"scope": "project", "project": "alice/paper", "group_by": by})
    body = response.content.decode()
    # Assert
    assert (
        response.status_code,
        "<img onerror=alert(1)>" in body,
        "&lt;img onerror=alert(1)&gt;" in body,
    ) == (200, False, True)


@pytest.mark.parametrize("by", ["", "parent", "top-level"])
def test_report_leaves_owned_files_intact_and_creates_no_archive(catalog, by):
    # Arrange
    before = _contents(catalog)
    # Act
    response, _ = _get({"scope": "all", "group_by": by})
    # Assert
    assert (
        response.status_code,
        _contents(catalog),
        [path for root in catalog.values() for path in root.rglob(".old")],
    ) == (200, before, [])
