"""TDD tests for document_generator.py - PDF generation for LEG formation documents."""
import pytest
from unittest.mock import patch, MagicMock

# Mock _render_pdf to return fake PDF bytes (avoids WeasyPrint system dep in CI)
FAKE_PDF = b'%PDF-1.4 fake'


@pytest.fixture(autouse=True)
def mock_render_pdf():
    with patch('document_generator._render_pdf', return_value=FAKE_PDF) as m:
        yield m


class TestGemeinschaftsvereinbarung:
    """Tests for LEG community agreement document."""

    def test_generates_pdf_bytes(self):
        from document_generator import generate_gemeinschaftsvereinbarung
        result = generate_gemeinschaftsvereinbarung(
            community_name="LEG Musterstadt",
            participants=[
                {"name": "Max Muster", "address": "Musterstrasse 1, 8000 Zürich", "role": "producer"},
                {"name": "Anna Beispiel", "address": "Musterstrasse 3, 8000 Zürich", "role": "consumer"},
                {"name": "Peter Test", "address": "Musterstrasse 5, 8000 Zürich", "role": "consumer"},
            ],
            municipality="Zürich",
            distribution_model="proportional",
            date_str="2026-03-01",
        )
        assert isinstance(result, bytes)
        assert result[:5] == b'%PDF-'

    def test_minimum_participants(self):
        from document_generator import generate_gemeinschaftsvereinbarung
        with pytest.raises(ValueError, match="mindestens 2"):
            generate_gemeinschaftsvereinbarung(
                community_name="LEG Test",
                participants=[{"name": "Solo", "address": "Addr", "role": "producer"}],
                municipality="Bern",
                distribution_model="proportional",
            )

    def test_requires_producer(self):
        from document_generator import generate_gemeinschaftsvereinbarung
        with pytest.raises(ValueError, match="Produzent"):
            generate_gemeinschaftsvereinbarung(
                community_name="LEG Test",
                participants=[
                    {"name": "A", "address": "Addr1", "role": "consumer"},
                    {"name": "B", "address": "Addr2", "role": "consumer"},
                ],
                municipality="Bern",
                distribution_model="proportional",
            )

    def test_all_distribution_models(self):
        from document_generator import generate_gemeinschaftsvereinbarung
        participants = [
            {"name": "A", "address": "Addr1", "role": "producer"},
            {"name": "B", "address": "Addr2", "role": "consumer"},
        ]
        for model in ["einfach", "proportional", "individuell"]:
            result = generate_gemeinschaftsvereinbarung(
                community_name="LEG Test",
                participants=participants,
                municipality="Bern",
                distribution_model=model,
            )
            assert result[:5] == b'%PDF-'

    def test_html_contains_participants(self, mock_render_pdf):
        from document_generator import generate_gemeinschaftsvereinbarung
        generate_gemeinschaftsvereinbarung(
            community_name="LEG Test",
            participants=[
                {"name": "Max Muster", "address": "Addr1", "role": "producer"},
                {"name": "Anna B", "address": "Addr2", "role": "consumer"},
            ],
            municipality="Zürich",
            distribution_model="proportional",
        )
        html_arg = mock_render_pdf.call_args[0][0]
        assert "Max Muster" in html_arg
        assert "Anna B" in html_arg
        assert "Produzent" in html_arg
        assert "Proportionale Verteilung" in html_arg


class TestTeilnehmervertrag:
    """Tests for individual participant contract."""

    def test_generates_pdf_bytes(self):
        from document_generator import generate_teilnehmervertrag
        result = generate_teilnehmervertrag(
            participant_name="Max Muster",
            participant_address="Musterstrasse 1, 8000 Zürich",
            community_name="LEG Musterstadt",
            role="producer",
            pv_kwp=12.5,
            annual_consumption_kwh=4500,
        )
        assert isinstance(result, bytes)
        assert result[:5] == b'%PDF-'

    def test_consumer_no_pv(self):
        from document_generator import generate_teilnehmervertrag
        result = generate_teilnehmervertrag(
            participant_name="Anna Beispiel",
            participant_address="Addr",
            community_name="LEG Test",
            role="consumer",
            pv_kwp=0,
            annual_consumption_kwh=3200,
        )
        assert result[:5] == b'%PDF-'

    def test_html_contains_pv_for_producer(self, mock_render_pdf):
        from document_generator import generate_teilnehmervertrag
        generate_teilnehmervertrag(
            participant_name="Max",
            participant_address="Addr",
            community_name="LEG",
            role="producer",
            pv_kwp=12.5,
            annual_consumption_kwh=4500,
        )
        html_arg = mock_render_pdf.call_args[0][0]
        assert "12.5 kWp" in html_arg


class TestDsoAnmeldung:
    """Tests for DSO registration form."""

    def test_generates_pdf_bytes(self):
        from document_generator import generate_dso_anmeldung
        result = generate_dso_anmeldung(
            community_name="LEG Musterstadt",
            dso_name="EKZ",
            participants=[
                {"name": "Max Muster", "address": "Addr1", "metering_point": "CH1234567890"},
                {"name": "Anna Beispiel", "address": "Addr2", "metering_point": "CH0987654321"},
            ],
            total_pv_kwp=25.0,
            network_level="NE7",
        )
        assert isinstance(result, bytes)
        assert result[:5] == b'%PDF-'

    def test_requires_metering_points(self):
        from document_generator import generate_dso_anmeldung
        with pytest.raises(ValueError, match="Messpunkt"):
            generate_dso_anmeldung(
                community_name="LEG Test",
                dso_name="EKZ",
                participants=[
                    {"name": "A", "address": "Addr1"},
                ],
                total_pv_kwp=10.0,
                network_level="NE7",
            )


class TestDocumentStore:
    """Tests for storing/listing generated documents."""

    @patch('document_generator.db_store_document')
    def test_store_and_list(self, mock_store):
        from document_generator import store_document
        mock_store.return_value = 42
        doc_id = store_document(
            community_id=1,
            doc_type="gemeinschaftsvereinbarung",
            pdf_bytes=b'%PDF-fake',
            filename="gv_leg_test.pdf",
        )
        mock_store.assert_called_once()
        assert doc_id == 42
