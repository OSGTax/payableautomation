"""Flask web application for the payable automation workflow."""

import json
import os
import secrets
import shutil
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_file, flash,
)

from .config_loader import (
    BASE_DIR, DATA_DIR, load_pm_assignments, load_job_codes,
    load_vendor_list, get_pm_for_job,
)
from .intake import split_pdf_to_pages, load_batch, save_batch, list_batches, INBOX_DIR
from .prepare import group_and_distribute
from .collect import scan_pm_inbox, read_coded_pdf
from .export import export_coded_pdfs_to_xml, export_browser_codings_to_xml
from .file_router import route_all_pm_files
from .notify import notify_all_pms

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.secret_key = os.urandom(24)

# Store for PM coding assignments: {token -> assignment_data}
ASSIGNMENTS_DIR = DATA_DIR / "assignments"


@app.route("/")
def index():
    """Dashboard showing workflow status."""
    batches = list_batches()
    pm_inbox = scan_pm_inbox() if (DATA_DIR / "pm-inbox").exists() else []
    return render_template("index.html", batches=batches, pm_inbox=pm_inbox)


@app.route("/upload", methods=["POST"])
def upload():
    """Upload one or more PDFs for processing. Multiple files are merged into one batch."""
    from pypdf import PdfReader, PdfWriter

    files = request.files.getlist("file")
    files = [f for f in files if f.filename and f.filename.lower().endswith('.pdf')]

    if not files:
        flash("No PDF files selected.")
        return redirect(url_for("index"))

    upload_dir = DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    if len(files) == 1:
        # Single file — process directly
        filepath = upload_dir / files[0].filename
        files[0].save(str(filepath))
        batch_id, manifest = split_pdf_to_pages(filepath)
        os.remove(filepath)
        flash(f"Uploaded {files[0].filename}: {manifest['total_pages']} pages ready for review.")
    else:
        # Multiple files — merge into one PDF then process
        writer = PdfWriter()
        filenames = []
        saved_paths = []

        for f in files:
            filepath = upload_dir / f.filename
            f.save(str(filepath))
            saved_paths.append(filepath)
            filenames.append(f.filename)
            reader = PdfReader(filepath)
            for page in reader.pages:
                writer.add_page(page)

        merged_path = upload_dir / "merged_upload.pdf"
        with open(merged_path, "wb") as mf:
            writer.write(mf)

        batch_id, manifest = split_pdf_to_pages(merged_path)

        # Clean up
        os.remove(merged_path)
        for p in saved_paths:
            os.remove(p)

        total = manifest['total_pages']
        flash(f"Uploaded {len(files)} files ({total} pages total) ready for review.")

    return redirect(url_for("prescreen", batch_id=batch_id))


@app.route("/prescreen/<batch_id>")
def prescreen(batch_id):
    """Pre-screening view: review pages, mark overhead, group multi-page invoices."""
    manifest = load_batch(batch_id)
    config = load_pm_assignments()
    job_codes = load_job_codes()
    vendors = load_vendor_list()

    # Build job list for dropdowns
    job_list = sorted(job_codes.keys())

    # Build PM list
    pm_list = [
        {"key": k, "name": v["name"]}
        for k, v in config["project_managers"].items()
    ]

    return render_template(
        "prescreen.html",
        batch_id=batch_id,
        manifest=manifest,
        job_list=job_list,
        pm_list=pm_list,
        vendors=vendors[:50],  # Top vendors for quick selection
        config=config,
    )


@app.route("/prescreen/<batch_id>/thumbnail/<int:page_num>")
def page_thumbnail(batch_id, page_num):
    """Serve a page thumbnail image."""
    manifest = load_batch(batch_id)
    for page in manifest["pages"]:
        if page["page_num"] == page_num:
            thumb_path = INBOX_DIR / batch_id / page["thumbnail"]
            if thumb_path.exists():
                return send_file(str(thumb_path), mimetype="image/png")
    return "Not found", 404


@app.route("/prescreen/<batch_id>/pdf/<int:page_num>")
def page_pdf(batch_id, page_num):
    """Serve a single page PDF."""
    manifest = load_batch(batch_id)
    for page in manifest["pages"]:
        if page["page_num"] == page_num:
            pdf_path = INBOX_DIR / batch_id / page["pdf_file"]
            if pdf_path.exists():
                return send_file(str(pdf_path), mimetype="application/pdf")
    return "Not found", 404


@app.route("/api/prescreen/<batch_id>/save", methods=["POST"])
def save_prescreen(batch_id):
    """Save pre-screening decisions for all pages."""
    data = request.get_json()
    manifest = load_batch(batch_id)

    for page_update in data.get("pages", []):
        page_num = page_update["page_num"]
        for page in manifest["pages"]:
            if page["page_num"] == page_num:
                page["status"] = page_update.get("status", page["status"])
                page["group_with_prev"] = page_update.get("group_with_prev", False)
                page["assigned_pm"] = page_update.get("assigned_pm")
                page["assigned_job"] = page_update.get("assigned_job")
                if page_update.get("overhead_coding"):
                    page["overhead_coding"] = page_update["overhead_coding"]
                break

    save_batch(batch_id, manifest)
    return jsonify({"status": "ok"})


@app.route("/api/prescreen/<batch_id>/distribute", methods=["POST"])
def distribute(batch_id):
    """Finalize pre-screening and distribute to PMs."""
    import traceback

    # Check if already distributed (prevent duplicates)
    manifest = load_batch(batch_id)
    if manifest.get("distributed"):
        return jsonify({"status": "ok", "results": manifest.get("distribution_results", {}),
                        "message": "Already distributed."}), 200

    try:
        results = group_and_distribute(batch_id)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

    # Create browser coding assignments for each PM
    ASSIGNMENTS_DIR.mkdir(parents=True, exist_ok=True)
    config = load_pm_assignments()
    pm_links = {}

    for pm_name, file_path in results.get("pm_files", {}).items():
        token = secrets.token_urlsafe(16)
        assignment = {
            "token": token,
            "pm_name": pm_name,
            "batch_id": batch_id,
            "pdf_file": file_path,
            "status": "pending",  # pending | completed
            "codings": {},  # page_num -> [coding rows]
        }
        assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
        with open(assignment_path, "w") as f:
            json.dump(assignment, f, indent=2)
        pm_links[pm_name] = token

    results["pm_links"] = pm_links

    # Copy PDFs to OneDrive sync folders (optional, if configured)
    route_results = route_all_pm_files(results)
    results["file_routing"] = route_results

    # Send email notifications with browser coding links
    email_results = notify_all_pms(results)
    results["email_notifications"] = email_results

    flash(f"Distributed to {len(pm_links)} PM(s): {', '.join(pm_links.keys())}. Coding links are on the Collect & Export page.")

    return jsonify({"status": "ok", "results": results})


# --- PM Browser Coding Routes ---

@app.route("/code/<token>")
def pm_coding_page(token):
    """Browser-based invoice coding page for PMs."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return "Invalid or expired link.", 404

    with open(assignment_path) as f:
        assignment = json.load(f)

    config = load_pm_assignments()
    job_codes = load_job_codes()

    # Build cascading dropdown data
    job_list = sorted(job_codes.keys())
    phase_map = {}
    cost_map = {}
    for job_full, job_data in job_codes.items():
        phases = sorted(job_data["phases"].keys())
        phase_map[job_full] = phases
        for phase, costs in job_data["phases"].items():
            cost_map[f"{job_full}||{phase}"] = sorted(costs)

    # Count pages in the PDF
    from pypdf import PdfReader
    pdf_path = Path(assignment["pdf_file"])
    num_pages = len(PdfReader(pdf_path).pages) if pdf_path.exists() else 0

    # Load extracted amounts and page suggestions from batch manifest
    page_amounts = {}
    page_suggestions = {}
    try:
        batch_manifest = load_batch(assignment["batch_id"])
        for page in batch_manifest["pages"]:
            if page.get("extracted_amount"):
                page_amounts[page["page_num"]] = page["extracted_amount"]
            if page.get("suggested_job"):
                page_suggestions[page["page_num"]] = page["suggested_job"]
    except Exception:
        pass  # Amounts/suggestions are optional

    # Load PM's assigned jobs from config
    pm_jobs = []
    pm_name = assignment.get("pm_name", "")
    for pm_key, pm_info in config["project_managers"].items():
        if pm_info["name"] == pm_name:
            pm_jobs = pm_info.get("jobs", [])
            break

    # Build pm_job_list: full job names that match PM's assigned job prefixes
    pm_job_list = []
    for job_full in job_list:
        job_prefix = job_full.split(".")[0] if "." in job_full else job_full
        if job_prefix in pm_jobs:
            pm_job_list.append(job_full)

    # Build PM list for redirect dropdown
    pm_list = [
        {"key": k, "name": v["name"]}
        for k, v in config["project_managers"].items()
    ]

    return render_template(
        "pm_coding.html",
        assignment=assignment,
        token=token,
        job_list=job_list,
        pm_job_list=pm_job_list,
        phase_map=phase_map,
        cost_map=cost_map,
        num_pages=num_pages,
        page_amounts=page_amounts,
        page_suggestions=page_suggestions,
        pm_list=pm_list,
    )


@app.route("/code/<token>/page/<int:page_num>")
def pm_page_image(token, page_num):
    """Serve a rendered page image for PM coding view."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return "Not found", 404

    with open(assignment_path) as f:
        assignment = json.load(f)

    # Render the specific page from the PM's PDF
    pdf_path = Path(assignment["pdf_file"])
    if not pdf_path.exists():
        return "PDF not found", 404

    # Render on demand, cache in a temp location
    cache_dir = DATA_DIR / "page-cache" / token
    cache_dir.mkdir(parents=True, exist_ok=True)
    img_path = cache_dir / f"page_{page_num:03d}.png"

    if not img_path.exists():
        from .intake import render_page_to_png
        render_page_to_png(pdf_path, page_num - 1, img_path, dpi=200)

    if img_path.exists():
        return send_file(str(img_path), mimetype="image/png")
    return "Could not render page", 500


@app.route("/api/code/<token>/save", methods=["POST"])
def save_pm_coding(token):
    """Save PM's coding selections."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return jsonify({"error": "Invalid token"}), 404

    data = request.get_json()

    with open(assignment_path) as f:
        assignment = json.load(f)

    assignment["codings"] = data.get("codings", {})
    assignment["status"] = "in_progress"

    with open(assignment_path, "w") as f:
        json.dump(assignment, f, indent=2)

    return jsonify({"status": "ok"})


@app.route("/api/code/<token>/submit", methods=["POST"])
def submit_pm_coding(token):
    """PM submits completed coding back to controller."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return jsonify({"error": "Invalid token"}), 404

    data = request.get_json()

    with open(assignment_path) as f:
        assignment = json.load(f)

    assignment["codings"] = data.get("codings", {})
    assignment["flagged_pages"] = data.get("flagged_pages", [])
    assignment["status"] = "completed"

    with open(assignment_path, "w") as f:
        json.dump(assignment, f, indent=2)

    flagged = data.get("flagged_pages", [])
    if flagged:
        msg = f"Submitted. Pages {', '.join(str(p) for p in flagged)} flagged for controller review."
    else:
        msg = "Submitted to controller. Thank you!"

    return jsonify({"status": "ok", "message": msg})


@app.route("/collect")
def collect_view():
    """View coded invoices from PMs (browser-submitted + PDF inbox)."""
    config = load_pm_assignments()
    pm_list = [{"key": k, "name": v["name"]} for k, v in config["project_managers"].items()]

    # Browser-submitted assignments
    assignments = []
    if ASSIGNMENTS_DIR.exists():
        for f in sorted(ASSIGNMENTS_DIR.glob("*.json")):
            with open(f) as fh:
                a = json.load(fh)
                # Count pages where all rows have job+phase+cost
                coded_count = sum(
                    1 for codings in a.get("codings", {}).values()
                    if codings and all(
                        c.get("job") and c.get("phase") and c.get("cost")
                        for c in codings
                    )
                )
                a["coded_count"] = coded_count
                a["flagged_pages"] = a.get("flagged_pages", [])
                a["return_reason"] = a.get("return_reason", "")
                assignments.append(a)

    # Legacy PDF inbox
    pdf_results = scan_pm_inbox() if (DATA_DIR / "pm-inbox").exists() else []

    return render_template("collect.html", assignments=assignments,
                           pdf_results=pdf_results, pm_list=pm_list)


@app.route("/api/reassign/<token>", methods=["POST"])
def reassign_invoice(token):
    """Reassign an invoice (e.g. unassigned) to a specific PM."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return jsonify({"error": "Invalid token"}), 404

    data = request.get_json()
    new_pm = data.get("pm_name", "")
    if not new_pm:
        return jsonify({"error": "No PM specified"}), 400

    with open(assignment_path) as f:
        assignment = json.load(f)

    old_pm = assignment["pm_name"]
    assignment["pm_name"] = new_pm

    # Move the PDF to the new PM's outbox
    old_pdf = Path(assignment["pdf_file"])
    if old_pdf.exists():
        from .prepare import PM_OUTBOX_DIR
        new_pm_dir = PM_OUTBOX_DIR / new_pm
        new_pm_dir.mkdir(parents=True, exist_ok=True)
        new_pdf = new_pm_dir / old_pdf.name.replace(old_pm, new_pm)
        import shutil
        shutil.move(str(old_pdf), str(new_pdf))
        assignment["pdf_file"] = str(new_pdf)

    with open(assignment_path, "w") as f:
        json.dump(assignment, f, indent=2)

    return jsonify({"status": "ok", "message": f"Reassigned to {new_pm}"})


@app.route("/export", methods=["POST"])
def export_view():
    """Export coded invoices to CE XML."""
    export_type = request.form.get("type", "browser")

    if export_type == "browser":
        # Export browser-submitted codings
        tokens = request.form.getlist("tokens")
        output_path = export_browser_codings_to_xml(tokens)
    else:
        # Legacy: export from coded PDFs
        files = request.form.getlist("files")
        if not files:
            output_path = export_coded_pdfs_to_xml()
        else:
            output_path = export_coded_pdfs_to_xml(coded_files=files)

    return send_file(output_path, as_attachment=True, download_name="ap_import.xml")


@app.route("/api/batch/<batch_id>/delete", methods=["POST"])
def delete_batch(batch_id):
    """Delete a batch and all associated assignments and PM outbox files."""
    from .prepare import PM_OUTBOX_DIR

    # Delete batch directory
    batch_dir = INBOX_DIR / batch_id
    if batch_dir.exists():
        shutil.rmtree(str(batch_dir))

    # Delete assignments associated with this batch
    if ASSIGNMENTS_DIR.exists():
        for f in list(ASSIGNMENTS_DIR.glob("*.json")):
            try:
                with open(f) as fh:
                    a = json.load(fh)
                if a.get("batch_id") == batch_id:
                    os.remove(f)
            except Exception:
                pass

    # Delete PM outbox files for this batch
    if PM_OUTBOX_DIR.exists():
        for pm_dir in PM_OUTBOX_DIR.iterdir():
            if pm_dir.is_dir():
                for pdf_file in pm_dir.glob(f"*{batch_id}*"):
                    os.remove(pdf_file)

    return jsonify({"status": "ok", "message": f"Batch {batch_id} deleted."})


@app.route("/api/assignment/<token>/delete", methods=["POST"])
def delete_assignment(token):
    """Delete a coding assignment."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return jsonify({"error": "Invalid token"}), 404

    os.remove(assignment_path)
    return jsonify({"status": "ok", "message": "Assignment deleted."})


@app.route("/api/code/<token>/return", methods=["POST"])
def return_to_controller(token):
    """PM returns an invoice back to the controller with an optional reason."""
    assignment_path = ASSIGNMENTS_DIR / f"{token}.json"
    if not assignment_path.exists():
        return jsonify({"error": "Invalid token"}), 404

    data = request.get_json() or {}

    with open(assignment_path) as f:
        assignment = json.load(f)

    assignment["status"] = "returned"
    assignment["return_reason"] = data.get("reason", "")
    assignment["codings"] = data.get("codings", assignment.get("codings", {}))

    # If redirecting to another PM
    redirect_pm = data.get("redirect_pm")
    if redirect_pm:
        assignment["pm_name"] = redirect_pm
        assignment["status"] = "pending"
        assignment["redirected_from"] = assignment.get("pm_name", "")

    with open(assignment_path, "w") as f:
        json.dump(assignment, f, indent=2)

    msg = "Returned to controller." if not redirect_pm else f"Redirected to {redirect_pm}."
    return jsonify({"status": "ok", "message": msg})


@app.route("/api/job-codes")
def api_job_codes():
    """Return job codes as JSON for dynamic dropdowns."""
    job_codes = load_job_codes()
    return jsonify(job_codes)


@app.route("/api/vendors")
def api_vendors():
    """Return vendor list as JSON."""
    vendors = load_vendor_list()
    return jsonify(vendors)


def create_app():
    """Application factory."""
    # Ensure data directories exist
    for subdir in ["inbox", "uploads", "prescreened", "pm-outbox", "pm-inbox", "exported"]:
        (DATA_DIR / subdir).mkdir(parents=True, exist_ok=True)
    return app
