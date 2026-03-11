"""Collect coded PDFs from PMs and read form field values."""

from pathlib import Path

from pypdf import PdfReader

from .config_loader import DATA_DIR, load_vendor_list, find_vendor_by_name


PM_INBOX_DIR = DATA_DIR / "pm-inbox"


def read_coded_pdf(pdf_path):
    """Read form field values from a PM-coded PDF.

    Returns list of dicts, one per page, each with coding rows.
    """
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}

    pages = []
    num_pages = len(reader.pages)

    for page_idx in range(num_pages):
        page_data = {
            "page": page_idx + 1,
            "approved": False,
            "codings": [],
        }

        # Check approved checkbox
        cb_name = f"ApprovedCheckbox_{page_idx}"
        if cb_name in fields:
            val = fields[cb_name].get("/V")
            page_data["approved"] = val in ("/Yes", "Yes", True)

        # Read coding rows
        for row in range(3):
            job_val = _get_field_value(fields, f"JobDropdown_{row}_{page_idx}")
            phase_val = _get_field_value(fields, f"PhaseDropdown_{row}_{page_idx}")
            cost_val = _get_field_value(fields, f"CostDropdown_{row}_{page_idx}")
            amount_val = _get_field_value(fields, f"AmountInput_{row}_{page_idx}")

            # Skip empty rows
            if not job_val or job_val == "Job":
                continue

            coding = {
                "job": job_val,
                "phase": phase_val or "",
                "cost": cost_val or "",
                "amount": amount_val or "",
            }

            # Parse job number from full job string
            if "." in coding["job"]:
                coding["job_number"] = coding["job"].split(".")[0]
            else:
                coding["job_number"] = coding["job"]

            # Parse phase number
            if "." in coding["phase"]:
                coding["phase_number"] = coding["phase"].split(".")[0]

            # Parse cost number
            if "." in coding["cost"]:
                coding["cost_number"] = coding["cost"].split(".")[0]
                # Check if it's a PO or subcontract
                cost_prefix = coding["cost_number"]
                if cost_prefix.startswith("PO"):
                    coding["is_po"] = True
                elif cost_prefix.startswith("S"):
                    coding["is_subcontract"] = True

            page_data["codings"].append(coding)

        pages.append(page_data)

    return pages


def scan_pm_inbox():
    """Scan the PM inbox directory for returned coded PDFs."""
    if not PM_INBOX_DIR.exists():
        return []

    results = []
    for pdf_file in sorted(PM_INBOX_DIR.glob("**/*.pdf")):
        try:
            pages = read_coded_pdf(pdf_file)
            results.append({
                "file": str(pdf_file),
                "filename": pdf_file.name,
                "pages": pages,
                "total_pages": len(pages),
                "coded_pages": sum(1 for p in pages if p["codings"]),
            })
        except Exception as e:
            results.append({
                "file": str(pdf_file),
                "filename": pdf_file.name,
                "error": str(e),
            })

    return results


def _get_field_value(fields, field_name):
    """Get string value from a form field."""
    if field_name not in fields:
        return None
    val = fields[field_name].get("/V")
    if val is None:
        return None
    return str(val).strip()
