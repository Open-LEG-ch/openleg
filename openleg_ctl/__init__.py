# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read and preview commands generated from the operator contract."""

import argparse
import json
import os
import sys
from http.client import HTTPException, InvalidURL
from importlib.resources import files
from pathlib import Path

import yaml


def contract():
    source = Path(__file__).parents[1] / "docs/openapi/operator-v1.yaml"
    if not source.exists():
        source = files("openleg_ctl").joinpath("operator-v1.yaml")
    return yaml.safe_load(source.read_text())


def resolve(spec, value):
    while "$ref" in value:
        ref = value["$ref"]
        value = spec
        for part in ref.removeprefix("#/").split("/"):
            value = value[part]
    return value


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error includes raw caller input (possibly secrets).
        raise ValueError("invalid_arguments")


def parser(spec):
    root = Parser(prog="openleg-ctl", allow_abbrev=False)
    commands = root.add_subparsers(dest="command", required=True)
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            parameters = [resolve(spec, p) for p in op.get("parameters", [])]
            preview = method != "get" and any(
                p["name"] == "dry_run" and p["in"] == "query" for p in parameters
            )
            if method != "get" and not preview:
                continue
            name = op["operationId"] + ("-preview" if preview else "")
            command = commands.add_parser(
                name,
                allow_abbrev=False,
                description=op["x-openleg-capability"]
                + ("; preview only" if preview else ""),
            )
            command.add_argument("--json", action="store_true")
            command.add_argument("--url", default=os.environ.get("OPENLEG_URL"))
            body = resolve(
                spec,
                op.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {}),
            )
            fields = [p for p in parameters if p["name"] != "dry_run"]
            fields += [
                dict(
                    name=n,
                    schema=s,
                    required=n in body.get("required", []),
                    **{"in": "body"},
                )
                for n, s in body.get("properties", {}).items()
            ]
            for field in fields:
                schema = resolve(spec, field["schema"])
                kind = schema.get("type")
                convert = (
                    int
                    if kind == "integer"
                    else json.loads
                    if kind in ("object", "array", "boolean")
                    else str
                )
                # Flags are lowercase with hyphens (Idempotency-Key becomes
                # --idempotency-key); dest keeps the contract name for lookup.
                command.add_argument(
                    "--" + field["name"].lower().replace("_", "-"),
                    dest=field["name"],
                    type=convert,
                    required=field.get("required", False),
                )
            command.set_defaults(
                path=path, method=method, preview=preview, fields=fields
            )
    return root


def main(argv=None):
    try:
        try:
            args = parser(contract()).parse_args(argv)
        except ValueError:
            print("invalid_arguments", file=sys.stderr)
            return 2
        return execute(args)
    except SystemExit as exc:
        return exc.code
    except Exception:
        # Last boundary: unexpected failures must never disclose exception text.
        print('{"code": "internal_error"}', file=sys.stderr)
        return 1


def execute(args):
    import re
    from urllib import error, parse, request

    from jsonschema import Draft202012Validator

    token = os.environ.get("OPENLEG_TOKEN", "")

    def emit(value, *, failed=False):
        rendered = json.dumps(value, ensure_ascii=True, indent=None if args.json else 2)
        if token:
            rendered = rendered.replace(json.dumps(token)[1:-1], "[redacted]")
        rendered = re.sub(r"olk_[A-Za-z0-9_-]+", "[redacted]", rendered)
        print(rendered, file=sys.stderr if failed else sys.stdout)
        return 1 if failed else 0

    def fail(code, **details):
        return emit(dict(code=code, **details), failed=True)

    if not token:
        return fail("authentication_required")
    if not re.fullmatch(r"olk_[A-Za-z0-9_-]+", token):
        return fail("invalid_credential")
    try:
        try:
            origin = parse.urlsplit(args.url or "")
            _ = origin.port  # Access validates numeric syntax and the port range.
        except ValueError:
            return fail("invalid_url")
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or origin.path not in ("", "/")
        ):
            return fail("invalid_url")
        path, query, body, headers = args.path, {}, {}, {}
        spec = contract()
        for field in args.fields:
            value = getattr(args, field["name"])
            if value is None:
                continue
            if not Draft202012Validator(
                {"components": spec["components"], **field["schema"]}
            ).is_valid(value):
                return fail("invalid_arguments", field=field["name"])
            if field["in"] == "path":
                if value in (".", ".."):
                    return fail("invalid_arguments", field=field["name"])
                path = path.replace(
                    "{" + field["name"] + "}", parse.quote(str(value), safe="")
                )
            elif field["in"] == "query":
                query[field["name"]] = value
            elif field["in"] == "body":
                body[field["name"]] = value
            elif field["in"] == "header":
                headers[field["name"]] = str(value)
        if args.preview:
            query["dry_run"] = "true"
        url = (
            args.url.rstrip("/")
            + path
            + ("?" + parse.urlencode(query) if query else "")
        )
        req = request.Request(
            url,
            method=args.method.upper(),
            data=json.dumps(body).encode() if args.preview else None,
            headers={
                **headers,
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )

        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        # Credentials must not follow redirects or ambient proxy settings.
        opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(req, timeout=15)
        except error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            try:
                payload = json.load(response)
            except (ValueError, UnicodeError):
                return fail("operator_error")
            if status != 200:
                codes = resolve(
                    spec, spec["components"]["schemas"]["Error"]["properties"]["code"]
                )["enum"]
                code = payload.get("code") if isinstance(payload, dict) else None
                if code not in codes:
                    code = "operator_error"
                details = {}
                if status == 429:
                    delay = response.headers.get("Retry-After", "")
                    details["retry_after"] = delay if delay.isdigit() else "unknown"
                return fail(code, **details)
            return emit(payload)
    except InvalidURL:
        return fail("invalid_url")
    except (HTTPException, error.URLError, TimeoutError, ConnectionError, OSError):
        return fail("network_error")
    except (ValueError, TypeError):
        return fail("invalid_arguments")
