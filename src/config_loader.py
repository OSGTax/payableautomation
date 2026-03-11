"""Load and manage configuration files."""

import csv
import os
from pathlib import Path

import openpyxl
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"


def load_pm_assignments():
    """Load PM-to-job assignments from YAML config."""
    path = CONFIG_DIR / "pm_assignments.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def get_pm_for_job(job_number, config=None):
    """Return PM name for a given job number prefix (e.g. '25-009')."""
    if config is None:
        config = load_pm_assignments()
    for pm_key, pm_info in config["project_managers"].items():
        if job_number in pm_info["jobs"]:
            return pm_info["name"]
    return None


def get_job_for_keyword(text, config=None):
    """Search text for job keywords, return (job_number, pm_name) or (None, None)."""
    if config is None:
        config = load_pm_assignments()
    text_lower = text.lower()
    for keyword, job_number in config.get("job_keywords", {}).items():
        if keyword.lower() in text_lower:
            pm_name = get_pm_for_job(job_number, config)
            return job_number, pm_name
    return None, None


def load_job_codes(csv_path=None):
    """Load job codes from CSV. Returns dict: job_number -> {phases: {phase -> [costs]}}."""
    if csv_path is None:
        csv_path = BASE_DIR / "samples" / "job-codes" / "Job Cost Codes.csv"
    jobs = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            job_full = row["Job"].strip()
            phase = row["Phase"].strip()
            cost = row["Cost"].strip()
            # Extract job number (e.g. "25-002" from "25-002.STEP BY STEP - OGDENSBURG")
            job_number = job_full.split(".")[0] if "." in job_full else job_full
            if job_full not in jobs:
                jobs[job_full] = {"number": job_number, "phases": {}}
            if phase not in jobs[job_full]["phases"]:
                jobs[job_full]["phases"][phase] = []
            jobs[job_full]["phases"][phase].append(cost)
    return jobs


def load_vendor_list(xlsx_path=None):
    """Load vendor list from Excel. Returns list of dicts with keys: number, name, account."""
    if xlsx_path is None:
        xlsx_path = BASE_DIR / "samples" / "vendor-list" / "Vendorlist.xlsx"
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    vendors = []
    header = None
    for row in ws.iter_rows(values_only=True):
        if header is None:
            header = row
            continue
        vendors.append({
            "number": str(row[0]).strip() if row[0] else "",
            "name": str(row[1]).strip() if row[1] else "",
            "account": str(row[2]).strip() if row[2] else "",
            "account_number": str(row[3]).strip() if row[3] else "",
        })
    wb.close()
    return vendors


def find_vendor_by_name(search_text, vendors=None):
    """Fuzzy match vendor name in text. Returns best match vendor dict or None."""
    if vendors is None:
        vendors = load_vendor_list()
    search_lower = search_text.lower()
    best_match = None
    best_len = 0
    for vendor in vendors:
        name_lower = vendor["name"].lower()
        if name_lower in search_lower and len(name_lower) > best_len:
            best_match = vendor
            best_len = len(name_lower)
    return best_match
