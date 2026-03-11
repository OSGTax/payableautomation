"""Prepare invoices for PM coding: group pages, apply form field dropdowns."""

import json
import os
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import (
    ArrayObject, BooleanObject, DictionaryObject, FloatObject,
    NameObject, NumberObject, TextStringObject, IndirectObject,
)

from .config_loader import (
    BASE_DIR, DATA_DIR, load_job_codes, load_pm_assignments, get_pm_for_job,
)
from .intake import load_batch, save_batch, INBOX_DIR


PM_OUTBOX_DIR = DATA_DIR / "pm-outbox"
NUM_CODE_ROWS = 3  # Number of job/phase/cost rows per page


def _create_choice_field(writer, name, options, rect, page_ref, default=""):
    """Create a dropdown (choice) form field on a PDF page."""
    opt_array = ArrayObject([TextStringObject(o) for o in options])

    field = DictionaryObject()
    field.update({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Ch"),
        NameObject("/Ff"): NumberObject(1 << 17),  # Combo box flag
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): TextStringObject(default),
        NameObject("/DV"): TextStringObject(default),
        NameObject("/Opt"): opt_array,
        NameObject("/Rect"): ArrayObject([FloatObject(x) for x in rect]),
        NameObject("/P"): page_ref,
        NameObject("/DA"): TextStringObject("/Helv 8 Tf 0 g"),
    })
    return field


def _create_text_field(writer, name, rect, page_ref, default=""):
    """Create a text input form field on a PDF page."""
    field = DictionaryObject()
    field.update({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): TextStringObject(default),
        NameObject("/DV"): TextStringObject(default),
        NameObject("/Rect"): ArrayObject([FloatObject(x) for x in rect]),
        NameObject("/P"): page_ref,
        NameObject("/DA"): TextStringObject("/Helv 8 Tf 0 g"),
    })
    return field


def _create_checkbox_field(writer, name, rect, page_ref):
    """Create a checkbox form field."""
    field = DictionaryObject()
    field.update({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/Rect"): ArrayObject([FloatObject(x) for x in rect]),
        NameObject("/P"): page_ref,
        NameObject("/DA"): TextStringObject("/ZaDb 10 Tf 0 g"),
    })
    return field


def _create_label_field(writer, name, rect, page_ref, text="APPROVED"):
    """Create a read-only text label field."""
    field = DictionaryObject()
    field.update({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/Ff"): NumberObject(1),  # Read-only
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): TextStringObject(text),
        NameObject("/Rect"): ArrayObject([FloatObject(x) for x in rect]),
        NameObject("/P"): page_ref,
        NameObject("/DA"): TextStringObject("/Helv 8 Tf 0 g"),
    })
    return field


def add_coding_fields_to_pdf(input_pdf, output_pdf, job_codes):
    """Add job/phase/cost dropdown fields to each page of a PDF.

    Mimics the existing Adobe script field layout:
    - ApprovedCheckbox_N, ApprovedLabel_N
    - JobDropdown_R_N, PhaseDropdown_R_N, CostDropdown_R_N, AmountInput_R_N
    Where N = page index, R = row (0-2)
    """
    reader = PdfReader(input_pdf)
    writer = PdfWriter()

    # Build option lists
    job_options = ["Job"] + sorted(job_codes.keys())

    # Build phase options per job, cost options per (job, phase)
    phase_options_map = {}
    cost_options_map = {}
    for job_full, job_data in job_codes.items():
        phases = sorted(job_data["phases"].keys())
        phase_options_map[job_full] = phases
        for phase, costs in job_data["phases"].items():
            cost_options_map[(job_full, phase)] = sorted(costs)

    # All phases and costs flattened (for initial dropdown population)
    all_phases = sorted(set(
        phase for job_data in job_codes.values()
        for phase in job_data["phases"].keys()
    ))
    all_costs = sorted(set(
        cost for job_data in job_codes.values()
        for costs in job_data["phases"].values()
        for cost in costs
    ))

    for page_idx in range(len(reader.pages)):
        writer.add_page(reader.pages[page_idx])
        page_ref = writer.pages[page_idx]

        # Field positioning - bottom of page
        # Matching the existing Adobe script layout
        y_base = 30  # bottom margin
        row_height = 14
        fields = []

        # Approved checkbox and label
        cb = _create_checkbox_field(
            writer, f"ApprovedCheckbox_{page_idx}",
            [20, y_base + NUM_CODE_ROWS * row_height + 5,
             32, y_base + NUM_CODE_ROWS * row_height + 17],
            page_ref
        )
        fields.append(cb)

        lbl = _create_label_field(
            writer, f"ApprovedLabel_{page_idx}",
            [34, y_base + NUM_CODE_ROWS * row_height + 5,
             90, y_base + NUM_CODE_ROWS * row_height + 17],
            page_ref, "APPROVED"
        )
        fields.append(lbl)

        # Code rows
        for row in range(NUM_CODE_ROWS):
            y = y_base + (NUM_CODE_ROWS - 1 - row) * row_height

            # Job dropdown
            job_field = _create_choice_field(
                writer, f"JobDropdown_{row}_{page_idx}",
                job_options,
                [20, y, 220, y + row_height],
                page_ref, "Job"
            )
            fields.append(job_field)

            # Phase dropdown (all phases initially, JS would filter in Adobe)
            phase_field = _create_choice_field(
                writer, f"PhaseDropdown_{row}_{page_idx}",
                all_phases,
                [222, y, 370, y + row_height],
                page_ref
            )
            fields.append(phase_field)

            # Cost dropdown
            cost_field = _create_choice_field(
                writer, f"CostDropdown_{row}_{page_idx}",
                all_costs,
                [372, y, 510, y + row_height],
                page_ref
            )
            fields.append(cost_field)

            # Amount input
            amt_field = _create_text_field(
                writer, f"AmountInput_{row}_{page_idx}",
                [512, y, 592, y + row_height],
                page_ref
            )
            fields.append(amt_field)

        # Add fields as annotations
        if "/Annots" not in page_ref:
            page_ref[NameObject("/Annots")] = ArrayObject()

        annots = page_ref["/Annots"]
        for field in fields:
            field_ref = writer._add_object(field)
            annots.append(field_ref)

        # Add to AcroForm
        if "/AcroForm" not in writer._root_object:
            writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
                NameObject("/Fields"): ArrayObject(),
                NameObject("/NeedAppearances"): BooleanObject(True),
            })
        acroform = writer._root_object["/AcroForm"]
        if "/Fields" not in acroform:
            acroform[NameObject("/Fields")] = ArrayObject()
        for field in fields:
            field_ref = writer._add_object(field)
            acroform["/Fields"].append(field_ref)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    return output_pdf


def group_and_distribute(batch_id):
    """Group prescreened invoice pages by PM, create coded PDFs, save to PM outbox."""
    manifest = load_batch(batch_id)
    batch_dir = INBOX_DIR / batch_id
    config = load_pm_assignments()
    job_codes = load_job_codes()

    # Group pages into invoices (respecting group_with_prev)
    invoices = []
    current_invoice = None

    for page in manifest["pages"]:
        if page["status"] == "skipped":
            continue

        if page["group_with_prev"] and current_invoice is not None:
            current_invoice["pages"].append(page)
        else:
            if current_invoice is not None:
                invoices.append(current_invoice)
            current_invoice = {
                "pages": [page],
                "pm": page.get("assigned_pm") or page.get("suggested_pm"),
                "job": page.get("assigned_job") or page.get("suggested_job"),
                "status": page["status"],
            }

    if current_invoice is not None:
        invoices.append(current_invoice)

    # Group invoices by PM
    pm_groups = {}
    overhead_invoices = []

    for inv in invoices:
        if inv["status"] == "overhead":
            overhead_invoices.append(inv)
            continue

        pm = inv["pm"] or "unassigned"
        if pm not in pm_groups:
            pm_groups[pm] = []
        pm_groups[pm].append(inv)

    # Create PM output PDFs
    date_str = datetime.now().strftime("%m.%d.%y")
    results = {"pm_files": {}, "overhead_file": None}

    for pm_name, pm_invoices in pm_groups.items():
        pm_dir = PM_OUTBOX_DIR / pm_name
        pm_dir.mkdir(parents=True, exist_ok=True)
        output_filename = f"{pm_name} - {date_str}.pdf"
        output_path = pm_dir / output_filename

        # Merge all invoice pages for this PM
        writer = PdfWriter()
        for inv in pm_invoices:
            for page in inv["pages"]:
                page_pdf = batch_dir / page["pdf_file"]
                reader = PdfReader(page_pdf)
                writer.add_page(reader.pages[0])

        # Save merged PDF first
        temp_path = pm_dir / f"_temp_{output_filename}"
        with open(temp_path, "wb") as f:
            writer.write(f)

        # Add coding form fields
        add_coding_fields_to_pdf(temp_path, output_path, job_codes)
        os.remove(temp_path)

        results["pm_files"][pm_name] = str(output_path)

    # Handle overhead invoices (save without coding fields - already coded by user)
    if overhead_invoices:
        overhead_dir = DATA_DIR / "prescreened"
        overhead_dir.mkdir(parents=True, exist_ok=True)
        overhead_path = overhead_dir / f"overhead_{date_str}.json"

        overhead_data = []
        for inv in overhead_invoices:
            first_page = inv["pages"][0]
            overhead_data.append({
                "pages": [p["page_num"] for p in inv["pages"]],
                "coding": first_page.get("overhead_coding", {}),
            })

        with open(overhead_path, "w") as f:
            json.dump(overhead_data, f, indent=2)
        results["overhead_file"] = str(overhead_path)

    # Update manifest status
    manifest["distributed"] = True
    manifest["distributed_at"] = datetime.now().isoformat()
    manifest["distribution_results"] = {
        "pm_files": results["pm_files"],
        "overhead_count": len(overhead_invoices),
    }
    save_batch(batch_id, manifest)

    return results
