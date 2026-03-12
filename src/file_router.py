"""Route distributed PDFs to PM OneDrive/SharePoint sync folders."""

import shutil
from pathlib import Path

from .config_loader import load_pm_assignments


def copy_to_onedrive(pm_name, source_pdf_path):
    """Copy a PM's invoice PDF to their OneDrive sync folder.

    Returns dict with status and destination path.
    """
    config = load_pm_assignments()

    # Find PM config by name
    pm_info = None
    for key, info in config["project_managers"].items():
        if info["name"] == pm_name:
            pm_info = info
            break

    if not pm_info:
        return {"copied": False, "reason": f"PM '{pm_name}' not found in config"}

    onedrive_folder = pm_info.get("onedrive_folder", "")
    if not onedrive_folder:
        return {"copied": False, "reason": f"No onedrive_folder configured for {pm_name}"}

    dest_dir = Path(onedrive_folder)
    if not dest_dir.exists():
        return {"copied": False, "reason": f"Folder does not exist: {onedrive_folder}"}

    source = Path(source_pdf_path)
    dest = dest_dir / source.name

    try:
        shutil.copy2(str(source), str(dest))
        return {"copied": True, "destination": str(dest)}
    except Exception as e:
        return {"copied": False, "reason": str(e)}


def route_all_pm_files(distribution_results):
    """Copy all distributed PM PDFs to their OneDrive folders.

    Args:
        distribution_results: dict from group_and_distribute() with pm_files mapping.

    Returns:
        dict: pm_name -> copy result
    """
    results = {}
    for pm_name, source_path in distribution_results.get("pm_files", {}).items():
        results[pm_name] = copy_to_onedrive(pm_name, source_path)
    return results
