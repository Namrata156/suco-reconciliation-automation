"""
Karnataka (RBI Bengaluru regional office) bank holidays for 2026, as plain
data -- transcribed from docs/bank_holidays_bengaluru_2026.txt (RBI Holiday
Matrix, "Holiday under Negotiable Instruments Act", retrieved 2026-10-10 by
the PM; region chosen by the project owner 2026-10-10).

FROZEN: this is the published 2026 list. A correction or an ops-confirmed
addition is a new module / migration, not an edit here. Holds plain data
only (no app imports), so both data migration core/0005 and
`manage.py seed_bank_holidays` read it. core/tests/test_working_days.py
re-checks it against the doc.

Status: CONFIRMED BY OPS (project owner decision 2026-10-10), applied by data
migration core/0006, which sets confirmed_by_ops=True and appends a
confirmation note on the existing rows. This module still carries no
confirmation flag: a row created from it later (e.g. by `seed_bank_holidays`
after a delete) gets the model default confirmed_by_ops=False.
"""
from __future__ import annotations

from datetime import date

REGION = "Karnataka"
YEAR = 2026
SOURCE_URL = "https://www.rbi.org.in/Scripts/HolidayMatrixDisplay.aspx"
RETRIEVED_ON = date(2026, 10, 10)
NOTES = (
    "RBI Holiday Matrix, regional office Bengaluru, 2026, 'Holiday under Negotiable Instruments Act' "
    "(docs/bank_holidays_bengaluru_2026.txt). Name shortened to the Karnataka festival."
)

HOLIDAYS = [
    (date(2026, 1, 15), "Uttarayana Punyakala / Makara Sankranti"),
    (date(2026, 1, 26), "Republic Day"),
    (date(2026, 3, 19), "Ugadi Festival"),
    (date(2026, 3, 21), "Ramzan-Id (Id-Ul-Fitr)"),
    (date(2026, 3, 30), "Mahavir Jayanti"),
    (date(2026, 4, 1), "Banks' closing of yearly accounts"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 14), "Dr. Babasaheb Ambedkar Jayanti"),
    (date(2026, 4, 20), "Basava Jayanti / Akshaya Tritiya"),
    (date(2026, 5, 1), "May Day (Labour Day)"),
    (date(2026, 5, 28), "Bakri ID (Id-Uz-Zuha)"),
    (date(2026, 6, 26), "Muharram"),
    (date(2026, 8, 15), "Independence Day"),
    (date(2026, 8, 26), "Id-E-Milad (Milad-un-Nabi)"),
    (date(2026, 9, 14), "Ganesh Chaturthi / Varasiddhi Vinayaka Vrata"),
    (date(2026, 10, 2), "Mahatma Gandhi Jayanti"),
    (date(2026, 10, 10), "Mahalaya Amavasye (also 2nd Saturday)"),
    (date(2026, 10, 20), "Mahanavami / Ayudhapooja"),
    (date(2026, 10, 21), "Vijaya Dasami"),
    (date(2026, 11, 10), "Deepavali (Bali Pratipada)"),
    (date(2026, 11, 27), "Kanakadasa Jayanthi"),
    (date(2026, 12, 25), "Christmas"),
]
