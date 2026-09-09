from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class DpapiSecretStore:
    """Small Windows-current-user secret store backed by DPAPI.

    The encrypted file can travel with the portable folder, but Windows can
    decrypt it only under the user account that created it. Plaintext is held
    in process memory only while the application is running.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(data, max(1, len(data)))
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    @staticmethod
    def _protect(data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("API 보안 저장은 Windows DPAPI가 필요합니다.")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        source, keepalive = DpapiSecretStore._blob(data)
        output = _DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "KR DayTrading Terminal", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        _ = keepalive
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("API 보안 저장은 Windows DPAPI가 필요합니다.")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        source, keepalive = DpapiSecretStore._blob(data)
        output = _DataBlob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        _ = keepalive
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    def load(self) -> dict[str, str]:
        with self._lock:
            if not self.path.exists():
                return {}
            raw = self._unprotect(self.path.read_bytes())
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("DPAPI 비밀 저장소 형식이 올바르지 않습니다.")
            return {
                str(key): str(value)
                for key, value in payload.items()
                if isinstance(key, str) and isinstance(value, str) and value
            }

    def save(self, values: dict[str, str]) -> Path:
        clean = {str(key): str(value) for key, value in values.items() if str(value)}
        encrypted = self._protect(json.dumps(clean, ensure_ascii=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_bytes(encrypted)
        os.replace(temp, self.path)
        return self.path

    def update(self, values: dict[str, str]) -> Path:
        with self._lock:
            current = self.load()
            current.update({key: value for key, value in values.items() if value})
            return self.save(current)

    def delete(self, keys: set[str] | None = None) -> None:
        with self._lock:
            if keys is None:
                if self.path.exists():
                    self.path.unlink()
                return
            current = self.load()
            for key in keys:
                current.pop(key, None)
            self.save(current)


def secret_fingerprint(*values: str) -> str:
    joined = "\0".join(value for value in values if value)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest() if joined else ""


class ProviderValidationStore:
    """Non-secret validation outcomes tied to a secret fingerprint."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
            except (OSError, ValueError, TypeError):
                return {}

    def record(self, provider_id: str, fingerprint: str, state: str, message: str) -> dict[str, Any]:
        now = _utcnow()
        with self._lock:
            payload = self.load()
            previous = payload.get(provider_id, {})
            row = {
                "state": state,
                "message": message,
                "fingerprint": fingerprint,
                "last_validation_at": now,
                "last_success_at": now if state == "NORMAL" else previous.get("last_success_at"),
            }
            payload[provider_id] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
            return row

    def status(self, provider_id: str, fingerprint: str) -> dict[str, Any]:
        row = dict(self.load().get(provider_id, {}))
        if not fingerprint:
            return {"state": "NOT_CONFIGURED", "message": "미설정", "last_validation_at": None, "last_success_at": None}
        if not row or row.get("fingerprint") != fingerprint:
            return {"state": "SAVED_UNVERIFIED", "message": "저장됨 / 미검증", "last_validation_at": None, "last_success_at": None}
        row.pop("fingerprint", None)
        return row

    def is_verified(self, provider_id: str, fingerprint: str) -> bool:
        return self.status(provider_id, fingerprint).get("state") == "NORMAL"

