#!/usr/bin/env python3
"""Run the Payable Automation web application."""

from src.app import create_app

app = create_app()

if __name__ == "__main__":
    print("Starting Payable Automation...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, host="0.0.0.0", port=5000)
