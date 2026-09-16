# SPDX-License-Identifier: AGPL-3.0-or-later
"""Visible formation FAQs and their structured representation."""

LEG_FORMATION_FAQS = (
    {
        "question": "Brauchen die Teilnehmenden eigene Smart Meter?",
        "answer": (
            "Nein. Vorhandene gesetzeskonforme Smart Meter des "
            "Verteilnetzbetreibers werden weiterverwendet. Fehlende erforderliche "
            "Geräte installiert der Verteilnetzbetreiber nach einem gültigen Antrag. "
            "Regulierte Messtarife können weiterhin anfallen."
        ),
    },
    {
        "question": "Kann eine LEG mehrere Gemeinden oder VNB-Gebiete umfassen?",
        "answer": (
            "Nein. Eine LEG bleibt innerhalb einer politischen Gemeinde und im "
            "Netzgebiet eines Verteilnetzbetreibers. Für mehrere Gemeinden oder "
            "VNB-Gebiete braucht es separate lokale LEGs."
        ),
    },
    {
        "question": "Wer ist für die Abrechnung zuständig?",
        "answer": (
            "Der Verteilnetzbetreiber rechnet Netznutzung und Messung ab, bei "
            "Teilnehmenden in der Grundversorgung auch den übrigen Strombezug. Die "
            "LEG oder ihr Dienstleister rechnet den innerhalb der Gemeinschaft "
            "ausgetauschten Strom ab."
        ),
    },
    {
        "question": "Darf ich OpenLEG installieren, anpassen oder forken?",
        "answer": (
            "Ja. Unter der Lizenz AGPL-3.0-or-later dürfen Sie OpenLEG "
            "installieren, nutzen und anpassen. Auch ein Fork ist erlaubt. "
            "Wenn Sie eine veränderte Version über ein Netzwerk anbieten, müssen "
            "Sie den Nutzenden den entsprechenden Quellcode zugänglich machen. "
            "Das ist eine praktische Zusammenfassung und keine Rechtsberatung."
        ),
        "links": (
            {
                "label": "Lizenz lesen",
                "href": "https://github.com/Open-LEG-ch/openleg/blob/main/LICENSE",
            },
            {
                "label": "Repository öffnen",
                "href": "https://github.com/Open-LEG-ch/openleg",
            },
            {"label": "Anleitung zum eigenen Betrieb", "href": "/self-host"},
        ),
    },
    {
        "question": "Ist OpenLEG an einen bestimmten Netzbetreiber gebunden?",
        "answer": (
            "OpenLEG ist ohne Bindung an einen bestimmten VNB konzipiert. "
            "Noch ist nicht jede VNB-Anbindung fertig umgesetzt. Anmeldung und "
            "Datenlieferung müssen pro VNB konfiguriert werden."
        ),
    },
    {
        "question": "Kann OpenLEG unsere Gruppe persönlich unterstützen?",
        "answer": (
            "Vorträge, Workshops und Projektunterstützung bieten wir nach "
            "vorgängiger Vereinbarung gegen Honorar an."
        ),
        "contact_label": "Unterstützung anfragen",
    },
)


def build_guide_context():
    faq_page_jsonld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {"@type": "Answer", "text": faq["answer"]},
            }
            for faq in LEG_FORMATION_FAQS
        ],
    }
    return {
        "formation_faqs": LEG_FORMATION_FAQS,
        "faq_page_jsonld": faq_page_jsonld,
    }
