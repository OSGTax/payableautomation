"""Export coded invoices to Computerease XML import format."""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .config_loader import DATA_DIR, load_vendor_list, find_vendor_by_name
from .collect import read_coded_pdf, scan_pm_inbox


EXPORT_DIR = DATA_DIR / "exported"


def build_ce_invoice_element(vendor_code, invoice_data):
    """Build a single <apinvoice> XML element for CE import.

    invoice_data should contain:
        - vendor_number: CE vendor code
        - inv_date: invoice date string
        - inv_number: invoice number
        - description: invoice description
        - amount: total amount
        - distributions: list of dicts with job/phase/cost/amount
    """
    inv = ET.SubElement(ET.Element("root"), "apinvoice")

    ET.SubElement(inv, "vennum").text = str(invoice_data.get("vendor_number", vendor_code))
    ET.SubElement(inv, "invdate").text = invoice_data.get("inv_date", "")
    ET.SubElement(inv, "invnum").text = invoice_data.get("inv_number", "")
    ET.SubElement(inv, "desc").text = invoice_data.get("description", "")[:30]
    ET.SubElement(inv, "amount").text = str(invoice_data.get("amount", "0"))

    if invoice_data.get("po_number"):
        ET.SubElement(inv, "ponum").text = str(invoice_data["po_number"])[:10]

    # Distribution lines
    for dist in invoice_data.get("distributions", []):
        if dist.get("job_number"):
            ET.SubElement(inv, "jobnum").text = str(dist["job_number"])[:10]
        if dist.get("phase_number"):
            ET.SubElement(inv, "phasenum").text = str(dist["phase_number"])[:4]
        if dist.get("cost_number"):
            ET.SubElement(inv, "catnum").text = str(dist["cost_number"])[:6]
        if dist.get("cost_type"):
            ET.SubElement(inv, "ctnum").text = str(dist["cost_type"])
        if dist.get("gl_account"):
            ET.SubElement(inv, "glaccount").text = str(dist["gl_account"])[:9]
        if dist.get("dist_amount"):
            # Distribution amount for split invoices
            ET.SubElement(inv, "amount").text = str(dist["dist_amount"])
        if dist.get("dist_description"):
            ET.SubElement(inv, "distdesc").text = str(dist["dist_description"])[:30]
        if dist.get("qty"):
            ET.SubElement(inv, "qty").text = str(dist["qty"])
        if dist.get("hours"):
            ET.SubElement(inv, "hours").text = str(dist["hours"])

    return inv


def export_coded_pdfs_to_xml(coded_files=None, output_path=None):
    """Read all coded PDFs and generate CE XML import file.

    Args:
        coded_files: list of PDF paths, or None to scan pm-inbox
        output_path: output XML path, or None for auto-generated name
    """
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = EXPORT_DIR / f"ap_import_{date_str}.xml"

    vendors = load_vendor_list()

    # Build XML
    root = ET.Element("apinvoices")

    if coded_files is None:
        inbox_results = scan_pm_inbox()
        coded_files = [r["file"] for r in inbox_results if "error" not in r]

    for pdf_path in coded_files:
        pages = read_coded_pdf(pdf_path)

        for page in pages:
            if not page["codings"]:
                continue

            for coding in page["codings"]:
                inv_elem = ET.SubElement(root, "apinvoice")
                ET.SubElement(inv_elem, "jobnum").text = coding.get("job_number", "")
                ET.SubElement(inv_elem, "phasenum").text = coding.get("phase_number", "")
                ET.SubElement(inv_elem, "catnum").text = coding.get("cost_number", "")

                if coding.get("amount"):
                    try:
                        ET.SubElement(inv_elem, "amount").text = str(
                            float(coding["amount"].replace(",", "").replace("$", ""))
                        )
                    except ValueError:
                        ET.SubElement(inv_elem, "amount").text = coding["amount"]

    # Write XML
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)

    return str(output_path)


def export_browser_codings_to_xml(tokens=None, output_path=None):
    """Export browser-submitted PM codings to CE XML format.

    Args:
        tokens: list of assignment tokens, or None for all completed
        output_path: output XML path
    """
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    assignments_dir = DATA_DIR / "assignments"

    if output_path is None:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = EXPORT_DIR / f"ap_import_{date_str}.xml"

    root = ET.Element("apinvoices")

    # Load assignments
    if tokens:
        files = [assignments_dir / f"{t}.json" for t in tokens]
    else:
        files = sorted(assignments_dir.glob("*.json")) if assignments_dir.exists() else []

    for fpath in files:
        if not fpath.exists():
            continue
        import json
        with open(fpath) as f:
            assignment = json.load(f)

        if assignment.get("status") != "completed":
            continue

        for page_num, codings in assignment.get("codings", {}).items():
            for coding in codings:
                job_val = coding.get("job", "")
                if not job_val:
                    continue

                inv_elem = ET.SubElement(root, "apinvoice")

                # Parse job number from full string
                job_number = job_val.split(".")[0] if "." in job_val else job_val
                ET.SubElement(inv_elem, "jobnum").text = job_number

                phase_val = coding.get("phase", "")
                if phase_val:
                    phase_number = phase_val.split(".")[0] if "." in phase_val else phase_val
                    ET.SubElement(inv_elem, "phasenum").text = phase_number

                cost_val = coding.get("cost", "")
                if cost_val:
                    cost_number = cost_val.split(".")[0] if "." in cost_val else cost_val
                    ET.SubElement(inv_elem, "catnum").text = cost_number

                amount_val = coding.get("amount", "")
                if amount_val:
                    try:
                        ET.SubElement(inv_elem, "amount").text = str(
                            float(amount_val.replace(",", "").replace("$", ""))
                        )
                    except ValueError:
                        ET.SubElement(inv_elem, "amount").text = amount_val

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)

    return str(output_path)


def export_overhead_to_xml(overhead_data, output_path=None):
    """Export overhead invoice codings to CE XML format.

    overhead_data: list of dicts with coding info from pre-screening.
    """
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = EXPORT_DIR / f"overhead_import_{date_str}.xml"

    root = ET.Element("apinvoices")

    for item in overhead_data:
        coding = item.get("coding", {})
        inv_elem = ET.SubElement(root, "apinvoice")

        if coding.get("vendor_number"):
            ET.SubElement(inv_elem, "vennum").text = coding["vendor_number"]
        if coding.get("inv_date"):
            ET.SubElement(inv_elem, "invdate").text = coding["inv_date"]
        if coding.get("inv_number"):
            ET.SubElement(inv_elem, "invnum").text = coding["inv_number"]
        if coding.get("description"):
            ET.SubElement(inv_elem, "desc").text = coding["description"][:30]
        if coding.get("amount"):
            ET.SubElement(inv_elem, "amount").text = str(coding["amount"])
        if coding.get("gl_account"):
            ET.SubElement(inv_elem, "glaccount").text = coding["gl_account"]
        if coding.get("job_number"):
            ET.SubElement(inv_elem, "jobnum").text = coding["job_number"]
        if coding.get("phase_number"):
            ET.SubElement(inv_elem, "phasenum").text = coding["phase_number"]
        if coding.get("cost_number"):
            ET.SubElement(inv_elem, "catnum").text = coding["cost_number"]

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)

    return str(output_path)
