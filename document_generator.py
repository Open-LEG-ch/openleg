"""PDF document generator for LEG formation documents using WeasyPrint."""
from datetime import date


DISTRIBUTION_LABELS = {
    "einfach": "Gleichmässige Verteilung",
    "proportional": "Proportionale Verteilung nach Verbrauchsanteil",
    "individuell": "Individuelle Vereinbarung gemäss Anhang",
}


def _render_pdf(html_str):
    """Render HTML string to PDF bytes."""
    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()


def generate_gemeinschaftsvereinbarung(
    community_name,
    participants,
    municipality,
    distribution_model,
    date_str=None,
):
    """Generate LEG community agreement (Gemeinschaftsvereinbarung) as PDF.

    Args:
        community_name: Name of the LEG community
        participants: List of dicts with keys: name, address, role (producer/consumer)
        municipality: Municipality name
        distribution_model: einfach, proportional, or individuell
        date_str: Optional date string (YYYY-MM-DD), defaults to today

    Returns:
        PDF bytes

    Raises:
        ValueError: If fewer than 2 participants or no producer
    """
    if len(participants) < 2:
        raise ValueError("Eine LEG benötigt mindestens 2 Teilnehmer")

    has_producer = any(p.get("role") == "producer" for p in participants)
    if not has_producer:
        raise ValueError("Eine LEG benötigt mindestens einen Produzent")

    if date_str is None:
        date_str = date.today().isoformat()

    dist_label = DISTRIBUTION_LABELS.get(distribution_model, distribution_model)

    rows = ""
    for i, p in enumerate(participants, 1):
        role_label = "Produzent" if p["role"] == "producer" else "Konsument"
        rows += f"<tr><td>{i}</td><td>{p['name']}</td><td>{p['address']}</td><td>{role_label}</td></tr>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 2cm; }}
h1 {{ font-size: 16pt; }}
h2 {{ font-size: 13pt; margin-top: 1.5em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
td, th {{ border: 1px solid #333; padding: 6px 8px; text-align: left; font-size: 10pt; }}
th {{ background: #f0f0f0; }}
.footer {{ margin-top: 3em; font-size: 9pt; color: #666; }}
</style></head><body>
<h1>Gemeinschaftsvereinbarung</h1>
<h2>Lokale Elektrizitätsgemeinschaft: {community_name}</h2>
<p>Gemeinde: {municipality}<br>Datum: {date_str}</p>

<h2>1. Teilnehmer</h2>
<table><tr><th>#</th><th>Name</th><th>Adresse</th><th>Rolle</th></tr>{rows}</table>

<h2>2. Verteilmodell</h2>
<p>{dist_label}</p>
<p>Die intern produzierte Energie wird gemäss dem gewählten Modell auf alle Teilnehmer verteilt.
Die Abrechnung erfolgt auf Basis von 15-Minuten-Messintervallen.</p>

<h2>3. Rechtsgrundlage</h2>
<p>Diese Vereinbarung stützt sich auf Art. 17d und 17e des Stromversorgungsgesetzes (StromVG)
sowie Art. 19e bis 19h der Stromversorgungsverordnung (StromVV).</p>

<h2>4. Interne Preisgestaltung</h2>
<p>Der interne Strompreis wird durch die Gemeinschaft festgelegt und darf den lokalen
Standardtarif des Verteilnetzbetreibers nicht übersteigen.</p>

<h2>5. Ein- und Austritt</h2>
<p>Neue Teilnehmer können mit Zustimmung der bestehenden Mitglieder beitreten.
Der Austritt ist mit einer Frist von 3 Monaten auf Quartalsende möglich.</p>

<h2>6. Vertretung</h2>
<p>Die Gemeinschaft wird gegenüber dem Verteilnetzbetreiber durch den Vertreter vertreten,
der gemäss separater Vollmacht bestimmt wird.</p>

<h2>7. Unterschriften</h2>
<table><tr><th>Name</th><th>Datum</th><th>Unterschrift</th></tr>
{"".join(f'<tr><td>{p["name"]}</td><td></td><td></td></tr>' for p in participants)}
</table>

<div class="footer">Generiert durch OpenLEG Platform, openleg.ch</div>
</body></html>"""

    return _render_pdf(html)


def generate_teilnehmervertrag(
    participant_name,
    participant_address,
    community_name,
    role,
    pv_kwp=0,
    annual_consumption_kwh=0,
    date_str=None,
):
    """Generate individual participant contract as PDF.

    Returns:
        PDF bytes
    """
    if date_str is None:
        date_str = date.today().isoformat()

    role_label = "Produzent" if role == "producer" else "Konsument"

    pv_section = ""
    if pv_kwp and pv_kwp > 0:
        pv_section = f"<p><strong>PV-Anlage:</strong> {pv_kwp} kWp</p>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 2cm; }}
h1 {{ font-size: 16pt; }}
h2 {{ font-size: 13pt; margin-top: 1.5em; }}
.footer {{ margin-top: 3em; font-size: 9pt; color: #666; }}
</style></head><body>
<h1>Teilnehmervertrag</h1>
<h2>LEG: {community_name}</h2>
<p>Datum: {date_str}</p>

<h2>Teilnehmer</h2>
<p><strong>Name:</strong> {participant_name}<br>
<strong>Adresse:</strong> {participant_address}<br>
<strong>Rolle:</strong> {role_label}<br>
<strong>Jahresverbrauch:</strong> {annual_consumption_kwh:,.0f} kWh</p>
{pv_section}

<h2>Pflichten</h2>
<ul>
<li>Installation eines kommunikativen Smart Meters (Frist: 3 Monate nach Beitritt)</li>
<li>Bereitstellung der 15-Minuten-Messdaten an die Gemeinschaft</li>
<li>Einhaltung der Gemeinschaftsvereinbarung</li>
</ul>

<h2>Kündigung</h2>
<p>Austritt mit 3 Monaten Frist auf Quartalsende. Alle offenen Abrechnungen
werden vor Austritt abgeschlossen.</p>

<h2>Unterschrift</h2>
<table style="border:none; width:100%">
<tr><td style="border:none; width:50%">
<p>______________________<br>{participant_name}<br>Datum: </p>
</td><td style="border:none; width:50%">
<p>______________________<br>Vertreter {community_name}<br>Datum: </p>
</td></tr></table>

<div class="footer">Generiert durch OpenLEG Platform, openleg.ch</div>
</body></html>"""

    return _render_pdf(html)


def generate_dso_anmeldung(
    community_name,
    dso_name,
    participants,
    total_pv_kwp,
    network_level,
    date_str=None,
):
    """Generate DSO registration form as PDF.

    Args:
        participants: List of dicts with keys: name, address, metering_point

    Raises:
        ValueError: If any participant missing metering_point
    """
    for p in participants:
        if not p.get("metering_point"):
            raise ValueError(f"Messpunkt fehlt für Teilnehmer {p.get('name', '?')}")

    if date_str is None:
        date_str = date.today().isoformat()

    rows = ""
    for i, p in enumerate(participants, 1):
        rows += f"<tr><td>{i}</td><td>{p['name']}</td><td>{p['address']}</td><td>{p['metering_point']}</td></tr>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 2cm; }}
h1 {{ font-size: 16pt; }}
h2 {{ font-size: 13pt; margin-top: 1.5em; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
td, th {{ border: 1px solid #333; padding: 6px 8px; text-align: left; font-size: 10pt; }}
th {{ background: #f0f0f0; }}
.footer {{ margin-top: 3em; font-size: 9pt; color: #666; }}
</style></head><body>
<h1>Anmeldung Lokale Elektrizitätsgemeinschaft</h1>
<p><strong>An:</strong> {dso_name}<br>
<strong>Datum:</strong> {date_str}</p>

<h2>Gemeinschaft</h2>
<p><strong>Name:</strong> {community_name}<br>
<strong>Netzebene:</strong> {network_level}<br>
<strong>Gesamte PV-Leistung:</strong> {total_pv_kwp} kWp</p>

<h2>Teilnehmer und Messpunkte</h2>
<table><tr><th>#</th><th>Name</th><th>Adresse</th><th>Messpunkt-ID</th></tr>{rows}</table>

<h2>Beilagen</h2>
<ul>
<li>Gemeinschaftsvereinbarung (unterzeichnet)</li>
<li>Teilnehmerverträge (unterzeichnet)</li>
<li>Nachweis PV-Anlage(n)</li>
</ul>

<h2>Kontakt</h2>
<p>Vertreter der Gemeinschaft:<br>
[Name, Adresse, Telefon, E-Mail]</p>

<div class="footer">Generiert durch OpenLEG Platform, openleg.ch</div>
</body></html>"""

    return _render_pdf(html)


def store_document(community_id, doc_type, pdf_bytes, filename):
    """Store generated document in database.

    Returns:
        Document ID
    """
    return db_store_document(community_id, doc_type, pdf_bytes, filename)


def db_store_document(community_id, doc_type, pdf_bytes, filename):
    """Store document in database. Placeholder for database integration."""
    import database
    return database.store_leg_document(community_id, doc_type, pdf_bytes, filename)


def list_documents(community_id):
    """List all documents for a community."""
    import database
    return database.list_leg_documents(community_id)
