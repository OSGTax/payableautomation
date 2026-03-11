"""Flask web application for the payable automation workflow."""

import json
import os
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
from .export import export_coded_pdfs_to_xml

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.secret_key = os.urandom(24)


@app.route("/")
def index():
    """Dashboard showing workflow status."""
    batches = list_batches()
    pm_inbox = scan_pm_inbox() if (DATA_DIR / "pm-inbox").exists() else []
    return render_template("index.html", batches=batches, pm_inbox=pm_inbox)


@app.route("/upload", methods=["POST"])
def upload():
    """Upload a PDF for processing."""
    if "file" not in request.files:
        flash("No file selected.")
        return redirect(url_for("index"))

    file = request.files["file"]
    if not file.filename:
        flash("No file selected.")
        return redirect(url_for("index"))

    # Save uploaded file temporarily
    upload_dir = DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / file.filename
    file.save(str(filepath))

    # Process: split into pages
    batch_id, manifest = split_pdf_to_pages(filepath)

    # Clean up uploaded file
    os.remove(filepath)

    flash(f"Uploaded {file.filename}: {manifest['total_pages']} pages ready for review.")
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
    results = group_and_distribute(batch_id)
    return jsonify({"status": "ok", "results": results})


@app.route("/collect")
def collect_view():
    """View coded PDFs returned from PMs."""
    results = scan_pm_inbox()
    return render_template("collect.html", results=results)


@app.route("/export", methods=["POST"])
def export_view():
    """Export coded invoices to CE XML."""
    files = request.form.getlist("files")
    if not files:
        # Export all from pm-inbox
        output_path = export_coded_pdfs_to_xml()
    else:
        output_path = export_coded_pdfs_to_xml(coded_files=files)

    return send_file(output_path, as_attachment=True, download_name="ap_import.xml")


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
