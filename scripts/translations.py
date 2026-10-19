#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extract, validate and compile the standard gettext catalogs."""

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from babel.messages.mofile import read_mo, write_mo
from babel.messages.pofile import read_po, write_po
from babel.support import Translations
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = ROOT / "translations"
NAMED_FORMAT = re.compile(r"%\([^)]+\)[#0 +.\-0-9]*[diouxXeEfFgGcrsa]")


def extract(destination):
    """Extract repository messages and normalize the generated POT deterministically."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from babel.messages.frontend import main; main()",
            "extract",
            "-F",
            "babel.cfg",
            "-k",
            "gettext_noop",
            "-k",
            "_l",
            "-k",
            "_N",
            "-o",
            str(destination),
            ".",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    with destination.open("rb") as handle:
        catalog = read_po(handle)
    normalize_flags(catalog)
    with destination.open("wb") as handle:
        write_po(handle, catalog, width=100, sort_output=True)
    destination.write_bytes(destination.read_bytes().rstrip() + b"\n")
    return catalog


def normalize_flags(catalog):
    """Keep named-format validation without misclassifying literal percent prose."""
    for message in catalog:
        identifiers = message.id if isinstance(message.id, tuple) else (message.id,)
        # Babel mistakes literal prose such as '40% ohne' for '% o'. Our
        # interpolation convention uses named placeholders exclusively.
        if not any(NAMED_FORMAT.search(text or "") for text in identifiers):
            message.flags.discard("python-format")


def validate(catalog, expected):
    """Require complete translations and matching placeholders for each source form.

    As in Babel, translation form zero corresponds to the singular msgid and
    every later form to the plural msgid, including languages with more than
    two forms. Keep the stricter rule that required named parameters cannot
    disappear. NAMED_FORMAT is a regex over repository text, not an XPath query.
    """
    actual = {(message.id, message.context) for message in catalog if message.id}
    missing = {
        (message.id, message.context) for message in expected if message.id
    } - actual
    problems = [
        f"Missing source message: {message}" for message in sorted(missing, key=str)
    ]
    for message in catalog:
        if not message.id:
            continue
        values = (
            message.string if isinstance(message.string, tuple) else (message.string,)
        )
        if "fuzzy" in message.flags or not all(values):
            problems.append(f"Untranslated or fuzzy: {message.id}")
        identifiers = message.id if isinstance(message.id, tuple) else (message.id,)
        for index, value in enumerate(values):
            source = identifiers[min(index, len(identifiers) - 1)]
            source_parameters = set(NAMED_FORMAT.findall(source))
            if set(NAMED_FORMAT.findall(value or "")) != source_parameters:
                problems.append(f"Named placeholder mismatch: {message.id}: {value}")
    problems.extend(f"{message.id}: {errors}" for message, errors in catalog.check())
    if problems:
        raise ValueError("\n".join(problems))


def build_svg_assets(*, check):
    """Build code-native graphics from the same catalogs as the interface."""
    directory = ROOT / "templates/assets"
    sources = sorted(directory.rglob("*.svg"))
    if not sources:
        return
    environment = Environment(
        loader=FileSystemLoader(directory, encoding="utf-8"),
        autoescape=True,
        extensions=["jinja2.ext.i18n"],
        keep_trailing_newline=True,
    )
    for path in sorted(TRANSLATIONS.glob("*/LC_MESSAGES/messages.mo")):
        locale = path.parent.parent.name
        environment.install_gettext_translations(
            Translations.load(TRANSLATIONS, locales=[locale]),
            newstyle=False,
        )
        for source in sources:
            relative = source.relative_to(directory)
            rendered = environment.get_template(relative.as_posix()).render()
            ET.fromstring(rendered)
            destination = ROOT / "static/images" / relative
            if locale != "de_CH":
                destination = (
                    destination.parent / locale.replace("_", "-") / destination.name
                )
            if check:
                if (
                    not destination.exists()
                    or destination.read_text(encoding="utf-8") != rendered
                ):
                    raise ValueError(f"Localized asset is stale: {destination}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(rendered, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "check", "compile"))
    args = parser.parse_args()
    if args.command == "extract":
        catalog = extract(TRANSLATIONS / "messages.pot")
        print(f"Extracted {len(catalog)} source messages")
        return
    with TemporaryDirectory(prefix="openleg-i18n-") as directory:
        expected = extract(Path(directory) / "messages.pot")
    catalogs = sorted(TRANSLATIONS.glob("*/LC_MESSAGES/messages.po"))
    if not catalogs:
        raise ValueError("No translation catalogs found")
    for path in catalogs:
        with path.open("rb") as handle:
            catalog = read_po(handle, locale=path.parent.parent.name)
        normalize_flags(catalog)
        validate(catalog, expected)
        target = path.with_suffix(".mo")
        if args.command == "compile":
            with target.open("wb") as handle:
                write_mo(handle, catalog)
            path.write_bytes(path.read_bytes().rstrip() + b"\n")
        else:
            with target.open("rb") as handle:
                compiled = read_mo(handle)
            if (compiled.num_plurals, compiled.plural_expr) != (
                catalog.num_plurals,
                catalog.plural_expr,
            ):
                raise ValueError(f"Compiled plural rules are stale: {path}")
            # Babel currently reads MO contexts as bytes, while PO contexts
            # are text. Normalize both representations before comparing them.
            built_messages = {
                (
                    entry.id[0] if isinstance(entry.id, (tuple, list)) else entry.id,
                    entry.context.decode("utf-8")
                    if isinstance(entry.context, bytes)
                    else entry.context,
                ): entry
                for entry in compiled
                if entry.id
            }
            for message in catalog:
                if not message.id:
                    continue
                key = message.id[0] if isinstance(message.id, tuple) else message.id
                built = built_messages.get((key, message.context))
                expected_value = message.string
                built_value = built.string if built is not None else None
                if isinstance(expected_value, tuple) and isinstance(built_value, list):
                    built_value = tuple(built_value)
                if built_value != expected_value:
                    raise ValueError(f"Compiled catalog is stale: {path}, {message.id}")
        print(f"{path.relative_to(ROOT)}: {len(catalog)} translated messages, OK")
    build_svg_assets(check=args.command == "check")


if __name__ == "__main__":
    main()
