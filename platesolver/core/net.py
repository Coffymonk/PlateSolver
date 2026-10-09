# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Small HTTP helpers (standard library only) shared by online modules."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from platesolver import APP_NAME, __version__

USER_AGENT = f"{APP_NAME}/{__version__} (desktop plate-solving app; Python urllib)"
DEFAULT_TIMEOUT = 60


class NetworkError(RuntimeError):
    def __init__(self, message: str, body: str = ""):
        super().__init__(message)
        self.body = body            # the server's full answer to an HTTP error (e.g. a TAP error VOTable)


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:20000].decode(errors="replace") if hasattr(exc, "read") else ""
        raise NetworkError(f"{req.full_url.split('?')[0]} answered HTTP {exc.code}: {body.strip()[:200]}",
                           body) from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"could not reach {urllib.parse.urlsplit(req.full_url).netloc} ({exc.reason})") from exc
    except TimeoutError as exc:
        raise NetworkError(f"{urllib.parse.urlsplit(req.full_url).netloc} did not answer in time") from exc


def get(url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return _open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout)


def post_form(url: str, data: dict, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"})
    return _open(req, timeout)


def get_json(url: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT):
    return json.loads(get(url, params, timeout).decode("utf-8"))


def post_multipart(url: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]],
                   timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """POST form fields plus files as multipart/form-data. files: name -> (filename, content, mime type)."""
    import uuid
    boundary = "----PlateSolver" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                     f'Content-Type: text/plain\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content, ctype) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), headers={
        "User-Agent": USER_AGENT, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return _open(req, timeout)
