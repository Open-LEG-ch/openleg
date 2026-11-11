# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validated ordinary-IBAN donation instructions and Swiss QR payment symbols."""

import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import qrcode
from qrcode.util import MODE_8BIT_BYTE, QRData

from i18n import gettext as _

PUBLIC_FIELDS = {
    "name": 70,
    "street": 70,
    "building_number": 16,
    "postal_code": 16,
    "city": 35,
    "country": 2,
    "message": 140,
}
PRIVATE_FIELDS = ("account_evidence", "address_evidence", "qr_evidence")


class PaymentInputError(ValueError):
    """A field cannot form independently verifiable donation instructions."""

    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


def _text(value, field, limit, *, required=True, qr=True):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        raise PaymentInputError(
            _(
                "Prüfen Sie Länge und Zeichen dieser Angabe. Zeilenumbrüche sind nicht erlaubt."
            ),
            field,
        )
    value = value.strip()
    if required and not value:
        raise PaymentInputError(_("Diese Angabe ist erforderlich."), field)
    if qr and any(
        not (32 <= ord(char) <= 126 or 160 <= ord(char) <= 383 or char in "ȘșȚț€")
        for char in value
    ):
        raise PaymentInputError(
            _("Diese Zeichen werden vom Swiss QR-Standard nicht unterstützt."), field
        )
    return value


def evidence_reference(value, field, *, required=True):
    return _text(value, field, 500, qr=False, required=required)


def validated(data):
    public = {
        name: _text(
            data.get(name, ""),
            name,
            limit,
            required=name not in ("street", "building_number", "message"),
        )
        for name, limit in PUBLIC_FIELDS.items()
    }
    iban = (
        _text(data.get("iban", ""), "iban", 40)
        .replace(" ", "")
        .replace("\u00a0", "")
        .upper()
    )
    numeric = "".join(
        char if char.isdigit() else str(ord(char) - 55) for char in iban[4:] + iban[:4]
    )
    if (
        not re.fullmatch(r"(?:CH|LI)[0-9]{7}[A-Z0-9]{12}", iban)
        or int(numeric) % 97 != 1
    ):
        raise PaymentInputError(
            _("Geben Sie eine gültige gewöhnliche CH- oder LI-IBAN an."), "iban"
        )
    if 30000 <= int(iban[4:9]) <= 31999:
        raise PaymentInputError(
            _(
                "Diese Spendenroute verwendet eine gewöhnliche IBAN ohne strukturierte Referenz. Eine QR-IBAN ist dafür nicht geeignet."
            ),
            "iban",
        )
    public["iban"] = iban
    if public["country"] not in ("CH", "LI"):
        raise PaymentInputError(
            _(
                "Für diese Route ist eine geprüfte Empfängeradresse in CH oder LI erforderlich."
            ),
            "country",
        )
    if public["building_number"] and not public["street"]:
        raise PaymentInputError(
            _("Ergänzen Sie die zur Hausnummer gehörende Strasse."), "street"
        )
    private = {
        name: evidence_reference(
            data.get(name, ""), name, required=name != "qr_evidence"
        )
        for name in PRIVATE_FIELDS
    }
    # Refuse unrenderable data before it becomes an immutable review candidate.
    svg(public, "999999999.99")
    return private, public


def amount(value):
    if value == "":
        return ""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", value)
        or Decimal(value) < Decimal("0.01")
    ):
        raise PaymentInputError(
            _(
                "Geben Sie einen Betrag von CHF 0.01 bis CHF 999 999 999.99 mit höchstens zwei Nachkommastellen ein oder lassen Sie das Feld leer."
            ),
            "amount",
        )
    return format(Decimal(value), ".2f")


def payload(profile, selected_amount=""):
    selected_amount = amount(selected_amount)
    lines = [
        "SPC",
        "0200",
        "1",
        profile["iban"],
        "S",
        profile["name"],
        profile["street"],
        profile["building_number"],
        profile["postal_code"],
        profile["city"],
        profile["country"],
    ]
    lines += [""] * 7  # Ultimate creditor: reserved, must remain empty.
    lines += [selected_amount, "CHF"]
    lines += [""] * 7  # Unspecified payer; no donor identity is collected here.
    lines += ["NON", "", profile["message"], "EPD"]
    result = "\n".join(lines)
    if len(result.encode("utf-8")) > 997:
        raise PaymentInputError(
            _(
                "Die Zahlungsangaben sind für den Swiss QR-Code zu lang. Lassen Sie eine kürzere Fassung von der Bank prüfen."
            ),
            "message",
        )
    return result


def svg(profile, selected_amount=""):
    value = payload(profile, selected_amount)
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    code.add_data(QRData(value.encode("utf-8"), mode=MODE_8BIT_BYTE), optimize=0)
    code.make(fit=True)
    matrix = code.get_matrix()
    side = len(matrix)
    if code.version > 25 or 46 / side < 0.4:
        raise PaymentInputError(
            _(
                "Die Zahlungsangaben sind für den Swiss QR-Code zu lang. Lassen Sie eine kürzere Fassung von der Bank prüfen."
            ),
            "message",
        )
    scale = 46 / side
    commands = []
    for y, row in enumerate(matrix):
        for x, black in enumerate(row):
            if black:
                commands.append(
                    f"M{5 + x * scale:.6f},{5 + y * scale:.6f}h{scale:.6f}v{scale:.6f}h-{scale:.6f}z"
                )
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    root = ET.Element(
        "{" + namespace + "}svg",
        {"viewBox": "0 0 56 56", "width": "56mm", "height": "56mm", "role": "img"},
    )
    ET.SubElement(root, "{" + namespace + "}title").text = "Swiss QR Code"
    ET.SubElement(root, "{" + namespace + "}metadata").text = value
    ET.SubElement(
        root,
        "{" + namespace + "}rect",
        {"width": "56", "height": "56", "fill": "white"},
    )
    ET.SubElement(
        root, "{" + namespace + "}path", {"d": "".join(commands), "fill": "black"}
    )
    mark = ET.fromstring(
        (Path(__file__).parent / "static/images/payments/swiss-cross.svg").read_bytes()
    )
    for child in list(mark):
        if child.tag.rsplit("}", 1)[-1] == "style":
            mark.remove(child)
    for child in mark.iter():
        style_class = child.attrib.pop("class", None)
        child.attrib.pop("style", None)
        if style_class == "st0":
            child.set("fill", "#FFFFFF")
        elif style_class == "st1":
            child.attrib.update(
                fill="none",
                stroke="#FFFFFF",
                **{"stroke-width": "1.4357", "stroke-miterlimit": "10"},
            )
    mark.attrib.update(x="24.5", y="24.5", width="7", height="7")
    root.append(mark)
    return ET.tostring(root, encoding="unicode")


def grouped_iban(value):
    return " ".join(value[index : index + 4] for index in range(0, len(value), 4))
