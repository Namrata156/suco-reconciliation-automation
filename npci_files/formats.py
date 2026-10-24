"""
NPCI upload file formats, registered per payment module (design section 5:
SSKUPI is UPI-only; IMPS will need its own format later).

SSKUPI (docs/npci_file_design_draft_v1.txt sections 1-2, Amendment 1),
checked against ops' three real files (SSKUPI_files/, git-ignored):
  - name SSKUPIddmmyyyy.csv; a later file for the same date
    SSKUPIddmmyyyy_2.csv, _3.csv ... (Amendment 1);
  - header bankadjref,flag,Shtdat,adjamt,shser,shcrd,filename,reason,specifyother;
  - plain text, no quotes, no BOM, every line (the last too) ends CRLF;
  - rows DRC, then RRC, then TCC, then RET; inside a flag by numeric code,
    then by the row's order in NPCI's source report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Which NPCI dispute report (presence overlay leg) a reply flag answers, and
# the report's cells the file needs (1-based raw cell keys, as in
# FileTypeConfig.column_mapping and reconciliation.presence).
LEG_BY_FLAG = {"DRC": "DRC", "TCC": "TIMEOUT", "RET": "TIMEOUT", "RRC": "ADJUSTMENT"}
# shcrd: DRC / Timeout column 11 "Beneficiary Mobile /Account/Aadhar Number";
# Adjustment column 12 "Ben_Mobile_No" (design section 2).
ACCOUNT_CELL_BY_LEG = {"DRC": "11", "TIMEOUT": "11", "ADJUSTMENT": "12"}
# RRC Shtdat: the Adjustment report's Txndate (column 8, dd-mm-yyyy).
ADJUSTMENT_TXN_DATE_CELL = "8"
ADJUSTMENT_TXN_DATE_FORMAT = "%d-%m-%Y"


class FormatError(ValueError):
    """A value cannot be written into the file as it is (comma, quote, line
    break or non-ASCII character in a column that has no quoting)."""


@dataclass(frozen=True)
class FileLine:
    rrn: str
    flag: str
    code: str
    shtdat: date
    amount: Decimal
    account: str
    text: str


def format_amount(amount: Decimal) -> str:
    """No trailing zeros, no separators: 1, 7.5, 140.56."""
    text = f"{Decimal(amount):.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _plain(value: str, what: str) -> str:
    value = str(value)
    if not value:
        raise FormatError(f"{what} is empty")
    bad = [c for c in value if c in ',"\r\n' or not (32 <= ord(c) < 127)]
    if bad:
        raise FormatError(f"{what} contains characters the file cannot hold: {''.join(sorted(set(bad)))!r}")
    return value


@dataclass(frozen=True)
class SskupiFormat:
    code: str = "SSKUPI"
    header: str = "bankadjref,flag,Shtdat,adjamt,shser,shcrd,filename,reason,specifyother"
    flag_order: tuple[str, ...] = ("DRC", "RRC", "TCC", "RET")
    line_ending: str = "\r\n"

    def file_name(self, file_date: date, sequence: int) -> str:
        base = f"{self.code}{file_date:%d%m%Y}"
        return f"{base}.csv" if sequence == 1 else f"{base}_{sequence}.csv"

    def sort_key(self, flag: str, code: str, source_order) -> tuple:
        return (self.flag_order.index(flag), int(code) if code.isdigit() else 10**9, code, source_order)

    def check_line(self, line: FileLine) -> None:
        """Raises FormatError when the line cannot be written."""
        self.render_line(line, "check.csv")

    def render_line(self, line: FileLine, file_name: str) -> str:
        return ",".join([
            _plain(line.rrn, "bankadjref (RRN)"),
            _plain(line.flag, "flag"),
            f"{line.shtdat:%d-%m-%Y}",
            format_amount(line.amount),
            _plain(line.rrn, "shser (RRN)"),
            _plain(line.account, "shcrd (account / mobile number)"),
            _plain(file_name, "filename"),
            _plain(line.code, "reason (numeric code)"),
            _plain(line.text, "specifyother (description)"),
        ])

    def render(self, lines: list[FileLine], file_name: str) -> str:
        """The whole file. `lines` must already be in file order."""
        body = [self.header] + [self.render_line(line, file_name) for line in lines]
        return "".join(text + self.line_ending for text in body)


SSKUPI = SskupiFormat()

# Payment module code -> its NPCI upload file format.
FORMATS_BY_MODULE = {"UPI": SSKUPI}


def format_for(module) -> SskupiFormat | None:
    return FORMATS_BY_MODULE.get(module.code)
