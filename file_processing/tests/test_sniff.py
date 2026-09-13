"""
Confirms the sniffer correctly identifies real payload format for the two
sample files whose extensions lie (`.xls` for both CBS and NTSL), per the
approved plan's "Key findings".
"""
from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from file_processing.parsers.sniff import ContainerKind, PayloadKind, sniff_path

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / "14072026"
CBS_SAMPLE = SAMPLE_ROOT / "cbs_report" / "b5rg090oa.xls"
NTSL_SAMPLE = SAMPLE_ROOT / "NTSL" / "UPI_NTSLSSK140726_1C.xls"


class SniffRealSampleFilesTests(SimpleTestCase):
    def test_cbs_xls_is_detected_as_spreadsheetml_xml(self):
        result = sniff_path(CBS_SAMPLE)
        self.assertEqual(result.container, ContainerKind.NONE)
        self.assertEqual(result.payload, PayloadKind.SPREADSHEETML_XML)

    def test_ntsl_xls_is_detected_as_ooxml_zip(self):
        result = sniff_path(NTSL_SAMPLE)
        self.assertEqual(result.container, ContainerKind.ZIP)
        self.assertEqual(result.payload, PayloadKind.OOXML_ZIP)


class SniffBytesTests(SimpleTestCase):
    def test_csv_fallback(self):
        from file_processing.parsers.sniff import sniff_bytes

        result = sniff_bytes(b"rrn,amount,direction\n123456789012,100,CREDIT\n")
        self.assertEqual(result.container, ContainerKind.NONE)
        self.assertEqual(result.payload, PayloadKind.CSV)
