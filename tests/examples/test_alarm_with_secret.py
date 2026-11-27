"""The systemd decrypt-at-start wrapper, run for real with a throwaway key.

A real gpg home with a passphrase-less key is created under ``tmp_path``, a
real ciphertext is made with it, and the real wrapper execs a real child
that reports whether it received the password. Nothing is mocked. One
assertion per test (STX-TQ007).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "systemd"
    / "scitex-storage-alarm-with-secret.sh"
)
SECRET = "throwaway-test-password"
CHILD = (
    "import os, sys; "
    "v = os.environ.get('SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD'); "
    f"sys.stdout.write('MATCH' if v == {SECRET!r} else 'MISSING' if v is None else 'WRONG')"
)


def _gpg(home: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--quiet", *args],
        capture_output=True,
        check=True,
        timeout=60,
        **kw,
    )


@pytest.fixture
def ciphertext(tmp_path):
    if shutil.which("gpg") is None:
        pytest.skip("gpg is needed to exercise the wrapper")
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    _gpg(
        home,
        "--passphrase",
        "",
        "--quick-gen-key",
        "alarm-test@example.invalid",
        "default",
        "default",
        "never",
    )
    out = tmp_path / "secret.gpg"
    _gpg(
        home,
        "--trust-model",
        "always",
        "--encrypt",
        "-r",
        "alarm-test@example.invalid",
        "-o",
        str(out),
        input=SECRET.encode(),
    )
    yield home, out
    subprocess.run(
        ["gpgconf", "--homedir", str(home), "--kill", "all"],
        capture_output=True,
        check=False,
        timeout=30,
    )


def _wrap(env: dict) -> subprocess.CompletedProcess:
    base = {
        k: v
        for k, v in os.environ.items()
        if k != "SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD"
    }
    return subprocess.run(
        ["/bin/bash", str(WRAPPER), "python3", "-I", "-c", CHILD],
        capture_output=True,
        text=True,
        env={**base, **env},
        timeout=60,
        check=False,
    )


def test_wrapper_hands_the_password_to_the_child(ciphertext):
    # Arrange
    home, cipher = ciphertext
    env = {
        "SCITEX_STORAGE_ALARM_GNUPGHOME": str(home),
        "SCITEX_STORAGE_ALARM_PASSWORD_GPG": str(cipher),
    }
    # Act
    result = _wrap(env)
    # Assert
    assert result.stdout == "MATCH"


def test_wrapper_never_prints_the_password(ciphertext):
    # Arrange
    home, cipher = ciphertext
    env = {
        "SCITEX_STORAGE_ALARM_GNUPGHOME": str(home),
        "SCITEX_STORAGE_ALARM_PASSWORD_GPG": str(cipher),
    }
    # Act
    result = _wrap(env)
    # Assert
    assert SECRET not in result.stdout + result.stderr


def test_undecryptable_secret_still_runs_the_alarm_without_it(tmp_path):
    """No key: the alarm still runs, and the email rail will fail loudly."""
    # Arrange
    env = {
        "SCITEX_STORAGE_ALARM_GNUPGHOME": str(tmp_path / "empty-home"),
        "SCITEX_STORAGE_ALARM_PASSWORD_GPG": str(tmp_path / "absent.gpg"),
    }
    # Act
    result = _wrap(env)
    # Assert
    assert result.stdout == "MISSING"


def test_undecryptable_secret_is_reported_on_stderr(tmp_path):
    # Arrange
    env = {
        "SCITEX_STORAGE_ALARM_GNUPGHOME": str(tmp_path / "empty-home"),
        "SCITEX_STORAGE_ALARM_PASSWORD_GPG": str(tmp_path / "absent.gpg"),
    }
    # Act
    result = _wrap(env)
    # Assert
    assert "could not decrypt" in result.stderr
