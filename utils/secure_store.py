"""Small Windows-only secret helper backed by the current user's DPAPI key."""

from __future__ import annotations

import base64


class SecretStoreError(RuntimeError):
    """Raised when a locally saved API credential cannot be protected or read."""


try:
    import win32crypt
except ImportError:  # pragma: no cover - the packaged application targets Windows
    win32crypt = None


def protect_secret(value: str) -> str:
    """Return a DPAPI-encrypted, JSON-safe secret for the current Windows user."""
    secret = str(value or "").strip()
    if not secret:
        return ""
    if win32crypt is None:
        raise SecretStoreError("当前 Windows 环境无法使用系统凭据保护。")
    try:
        encrypted = win32crypt.CryptProtectData(
            secret.encode("utf-8"),
            "科研助手 API 密钥",
            None,
            None,
            None,
            0,
        )
        # pywin32 returns bytes on current Windows builds; retain support for
        # older tuple-shaped bindings without assuming either form.
        if isinstance(encrypted, tuple):
            encrypted = encrypted[-1]
        return base64.b64encode(bytes(encrypted)).decode("ascii")
    except Exception as error:  # noqa: BLE001 - third-party Win32 errors have no stable common base class
        raise SecretStoreError("无法使用 Windows 凭据保护 API 密钥。") from error


def reveal_secret(value: str) -> str:
    """Decrypt a DPAPI token. Empty tokens remain empty."""
    token = str(value or "").strip()
    if not token:
        return ""
    if win32crypt is None:
        raise SecretStoreError("当前 Windows 环境无法读取已保存的 API 密钥。")
    try:
        encrypted = base64.b64decode(token.encode("ascii"), validate=True)
        plain = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        if isinstance(plain, tuple):
            plain = plain[-1]
        return bytes(plain).decode("utf-8")
    except Exception as error:  # noqa: BLE001 - do not expose cryptographic implementation details to the UI
        raise SecretStoreError("已保存的 API 密钥无法读取；请在设置中重新填写。") from error
