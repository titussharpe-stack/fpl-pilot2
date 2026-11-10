"""Paths and credential handling.

The Fantasy Football Scout login is never stored in code or plain files.
Lookup order:
  1. FFS_USERNAME / FFS_PASSWORD environment variables (for servers / cloud runs)
  2. The operating system's password vault via `keyring`
     (macOS Keychain, Windows Credential Manager, Linux Secret Service).
Store it in the vault with:  python -m fpl_planner login
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FPL_PLANNER_DATA", ROOT / "data"))
PROJ_DIR = DATA_DIR / "projections"
DEBUG_DIR = DATA_DIR / "debug"

KEYRING_SERVICE = "fpl-planner-ffs"
USER_KEY = "__username__"


def get_ffs_credentials() -> tuple[str, str] | None:
    user, pw = os.environ.get("FFS_USERNAME"), os.environ.get("FFS_PASSWORD")
    if user and pw:
        return user, pw
    try:
        import keyring

        user = keyring.get_password(KEYRING_SERVICE, USER_KEY)
        if user:
            pw = keyring.get_password(KEYRING_SERVICE, user)
            if pw:
                return user, pw
    except Exception:
        pass
    return None


def save_ffs_credentials(user: str, password: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, USER_KEY, user)
    keyring.set_password(KEYRING_SERVICE, user, password)


def delete_ffs_credentials() -> None:
    import keyring

    user = keyring.get_password(KEYRING_SERVICE, USER_KEY)
    if user:
        keyring.delete_password(KEYRING_SERVICE, user)
        keyring.delete_password(KEYRING_SERVICE, USER_KEY)
