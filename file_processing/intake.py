"""
File intake for the web upload (Phase 5 design 4.8-4.10, owner P5 c,
Amendment 1, P10b). The services the API (and, for the CBS date, the Django
admin) call; they check the role themselves and write core.OperationLog.

  detect_files()          dry run, nothing saved: proposes file type, cycle
                          and business date per file, from the file name
                          (FileTypeConfig.outer_filename_pattern /
                          outer_date_format) or, for CBS, from the content
                          (SpreadsheetML sniff + worksheet names)
  upload_files()          stores each file, hashes it, refuses duplicates
                          and mismatches, creates the UploadedFile and
                          queues the EXISTING processing chain
                          (file_processing.tasks.build_processing_chain)
  select_business_date()  the CBS date pick, moved out of the Django-admin
                          action (design 3.3), now audited
  date_checklist()        expected type x cycle files for a date vs the files
                          received (design 4.11)

Upload, date selection (and the run, reconciliation.runs) are for the
Operations User only (P10b, approved 2026-10-13; a superuser holds every
role). Nothing here changes how a file is parsed, validated or loaded.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from celery import chain
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import OperationType, PaymentModule, RegisterType, SettlementCycle
from core.operations import InvalidInput, RoleRequired, WrongState, log_operation
from exception_management.roles import Role, has_role
from file_processing.models import (
    DateValidationMode,
    FileTypeConfig,
    FileValidationException,
    PayloadFormat,
    ProcessingStatus,
    UploadedFile,
    ValidationSeverity,
)
from file_processing.parsers.sniff import ContainerKind, PayloadKind, sniff_bytes, sniff_path
from file_processing.tasks import build_processing_chain, normalize_rows, promote_to_fact

logger = logging.getLogger(__name__)

# The detection endpoint needs only the start of each file: the SPA sends the
# first DETECT_HEAD_BYTES (or the whole file when smaller). The first CBS
# worksheet tag sits about 28 KB into the real export.
DETECT_HEAD_BYTES = 64 * 1024

FAILED_STATUSES = (ProcessingStatus.VALIDATION_FAILED, ProcessingStatus.FAILED)
DONE_STATUSES = (ProcessingStatus.LOADED, *FAILED_STATUSES)
# Still moving through the pipeline (the AWAITING_DATE_SELECTION pause excluded).
PROCESSING_STATUSES = tuple(
    s for s in ProcessingStatus.values if s not in (*DONE_STATUSES, ProcessingStatus.AWAITING_DATE_SELECTION)
)

SOURCE_SYSTEM = "manual web upload"
_WORKSHEET_RE = re.compile(rb'<(?:ss:)?Worksheet\b[^>]*?\bss:Name="([^"]*)"')
_CYCLE_TOKEN_RE = re.compile(r"_([A-Za-z0-9]+)\.[A-Za-z0-9]+$")
_DATE_DIRECTIVES = {"%d": r"\d{2}", "%m": r"\d{2}", "%y": r"\d{2}", "%Y": r"\d{4}"}


def require_operations_user(user) -> None:
    if not has_role(user, Role.OPERATIONS_USER):
        raise RoleRequired("Only an Operations User may upload files, choose the CBS business date or start a run.")


# ---------------------------------------------------------------------------
# file types
# ---------------------------------------------------------------------------
def active_configs(module: PaymentModule):
    return FileTypeConfig.objects.filter(payment_module=module, is_active=True).order_by("file_type_code", "version")


def config_label(config: FileTypeConfig) -> str:
    """'Raw Data ACQ', 'Merchant Raw Data (BBP) ACQ', 'CBS GL Extract' ...
    The register type's name, plus ACQ / ISS when the file-name pattern
    names exactly one of them (Raw Data and Merchant Raw Data have one
    config per side)."""
    base = RegisterType(config.register_type).label.split(" -- ")[0].strip()
    pattern = config.outer_filename_pattern.upper()
    sides = [s for s in ("ACQ", "ISS") if s in pattern]
    return f"{base} {sides[0]}" if len(sides) == 1 else base


def config_payload(config: FileTypeConfig) -> dict:
    return {
        "id": config.pk,
        "file_type_code": config.file_type_code,
        "version": config.version,
        "register_type": config.register_type,
        "label": config_label(config),
        "payload_format": config.payload_format,
        "is_cycle_wise": config.is_cycle_wise,
        "cycles": list(config.cycle_set or []) if config.is_cycle_wise else [],
        "date_mode": config.date_validation_mode,
    }


def file_type_options(module: PaymentModule) -> list[dict]:
    return [config_payload(c) for c in active_configs(module)]


def _base_name(name: str) -> str:
    return re.split(r"[\\/]", name or "")[-1].strip()


def date_from_name(name: str, date_format: str) -> date | None:
    """The date written in a file name, read with the config's
    outer_date_format (e.g. %d%m%y -> '140726' in UPIRAWDATAACQSSK140726_1C.csv,
    %Y-%m-%d in 'UPI Adjustment Report_SSK_2026-07-14.csv'). None when the
    format is not set or no valid date is found."""
    if not date_format:
        return None
    pattern = re.escape(date_format)
    for directive, digits in _DATE_DIRECTIVES.items():
        pattern = pattern.replace(re.escape(directive), digits)
    for m in re.finditer(rf"(?<!\d){pattern}(?!\d)", name):
        try:
            return datetime.strptime(m.group(0), date_format).date()
        except ValueError:
            continue
    return None


def cycle_from_name(name: str, config: FileTypeConfig) -> str | None:
    """The cycle code before the extension ('..._10C.csv' -> '10C'), if it
    is one of the config's cycles."""
    m = _CYCLE_TOKEN_RE.search(name)
    if m and m.group(1).upper() in {c.upper() for c in config.cycle_set or []}:
        return next(c for c in config.cycle_set if c.upper() == m.group(1).upper())
    return None


def _name_matches(config: FileTypeConfig, name: str) -> bool:
    if not config.outer_filename_pattern:
        return False
    try:
        return re.search(config.outer_filename_pattern, name) is not None
    except re.error:
        logger.warning("Invalid outer_filename_pattern on %s", config)
        return False


def _worksheet_names(head: bytes) -> list[str]:
    return [m.decode("utf-8", "replace") for m in _WORKSHEET_RE.findall(head)]


def _per_worksheet_keys(config: FileTypeConfig) -> set[str]:
    mapping = config.column_mapping or {}
    if mapping and all(isinstance(v, dict) for v in mapping.values()):
        return {k for v in mapping.values() for k in v if k != "_default"}
    return set()


# ---------------------------------------------------------------------------
# checks shared by detection, upload and date selection
# ---------------------------------------------------------------------------
def duplicate_of(sha256: str) -> UploadedFile | None:
    return (UploadedFile.objects.filter(sha256_hash=sha256, is_reprocess=False)
            .select_related("uploaded_by").order_by("uploaded_at").first())


def duplicate_message(existing: UploadedFile) -> str:
    who = existing.uploaded_by.get_username() if existing.uploaded_by else "an unknown user"
    when = timezone.localtime(existing.uploaded_at).strftime("%d-%b-%Y %H:%M") if existing.uploaded_at else "an unknown date"
    return f"Already uploaded on {when} by {who} (as '{existing.filename}', file #{existing.pk})."


def files_for_date(module: PaymentModule, business_date: date):
    """Current files of the date: business_date, or (CBS still waiting for
    its date) the batch date it was uploaded with."""
    return UploadedFile.objects.filter(payment_module=module, is_current=True).filter(
        Q(business_date=business_date) | Q(business_date__isnull=True, batch_business_date=business_date)
    )


def slot_conflict(config: FileTypeConfig, cycle: SettlementCycle | None, business_date: date,
                  exclude_id: int | None = None) -> UploadedFile | None:
    """A current, not failed file already holding this type / cycle / date.
    Replacing a loaded file (reprocessing) is not designed yet, so a second
    file for the same slot is refused: both would be current and counted
    twice. A failed file does not hold its slot."""
    qs = (files_for_date(config.payment_module, business_date)
          .filter(file_type_config=config, settlement_cycle=cycle)
          .exclude(processing_status__in=FAILED_STATUSES)
          .select_related("uploaded_by"))
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    return qs.order_by("uploaded_at").first()


_STATE_WORDS = {"LOADED": "loaded", "PROCESSING": "being processed",
                "AWAITING_DATE_SELECTION": "waiting for its business date"}


def slot_conflict_message(existing: UploadedFile) -> str:
    state = _STATE_WORDS.get(_file_state(existing.processing_status), existing.processing_status.lower())
    return (f"A file for this type, cycle and date is already {state}: '{existing.filename}' (file #{existing.pk}). "
            "Replacing a file is not supported yet.")


def date_is_archived(module: PaymentModule, business_date: date) -> bool:
    """True when any staging rows of the date were moved to history
    (transactions.services.archival). Uploading or picking a CBS date for an
    archived date is refused until the reprocessing-after-archival rule is
    decided (design section 10)."""
    from transactions.models import HISTORY_MODEL_BY_STAGING_MODEL

    ids = list(UploadedFile.objects.filter(payment_module=module, business_date=business_date)
               .values_list("pk", flat=True))
    if not ids:
        return False
    return any(history.objects.filter(uploaded_file_id__in=ids).exists()
               for history in HISTORY_MODEL_BY_STAGING_MODEL.values())


ARCHIVED_MESSAGE = ("Business date {d} has been archived. Loading more files for an archived date is not "
                    "supported until the reprocessing-after-archival rule is decided.")


# ---------------------------------------------------------------------------
# detection (dry run)
# ---------------------------------------------------------------------------
@dataclass
class DetectInput:
    name: str
    head: bytes
    sha256: str = ""


def _detected_format(head: bytes) -> tuple[str, bool]:
    """(format, partial): a zip whose directory is not in the head (only the
    start was sent) cannot be checked here; the upload checks it."""
    sniffed = sniff_bytes(head)
    if sniffed.container == ContainerKind.ZIP and sniffed.payload in (None, PayloadKind.UNKNOWN):
        return "ZIP", True
    return (sniffed.payload.value if sniffed.payload else ""), False


def _detect_one(item: DetectInput, configs: list[FileTypeConfig], module: PaymentModule) -> dict:
    name = _base_name(item.name)
    fmt, partial = _detected_format(item.head)
    warnings: list[str] = []
    matched = [c for c in configs if _name_matches(c, name)]
    matched_by = "filename" if matched else ""
    if not matched:
        # No name pattern matched: recognise by content. Only a type with no
        # fixed file name qualifies (CBS), and only when exactly one such type
        # has this content format.
        by_content = [c for c in configs if not c.outer_filename_pattern and c.payload_format == fmt]
        if len(by_content) == 1:
            matched, matched_by = by_content, "content"
    if len(matched) > 1:
        warnings.append("More than one file type matches this file name: "
                        + ", ".join(config_label(c) for c in matched) + ". Choose one.")
    config = matched[0] if len(matched) == 1 else None

    proposal = None
    if config is not None:
        cycle = cycle_from_name(name, config) if config.is_cycle_wise else None
        if config.is_cycle_wise and cycle is None:
            warnings.append("No settlement cycle found in the file name. Choose one.")
        name_date = (date_from_name(name, config.outer_date_format)
                     if config.date_validation_mode == DateValidationMode.EXACT_MATCH else None)
        if fmt and not partial and fmt != config.payload_format:
            warnings.append(f"The content is {PayloadFormat(fmt).label if fmt in PayloadFormat.values else fmt}, "
                            f"but {config_label(config)} files are {PayloadFormat(config.payload_format).label}. "
                            "The upload will be refused.")
        sheets = _worksheet_names(item.head)
        keys = _per_worksheet_keys(config)
        if sheets and keys and not set(sheets) & keys:
            warnings.append(f"Worksheet {sheets[0]!r} is not one of the known {config_label(config)} layouts "
                            f"({', '.join(sorted(keys))}).")
        proposal = {
            "file_type_config_id": config.pk,
            "file_type_code": config.file_type_code,
            "label": config_label(config),
            "cycle": cycle,
            "business_date": name_date,
            "date_mode": config.date_validation_mode,
            "matched_by": matched_by,
        }
        if config.date_validation_mode == DateValidationMode.RANGE_CONTAINS:
            warnings.append("This file covers several dates. After processing, confirm its business date "
                            "from the dates found in it.")
    else:
        warnings.append("The file type could not be recognised. Choose it.")

    duplicate = None
    sha = (item.sha256 or "").strip().lower()
    if sha:
        existing = duplicate_of(sha)
        if existing is not None:
            duplicate = {"uploaded_file_id": existing.pk, "filename": existing.filename,
                         "uploaded_at": existing.uploaded_at,
                         "uploaded_by": existing.uploaded_by.get_username() if existing.uploaded_by else None,
                         "message": duplicate_message(existing)}
    return {"filename": name, "detected_format": fmt, "proposal": proposal, "duplicate": duplicate,
            "warnings": warnings}


def detect_files(user, module: PaymentModule, items: Iterable[DetectInput]) -> dict:
    require_operations_user(user)
    configs = list(active_configs(module))
    files = [{"index": i, **_detect_one(item, configs, module)} for i, item in enumerate(items)]

    dates = Counter(f["proposal"]["business_date"] for f in files
                    if f["proposal"] and f["proposal"]["business_date"])
    proposed = max(dates, key=lambda d: (dates[d], d)) if dates else None
    warnings: list[str] = []
    if len(dates) > 1:
        warnings.append("The files carry different dates in their names: "
                        + ", ".join(d.isoformat() for d in sorted(dates))
                        + ". One upload is for one business date; files of another date will be refused.")
    if proposed is not None:
        for f in files:
            p = f["proposal"]
            if p and p["business_date"] in (None, proposed) and p["date_mode"] == DateValidationMode.EXACT_MATCH:
                cfg = next(c for c in configs if c.pk == p["file_type_config_id"])
                cycle = (SettlementCycle.objects.filter(payment_module=module, cycle_code=p["cycle"]).first()
                         if p["cycle"] else None)
                if not cfg.is_cycle_wise or cycle is not None:
                    existing = slot_conflict(cfg, cycle, proposed)
                    if existing is not None:
                        f["warnings"].append(slot_conflict_message(existing))
        if date_is_archived(module, proposed):
            warnings.append(ARCHIVED_MESSAGE.format(d=proposed.isoformat()))
    return {
        "business_date": proposed,
        "business_dates_found": sorted(dates),
        "warnings": warnings,
        "files": files,
        "file_types": [config_payload(c) for c in configs],
        "max_file_bytes": settings.UPLOAD_MAX_FILE_BYTES,
        "detect_head_bytes": DETECT_HEAD_BYTES,
    }


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------
@dataclass
class UploadItem:
    name: str
    file: object  # django.core.files.uploadedfile.UploadedFile (chunks(), size)
    file_type_config_id: int | None
    cycle: str | None = None


@dataclass
class UploadOutcome:
    business_date: date
    accepted: list[UploadedFile] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)


def upload_root() -> Path:
    root = Path(settings.FILE_PROCESSING_WORK_DIR)
    if not root.is_absolute():
        root = Path(settings.BASE_DIR) / root
    return root.resolve() / "uploads"


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return cleaned[:150] or "file"


def _store_temp(item: UploadItem, incoming: Path) -> tuple[Path, str, int]:
    """Writes the upload to a temporary file on the data drive while hashing
    it; never holds the file in memory."""
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{uuid.uuid4().hex}.part"
    digest, size = hashlib.sha256(), 0
    with open(tmp, "wb") as out:
        for chunk in item.file.chunks():
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return tmp, digest.hexdigest(), size


def _queue_processing(uploaded_file_id: int) -> None:
    try:
        build_processing_chain(uploaded_file_id).apply_async()
    except Exception as exc:  # noqa: BLE001 - broker down: the file stays RECEIVED and says why
        logger.exception("Could not queue processing for UploadedFile %s", uploaded_file_id)
        FileValidationException.objects.create(
            uploaded_file_id=uploaded_file_id, rule_code="QUEUE_FAILED", severity=ValidationSeverity.ERROR,
            message=f"The file was saved but its processing could not be queued: {exc}",
        )


def _check_item(item: UploadItem, module: PaymentModule, business_date: date, configs: dict[int, FileTypeConfig],
                cycles: dict[str, SettlementCycle], errors: list[str]) -> tuple[FileTypeConfig | None,
                                                                                SettlementCycle | None]:
    config = configs.get(item.file_type_config_id) if isinstance(item.file_type_config_id, int) else None
    if config is None:
        errors.append("Choose a file type of this payment module.")
        return None, None
    cycle = None
    if config.is_cycle_wise:
        if not item.cycle:
            errors.append(f"{config_label(config)} files are per settlement cycle; choose the cycle.")
        elif item.cycle not in (config.cycle_set or []) or item.cycle not in cycles:
            errors.append(f"Cycle {item.cycle!r} is not a cycle of {config_label(config)} "
                          f"({', '.join(config.cycle_set or [])}).")
        else:
            cycle = cycles[item.cycle]
    elif item.cycle:
        errors.append(f"{config_label(config)} files are not per cycle; leave the cycle empty.")
    if config.date_validation_mode == DateValidationMode.EXACT_MATCH:
        name_date = date_from_name(_base_name(item.name), config.outer_date_format)
        if name_date is not None and name_date != business_date:
            errors.append(f"The file name carries the date {name_date.isoformat()}, not the chosen business date "
                          f"{business_date.isoformat()}.")
    return config, cycle


def upload_files(user, module: PaymentModule, business_date: date, items: list[UploadItem], *,
                 source: str = "api") -> UploadOutcome:
    """Each file is accepted or refused on its own (a refused file never stops
    the others). Accepted: stored under FILE_PROCESSING_WORK_DIR/uploads/,
    UploadedFile created with uploaded_by, FILE_UPLOADED logged, and the
    existing processing chain queued after commit. Refused: nothing stored,
    FILE_REFUSED logged with the reasons."""
    require_operations_user(user)
    if not items:
        raise InvalidInput("Choose at least one file.", {"files": ["Choose at least one file."]})
    if date_is_archived(module, business_date):
        raise WrongState(ARCHIVED_MESSAGE.format(d=business_date.isoformat()))

    configs = {c.pk: c for c in active_configs(module)}
    cycles = {c.cycle_code: c for c in SettlementCycle.objects.filter(payment_module=module, is_active=True)}
    root = upload_root()
    incoming = root / "_incoming"
    outcome = UploadOutcome(business_date)
    seen_hashes: set[str] = set()

    for index, item in enumerate(items):
        name = _base_name(item.name)
        errors: list[str] = []
        config, cycle = _check_item(item, module, business_date, configs, cycles, errors)
        size = getattr(item.file, "size", None) or 0
        if size > settings.UPLOAD_MAX_FILE_BYTES:
            errors.append(f"The file is {size:,} bytes; the limit is {settings.UPLOAD_MAX_FILE_BYTES:,}.")
        tmp, sha = None, ""
        if not errors:
            tmp, sha, size = _store_temp(item, incoming)
            if sha in seen_hashes:
                errors.append("The same file was chosen twice in this upload.")
            else:
                existing = duplicate_of(sha)
                if existing is not None:
                    errors.append(duplicate_message(existing))
            if not errors:
                sniffed = sniff_path(tmp)
                detected = sniffed.payload.value if sniffed.payload else sniffed.container.value
                if detected != config.payload_format:
                    errors.append(f"The content does not match {config_label(config)}: it is {detected}, "
                                  f"expected {config.payload_format}.")
            if not errors:
                existing = slot_conflict(config, cycle, business_date)
                if existing is not None:
                    errors.append(slot_conflict_message(existing))

        if not errors:
            final_dir = root / module.code.lower() / business_date.isoformat()
            final_dir.mkdir(parents=True, exist_ok=True)
            final = final_dir / f"{sha[:16]}_{_safe_name(name)}"
            shutil.move(str(tmp), final)
            try:
                with transaction.atomic():
                    exact = config.date_validation_mode == DateValidationMode.EXACT_MATCH
                    uploaded = UploadedFile.objects.create(
                        filename=name[:255], path=str(final), source_system=SOURCE_SYSTEM, payment_module=module,
                        file_type_config=config, business_date=business_date if exact else None,
                        batch_business_date=business_date, settlement_cycle=cycle,
                        declared_format=os.path.splitext(name)[1].lower()[:30], size_bytes=size, sha256_hash=sha,
                        uploaded_by=user,
                    )
                    log_operation(OperationType.FILE_UPLOADED, user=user, payment_module=module,
                                  business_date=business_date, uploaded_file_id=uploaded.pk, source=source,
                                  details={"filename": uploaded.filename, "sha256": sha, "size_bytes": size,
                                           "file_type_config_id": config.pk, "file_type": config_label(config),
                                           "cycle": cycle.cycle_code if cycle else None})
                    transaction.on_commit(lambda pk=uploaded.pk: _queue_processing(pk))
            except IntegrityError:
                final.unlink(missing_ok=True)
                existing = duplicate_of(sha)
                errors.append(duplicate_message(existing) if existing else "The file could not be saved (duplicate).")
            else:
                seen_hashes.add(sha)
                outcome.accepted.append(uploaded)
                continue
        elif tmp is not None:
            tmp.unlink(missing_ok=True)

        log_operation(OperationType.FILE_REFUSED, user=user, payment_module=module, business_date=business_date,
                      source=source, details={"filename": name, "sha256": sha, "size_bytes": size,
                                              "file_type_config_id": config.pk if config else None,
                                              "cycle": item.cycle or None, "errors": errors})
        outcome.refused.append({"index": index, "filename": name, "errors": errors})
    return outcome


# ---------------------------------------------------------------------------
# CBS business-date selection (moved out of file_processing/admin.py)
# ---------------------------------------------------------------------------
def date_selection(uploaded_file: UploadedFile) -> dict | None:
    """What the date picker shows for a paused file: the dates found in it
    and the batch date pre-selected when it is one of them (Amendment 1)."""
    if uploaded_file.processing_status != ProcessingStatus.AWAITING_DATE_SELECTION:
        return None
    detected = list(uploaded_file.detected_dates or [])
    batch = uploaded_file.batch_business_date.isoformat() if uploaded_file.batch_business_date else None
    return {"detected_dates": detected, "batch_business_date": batch,
            "preselected": batch if batch in detected else None}


def _resume_after_date_selection(uploaded_file_id: int) -> None:
    # Rows were staged before the pause, so the resumed chain needs only the
    # file id: normalize_rows reads the staged rows back.
    chain(
        normalize_rows.s({"uploaded_file_id": uploaded_file_id, "halted": False}),
        promote_to_fact.s(),
    ).apply_async()


def select_business_date(user, uploaded_file: UploadedFile, chosen: str | date, *, source: str = "api") -> UploadedFile:
    """Applies the chosen business date to a RANGE_CONTAINS file paused at
    AWAITING_DATE_SELECTION and resumes processing (normalize -> fact).
    The date must be one of the dates found in the file. Audited:
    BUSINESS_DATE_SELECTED with who, when, the detected dates and the batch
    date."""
    require_operations_user(user)
    chosen_iso = chosen.isoformat() if isinstance(chosen, date) else str(chosen or "").strip()
    with transaction.atomic():
        f = UploadedFile.objects.select_for_update().select_related("file_type_config", "payment_module").get(
            pk=uploaded_file.pk)
        if f.processing_status != ProcessingStatus.AWAITING_DATE_SELECTION:
            raise WrongState(f"'{f.filename}' is not awaiting date selection (status: {f.processing_status}).")
        if chosen_iso not in (f.detected_dates or []):
            raise InvalidInput(
                f"'{chosen_iso}' is not one of the dates found in this file ({', '.join(f.detected_dates or [])}).",
                {"business_date": ["Choose one of the dates found in the file."]},
            )
        chosen_date = date.fromisoformat(chosen_iso)
        if date_is_archived(f.payment_module, chosen_date):
            raise WrongState(ARCHIVED_MESSAGE.format(d=chosen_iso))
        existing = slot_conflict(f.file_type_config, f.settlement_cycle, chosen_date, exclude_id=f.pk)
        # A file still waiting with the same batch date is not a conflict: only one of them can be confirmed.
        if existing is not None and existing.business_date is not None:
            raise WrongState(slot_conflict_message(existing))
        f.business_date = chosen_date
        f.processing_status = ProcessingStatus.STAGED
        f.save(update_fields=["business_date", "processing_status"])
        log_operation(OperationType.BUSINESS_DATE_SELECTED, user=user, payment_module=f.payment_module,
                      business_date=chosen_date, uploaded_file_id=f.pk, source=source,
                      details={"filename": f.filename, "detected_dates": list(f.detected_dates or []),
                               "batch_business_date": (f.batch_business_date.isoformat()
                                                       if f.batch_business_date else None)})
        transaction.on_commit(lambda pk=f.pk: _resume_after_date_selection(pk))
    return f


# ---------------------------------------------------------------------------
# expected files checklist (design 4.11)
# ---------------------------------------------------------------------------
SLOT_STATES = ("LOADED", "PROCESSING", "AWAITING_DATE_SELECTION", "FAILED", "MISSING")


def _file_state(status: str) -> str:
    if status == ProcessingStatus.LOADED:
        return "LOADED"
    if status == ProcessingStatus.AWAITING_DATE_SELECTION:
        return "AWAITING_DATE_SELECTION"
    if status in FAILED_STATUSES:
        return "FAILED"
    return "PROCESSING"


def date_checklists(module: PaymentModule, dates: list[date]) -> dict[date, dict]:
    """{date: {"summary": {...counts}, "slots": [...], "unexpected": [...]}}.
    Expected = every active FileTypeConfig, once per cycle in its cycle_set
    when cycle-wise, else once. A slot's state is the best of its current
    files: LOADED > PROCESSING > AWAITING_DATE_SELECTION > FAILED; MISSING
    when none."""
    configs = list(active_configs(module))
    files = (UploadedFile.objects.filter(payment_module=module, is_current=True)
             .filter(Q(business_date__in=dates) | Q(business_date__isnull=True, batch_business_date__in=dates))
             .select_related("settlement_cycle")
             .only("id", "filename", "processing_status", "business_date", "batch_business_date",
                   "file_type_config_id", "settlement_cycle__cycle_code", "uploaded_at")
             .order_by("uploaded_at", "id"))
    by_date: dict[date, list[UploadedFile]] = {d: [] for d in dates}
    for f in files:
        by_date.setdefault(f.business_date or f.batch_business_date, []).append(f)

    out = {}
    for d in dates:
        slot_files: dict[tuple[int, str | None], list[UploadedFile]] = {}
        for f in by_date.get(d, []):
            key = (f.file_type_config_id, f.settlement_cycle.cycle_code if f.settlement_cycle else None)
            slot_files.setdefault(key, []).append(f)
        slots, expected_keys = [], set()
        for c in configs:
            for cycle in (c.cycle_set or [None]) if c.is_cycle_wise else [None]:
                key = (c.pk, cycle)
                expected_keys.add(key)
                fs = slot_files.get(key, [])
                states = [_file_state(f.processing_status) for f in fs]
                state = next((s for s in SLOT_STATES if s in states), "MISSING")
                slots.append({"file_type_config_id": c.pk, "file_type_code": c.file_type_code,
                              "label": config_label(c), "cycle": cycle, "state": state,
                              "files": [{"id": f.pk, "filename": f.filename, "processing_status": f.processing_status}
                                        for f in fs]})
        unexpected = [{"id": f.pk, "filename": f.filename, "processing_status": f.processing_status,
                       "file_type_config_id": key[0], "cycle": key[1]}
                      for key, fs in slot_files.items() if key not in expected_keys for f in fs]
        counts = Counter(s["state"] for s in slots)
        file_states = Counter(_file_state(f.processing_status) for f in by_date.get(d, []))
        out[d] = {
            # Slot counts (expected type x cycle), then file counts (every current file of the date).
            "summary": {"expected": len(slots), "loaded": counts["LOADED"], "processing": counts["PROCESSING"],
                        "awaiting_date_selection": counts["AWAITING_DATE_SELECTION"], "failed": counts["FAILED"],
                        "missing": counts["MISSING"], "files_received": len(by_date.get(d, [])),
                        "files_loaded": file_states["LOADED"], "files_processing": file_states["PROCESSING"],
                        "files_awaiting_date_selection": file_states["AWAITING_DATE_SELECTION"],
                        "files_failed": file_states["FAILED"]},
            "slots": slots,
            "unexpected": unexpected,
        }
    return out
