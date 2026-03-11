"""Invoice intake: split multi-page PDFs into individual pages for review."""

import os
import json
import subprocess
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .config_loader import BASE_DIR, DATA_DIR, load_pm_assignments, get_job_for_keyword


INBOX_DIR = DATA_DIR / "inbox"


def extract_text_from_page(pdf_path, page_num):
    """Extract text from a single PDF page using pdftotext."""
    try:
        result = subprocess.run(
            ["pdftotext", "-f", str(page_num + 1), "-l", str(page_num + 1),
             str(pdf_path), "-"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return ""


def render_page_to_png(pdf_path, page_num, output_path, dpi=150):
    """Render a single PDF page to PNG."""
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi),
         "-f", str(page_num + 1), "-l", str(page_num + 1),
         str(pdf_path), str(output_path).replace(".png", "")],
        capture_output=True, timeout=30
    )
    # pdftoppm appends page number to filename
    expected = str(output_path).replace(".png", f"-{page_num + 1:02d}.png")
    if os.path.exists(expected):
        os.rename(expected, str(output_path))
        return True
    # Try single-digit format
    expected2 = str(output_path).replace(".png", f"-{page_num + 1}.png")
    if os.path.exists(expected2):
        os.rename(expected2, str(output_path))
        return True
    return False


def split_pdf_to_pages(pdf_path):
    """Split a multi-page PDF into individual page PDFs. Returns list of page info dicts."""
    pdf_path = Path(pdf_path)
    reader = PdfReader(pdf_path)
    num_pages = len(reader.pages)

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = INBOX_DIR / batch_id
    pages_dir = batch_dir / "pages"
    thumbs_dir = batch_dir / "thumbnails"
    pages_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    config = load_pm_assignments()
    pages = []

    for i in range(num_pages):
        # Extract single page PDF
        writer = PdfWriter()
        writer.add_page(reader.pages[i])
        page_pdf = pages_dir / f"page_{i + 1:03d}.pdf"
        with open(page_pdf, "wb") as f:
            writer.write(f)

        # Extract text for keyword matching
        text = extract_text_from_page(pdf_path, i)

        # Try to identify job from text
        job_number, pm_name = get_job_for_keyword(text, config)

        # Render thumbnail
        thumb_path = thumbs_dir / f"page_{i + 1:03d}.png"
        render_page_to_png(pdf_path, i, thumb_path, dpi=150)

        pages.append({
            "page_num": i + 1,
            "pdf_file": str(page_pdf.relative_to(batch_dir)),
            "thumbnail": str(thumb_path.relative_to(batch_dir)),
            "text_extract": text[:500],
            "suggested_job": job_number,
            "suggested_pm": pm_name,
            "status": "pending",          # pending | grouped | overhead | skipped
            "group_with_prev": False,     # True if this is a continuation of previous invoice
            "assigned_pm": None,
            "assigned_job": None,
            "overhead_coding": None,      # filled in if user codes as overhead
        })

    # Save batch manifest
    manifest = {
        "batch_id": batch_id,
        "source_file": pdf_path.name,
        "created": datetime.now().isoformat(),
        "total_pages": num_pages,
        "pages": pages,
    }
    manifest_path = batch_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return batch_id, manifest


def load_batch(batch_id):
    """Load a batch manifest."""
    manifest_path = INBOX_DIR / batch_id / "manifest.json"
    with open(manifest_path) as f:
        return json.load(f)


def save_batch(batch_id, manifest):
    """Save updated batch manifest."""
    manifest_path = INBOX_DIR / batch_id / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def list_batches():
    """List all batch IDs in the inbox."""
    if not INBOX_DIR.exists():
        return []
    batches = []
    for d in sorted(INBOX_DIR.iterdir()):
        if d.is_dir() and (d / "manifest.json").exists():
            manifest = load_batch(d.name)
            batches.append({
                "batch_id": d.name,
                "source_file": manifest.get("source_file", ""),
                "total_pages": manifest.get("total_pages", 0),
                "created": manifest.get("created", ""),
            })
    return batches
