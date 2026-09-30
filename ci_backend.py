"""Owned Phase 2C lab service and server-side HAR capture.

Captures record actual received HTTP requests. They do not attest client runtime.
No listener starts on import; the default bind is loopback.
"""

import argparse
import base64
import ipaddress
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def document(origin):
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "owner": {"type": "string"},
            "name": {"type": "string", "minLength": 1, "maxLength": 20},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["id", "owner", "name", "quantity"],
    }
    body = {
        "type": "object",
        "properties": {k: v for k, v in item["properties"].items() if k in {"name", "quantity"}},
        "required": ["name", "quantity"],
    }

    def operation(schema, *, parameters=None, request=False, success="200"):
        value = {
            "security": [{"LabBearer": []}],
            "responses": {
                success: {
                    "description": "Observed lab response",
                    "content": {"application/json": {"schema": schema}},
                },
                **{
                    str(code): {"description": "Controlled rejection"}
                    for code in (400, 401, 403, 404, 405, 415, 422, 500)
                },
            },
        }
        if parameters:
            value["parameters"] = parameters
        if request:
            value["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": body}},
            }
        return value

    return {
        "openapi": "3.0.3",
        "info": {"title": "Owned iOS integration lab", "version": "1"},
        "servers": [{"url": origin}],
        "components": {"securitySchemes": {"LabBearer": {"type": "http", "scheme": "bearer"}}},
        "paths": {
            "/me": {
                "get": operation(
                    {
                        "type": "object",
                        "required": ["subject", "role"],
                        "properties": {"subject": {"type": "string"}, "role": {"type": "string"}},
                    }
                )
            },
            "/items": {
                "get": operation(
                    {"type": "array", "items": item},
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        {
                            "name": "search",
                            "in": "query",
                            "schema": {"type": "string", "minLength": 1, "maxLength": 20},
                        },
                    ],
                ),
                "post": operation(item, request=True, success="201"),
            },
            "/items/{itemId}": {
                "parameters": [
                    {
                        "name": "itemId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "get": operation(item),
                "put": operation(item, request=True),
                "patch": operation(item, request=True),
            },
            "/admin/report": {
                "get": operation(
                    {
                        "type": "object",
                        "required": ["report"],
                        "properties": {"report": {"type": "string"}},
                    }
                )
            },
            "/diagnostics": {"get": operation({"type": "object"}, success="500")},
        },
    }


class Lab:
    def __init__(self, capture_dir, public_origin=None):
        self.public_origin = public_origin
        self.capture_dir = Path(capture_dir)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tokens = {identity: secrets.token_urlsafe(32) for identity in ("alice", "bob")}
        self.expiring = secrets.token_urlsafe(32)
        self.expires_at = time.time() + 1
        self.items = {
            101: {"id": 101, "owner": "alice", "name": "desk", "quantity": 2},
            202: {"id": 202, "owner": "bob", "name": "lamp", "quantity": 1},
        }
        self.captures = {}

    def identity(self, authorization):
        credential = authorization.removeprefix("Bearer ")
        if credential == self.expiring:
            return "alice" if time.time() < self.expires_at else None
        return next(
            (
                who
                for who, token in self.tokens.items()
                if secrets.compare_digest(token, credential)
            ),
            None,
        )

    def save_context(self, origin):
        # Issued once, then allowed to expire naturally; no JWT/signature editing.
        context = {
            "origin": origin,
            "alice": self.tokens["alice"],
            "bob": self.tokens["bob"],
            "expiredCredential": self.expiring,
            "expiredAt": datetime.fromtimestamp(self.expires_at, timezone.utc).isoformat(),
            "rolePolicy": {
                "method": "GET",
                "pathTemplate": "/admin/report",
                "referenceIdentity": "alice",
                "comparisonIdentity": "bob",
                "ownershipSensitive": "No",
                "expectedAccess": "DENY",
            },
            "objectPolicy": {
                "method": "GET",
                "pathTemplate": "/items/{itemId}",
                "callerIdentity": "alice",
                "foreignOwnerIdentity": "bob",
                "ownedObject": "101",
                "foreignObject": "202",
                "location": "path",
                "field": "itemId",
                "expectedAccess": "DENY",
            },
            "identityValidationPath": "/me",
        }
        (self.capture_dir / "private-context.json").write_text(
            json.dumps(context, indent=2), encoding="utf-8"
        )

    def record(self, session_id, entry):
        if not re.fullmatch(r"[a-f0-9]{32}", session_id):
            return
        with self.lock:
            records = self.captures.setdefault(session_id, [])
            records.append(entry)
            path = self.capture_dir / (session_id + ".har")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "log": {
                            "version": "1.2",
                            "creator": {
                                "name": "TCGen controlled backend HTTP capture",
                                "version": "1",
                            },
                            "entries": records,
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)


def handler(lab):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def handle_request(self):
            self.started = datetime.now(timezone.utc).isoformat()
            self.timer = time.monotonic()
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.raw_body = b""
                return self.reply(400, {"error": "invalid length"})
            if not 0 <= length <= 1024 * 1024:
                self.raw_body = b""
                return self.reply(413, {"error": "body too large"})
            self.raw_body = self.rfile.read(length)
            self.origin = lab.public_origin or "http://" + self.headers.get("Host", "127.0.0.1:8879")
            path = urlsplit(self.path).path
            if path == "/openapi.json" and self.command == "GET":
                return self.reply(200, document(self.origin))
            if self.command == "SECURITYCHECK":
                return self.reply(405, {"error": "unsupported method"})
            body = None
            if self.command in {"POST", "PUT", "PATCH"}:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.reply(415, {"error": "application/json required"})
                try:
                    body = json.loads(self.raw_body)
                    if not isinstance(body, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    return self.reply(400, {"error": "malformed JSON"})
            if path == "/login" and self.command == "POST":
                who = body.get("identity")
                if who not in lab.tokens:
                    return self.reply(400, {"error": "unknown lab identity"})
                return self.reply(200, {"token": lab.tokens[who]})
            who = lab.identity(self.headers.get("Authorization", ""))
            if not who:
                return self.reply(401, {"error": "invalid or expired credential"})
            if path == "/me" and self.command == "GET":
                return self.reply(
                    200, {"subject": who, "role": "admin" if who == "alice" else "user"}
                )
            if path == "/logout" and self.command == "POST":
                return self.reply(200, {"loggedOut": True})
            if path == "/diagnostics":
                return self.reply(500, {"error": "controlled error response"})
            if path == "/admin/report":
                return (
                    self.reply(200, {"report": "owned lab report"})
                    if who == "alice"
                    else self.reply(403, {"error": "admin required"})
                )
            with lab.lock:
                if path == "/items" and self.command == "GET":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    try:
                        limit = int(query.get("limit", ["2"])[0])
                        if not 1 <= limit <= 5 or (
                            "search" in query and not 1 <= len(query["search"][0]) <= 20
                        ):
                            raise ValueError()
                    except ValueError:
                        return self.reply(422, {"error": "declared query boundary"})
                    return self.reply(
                        200, [v for v in lab.items.values() if v["owner"] == who][:limit]
                    )
                match = re.fullmatch(r"/items/(\d+)", path)
                key = int(match[1]) if match else None
                if key is not None:
                    if key not in lab.items:
                        return self.reply(404, {"error": "unknown item"})
                    if lab.items[key]["owner"] != who:
                        return self.reply(403, {"error": "foreign owner"})
                    if self.command == "GET":
                        return self.reply(200, lab.items[key])
                if (path == "/items" and self.command == "POST") or (
                    key is not None and self.command in {"PUT", "PATCH"}
                ):
                    if (
                        not isinstance(body.get("name"), str)
                        or not 1 <= len(body["name"]) <= 20
                        or type(body.get("quantity")) is not int
                        or not 1 <= body["quantity"] <= 5
                    ):
                        return self.reply(422, {"error": "declared body boundary"})
                    created = key is None
                    key = max(lab.items) + 1 if created else key
                    lab.items[key] = {
                        "id": key,
                        "owner": who,
                        "name": body["name"],
                        "quantity": body["quantity"],
                    }
                    return self.reply(201 if created else 200, lab.items[key])
            self.reply(404, {"error": "unknown lab route"})

        def reply(self, status, value):
            body = json.dumps(value, separators=(",", ":")).encode()
            response_headers = [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ]
            self.send_response_only(status)
            for key, value in response_headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            origin = lab.public_origin or "http://" + self.headers.get("Host", "127.0.0.1:8879")
            lab.record(
                self.headers.get("X-Dashboard-Session", ""),
                {
                    "startedDateTime": self.started,
                    "_capturePoint": "Owned backend ingress after configured TLS reverse proxy"
                    if lab.public_origin else "Owned backend HTTP ingress",
                    "time": round((time.monotonic() - self.timer) * 1000, 3),
                    "request": {
                        "method": self.command,
                        "url": origin + self.path,
                        "httpVersion": self.request_version,
                        "headers": [{"name": k, "value": v} for k, v in self.headers.raw_items()],
                        "bodySize": len(self.raw_body),
                        "_bodyBase64": base64.b64encode(self.raw_body).decode(),
                    },
                    "response": {
                        "status": status,
                        "httpVersion": self.protocol_version,
                        "headers": [{"name": k, "value": v} for k, v in response_headers],
                        "bodySize": len(body),
                        "content": {
                            "mimeType": "application/json",
                            "size": len(body),
                            "text": base64.b64encode(body).decode(),
                            "encoding": "base64",
                        },
                    },
                },
            )

        do_GET = do_POST = do_PUT = do_PATCH = do_SECURITYCHECK = handle_request

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--capture-dir", type=Path, default=Path("output/phase2c/runtime-capture"))
    parser.add_argument("--public-origin", help="Exact owned HTTPS origin of the configured reverse proxy")
    args = parser.parse_args()
    address = ipaddress.ip_address(args.bind)
    if not (address.is_loopback or address.is_private) or address.is_unspecified:
        parser.error("Bind to loopback or a specific authorized private lab address.")
    if args.public_origin:
        parsed = urlsplit(args.public_origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            parser.error("Public origin must be an exact HTTPS origin without path or credentials.")
    lab = Lab(args.capture_dir, args.public_origin)
    lab.save_context(args.public_origin or f"http://{args.bind}:{args.port}")
    print(
        "Controlled backend ready. Private context is saved locally; credentials are not logged.",
        flush=True,
    )
    ThreadingHTTPServer((args.bind, args.port), handler(lab)).serve_forever()
