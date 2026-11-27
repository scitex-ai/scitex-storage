#!/bin/bash
# -*- coding: utf-8 -*-
# File: examples/systemd/scitex-storage-alarm-with-secret.sh
#
# Decrypt-at-start wrapper for scitex-storage-alarm.service.
#
# The email rail's SMTP password stays ciphertext at rest (~/.pw/*.gpg, the
# fleet secrets model). This wrapper decrypts it for ONE process only: it
# goes into SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD in the environment of
# the exec'd alarm, never into a file, never echoed. It does not source any
# shell secrets loader, so no other credential reaches the alarm.
#
# Usage (ExecStart):  scitex-storage-alarm-with-secret.sh <command> [args...]
#
# Configuration (all optional, set in alarm.env):
#   SCITEX_STORAGE_ALARM_PASSWORD_GPG   ciphertext  (default ~/.pw/no-reply@scitex.ai.gpg)
#   SCITEX_STORAGE_ALARM_GNUPGHOME      gpg --homedir for that ciphertext
#                                       (default: gpg's own default home)
#
# If decryption fails (no key, or a key that needs a passphrase -- --batch
# never prompts), the alarm STILL RUNS without the password: the gather and
# any other rail keep working, and the email rail fails loudly by name
# (exit 30 when no rail delivered). It never falls back to a plaintext copy.

set -uo pipefail
set +x

cipher="${SCITEX_STORAGE_ALARM_PASSWORD_GPG:-$HOME/.pw/no-reply@scitex.ai.gpg}"
gpg_args=(--quiet --batch --no-tty --decrypt)
if [[ -n "${SCITEX_STORAGE_ALARM_GNUPGHOME:-}" ]]; then
    gpg_args=(--homedir "$SCITEX_STORAGE_ALARM_GNUPGHOME" "${gpg_args[@]}")
fi

if [[ $# -eq 0 ]]; then
    echo "usage: $0 <command> [args...]" >&2
    exit 2
fi

if password="$(gpg "${gpg_args[@]}" "$cipher" 2>/dev/null)" && [[ -n "$password" ]]; then
    export SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD="$password"
else
    echo "scitex-storage-alarm-with-secret: could not decrypt $cipher non-interactively;" \
        "the email rail will fail (no SMTP password)" >&2
fi
unset password

exec "$@"

# EOF
