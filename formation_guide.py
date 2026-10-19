# SPDX-License-Identifier: AGPL-3.0-or-later
"""Visible formation FAQs and their structured representation."""

from i18n import gettext as _
from translation_markers import gettext_noop

LEG_FORMATION_FAQS = (
    {
        "question": gettext_noop("Brauchen die Teilnehmenden eigene Smart Meter?"),
        "answer": (
            gettext_noop(
                "Nein. Vorhandene gesetzeskonforme Smart Meter des "
                "Verteilnetzbetreibers werden weiterverwendet. Fehlende erforderliche "
                "Geräte installiert der Verteilnetzbetreiber nach einem gültigen Antrag. "
                "Regulierte Messtarife können weiterhin anfallen."
            )
        ),
    },
    {
        "question": gettext_noop(
            "Kann eine LEG mehrere Gemeinden oder VNB-Gebiete umfassen?"
        ),
        "answer": (
            gettext_noop(
                "Nein. Eine LEG bleibt innerhalb einer politischen Gemeinde und im "
                "Netzgebiet eines Verteilnetzbetreibers. Für mehrere Gemeinden oder "
                "VNB-Gebiete braucht es separate lokale LEGs."
            )
        ),
    },
    {
        "question": gettext_noop("Wer ist für die Abrechnung zuständig?"),
        "answer": (
            gettext_noop(
                "Der Verteilnetzbetreiber rechnet Netznutzung und Messung ab, bei "
                "Teilnehmenden in der Grundversorgung auch den übrigen Strombezug. Die "
                "LEG oder ihr Dienstleister rechnet den innerhalb der Gemeinschaft "
                "ausgetauschten Strom ab."
            )
        ),
    },
    {
        "question": gettext_noop(
            "Darf ich OpenLEG installieren, anpassen oder forken?"
        ),
        "answer": (
            gettext_noop(
                "Ja. Unter der Lizenz AGPL-3.0-or-later dürfen Sie OpenLEG "
                "installieren, nutzen und anpassen. Auch ein Fork ist erlaubt. "
                "Wenn Sie eine veränderte Version über ein Netzwerk anbieten, müssen "
                "Sie den Nutzenden den entsprechenden Quellcode zugänglich machen. "
                "Das ist eine praktische Zusammenfassung und keine Rechtsberatung."
            )
        ),
        "links": (
            {
                "label": gettext_noop("Lizenz lesen"),
                "href": "https://github.com/Open-LEG-ch/openleg/blob/main/LICENSE",
            },
            {
                "label": gettext_noop("Repository öffnen"),
                "href": "https://github.com/Open-LEG-ch/openleg",
            },
            {
                "label": gettext_noop("Anleitung zum eigenen Betrieb"),
                "href": "/self-host",
            },
        ),
    },
    {
        "question": gettext_noop(
            "Ist OpenLEG an einen bestimmten Netzbetreiber gebunden?"
        ),
        "answer": (
            gettext_noop(
                "OpenLEG ist ohne Bindung an einen bestimmten VNB konzipiert. "
                "Noch ist nicht jede VNB-Anbindung fertig umgesetzt. Anmeldung und "
                "Datenlieferung müssen pro VNB konfiguriert werden."
            )
        ),
    },
    {
        "question": gettext_noop("Kann OpenLEG unsere Gruppe persönlich unterstützen?"),
        "answer": (
            gettext_noop(
                "Vorträge, Workshops und Projektunterstützung bieten wir nach "
                "vorgängiger Vereinbarung gegen Honorar an."
            )
        ),
        "contact_label": gettext_noop("Unterstützung anfragen"),
    },
)


def build_guide_context():
    translate = _
    faqs = tuple(
        {
            key: (
                _(value)
                if isinstance(value, str)
                else tuple(
                    {**link, "label": translate(link["label"])} for link in value
                )
            )
            for key, value in faq.items()
        }
        for faq in LEG_FORMATION_FAQS
    )
    faq_page_jsonld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {"@type": "Answer", "text": faq["answer"]},
            }
            for faq in faqs
        ],
    }
    return {
        "formation_faqs": faqs,
        "faq_page_jsonld": faq_page_jsonld,
    }
