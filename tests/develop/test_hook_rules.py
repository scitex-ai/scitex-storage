"""Leaf-scope hooks provider: storage declares its own guardrails."""

from __future__ import annotations

from scitex_dev.hooks import HookRule
from scitex_dev.hooks._discover import discover_hooks

from scitex_storage._hook_rules import provide


def test_provide_declares_storage_scope():
    # Arrange
    # Act
    rules = provide()
    # Assert
    assert len(rules) >= 4


def test_provide_yields_hook_rules_only():
    # Arrange
    # Act
    rules = provide()
    # Assert
    assert all(isinstance(r, HookRule) for r in rules)


def test_rule_ids_are_unique():
    # Arrange
    # Act
    ids = [r.id for r in provide()]
    # Assert
    assert len(set(ids)) == len(ids)


def test_rule_ids_are_storage_namespaced():
    # Arrange
    # Act
    ids = [r.id for r in provide()]
    # Assert
    assert all(i.startswith("storage.") for i in ids)


def test_every_rule_states_its_reason():
    # Arrange
    # Act
    rules = provide()
    # Assert
    assert all(r.reason.strip() for r in rules)


def test_every_rule_is_traceable():
    # Arrange
    # Act
    rules = provide()
    # Assert
    assert all(r.implemented_in.strip() or r.script or r.check for r in rules)


def test_provider_survives_federated_discovery():
    # Arrange
    # Act
    corpus = discover_hooks(include_entry_points=False, extra_providers=[provide])
    # Assert
    assert len([r for r in corpus if r.id.startswith("storage.")]) == len(provide())
