# Payable Automation

Automates the accounts payable workflow from raw invoice intake through Computerease import.

## Workflow Overview

1. **Intake** - Collect raw PDF/image invoices from a folder
2. **Organize** - Combine and group invoices by job/project manager
3. **Code Application** - Apply job cost code dropdowns to PDFs
4. **Distribution** - Send grouped invoices to PMs for coding
5. **Collection** - Receive coded invoices back from PMs
6. **Export** - Convert coded invoices to XML/CSV for Computerease import

## Data Fields

Each invoice entry supports:
- Vendor name (mapped to CE vendor code)
- PO / subcontract number
- Description
- Amount
- Date
- GL account
- Job cost code
- Cost type
- Optional: second description, second date, qty, hrs

## Samples Folder

Place your reference files in `samples/` - see `samples/SAMPLE_FILES_GUIDE.txt` for details.