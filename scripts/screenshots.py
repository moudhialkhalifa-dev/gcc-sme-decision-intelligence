"""Capture a screenshot of every page for the ready case-study dataset. Usage:
    python scripts/screenshots.py [base_url] [out_dir]
Fails (non-zero exit) if a page shows an error state or logs a console error."""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
out = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots"
datasets = json.load(urllib.request.urlopen(f"{base}/api/datasets"))
ds = next(d for d in datasets if d["status"] == "ready" and d["is_demo"])
problems = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.goto(base)
    page.evaluate(f"localStorage.setItem('datasetId', '{ds['id']}')")
    for name in ["data", "overview", "forecast", "customers", "anomalies", "diagnostics", "recommendations"]:
        page.goto(f"{base}/#{name}")
        page.reload()
        page.wait_for_selector("h1")
        page.wait_for_function("!document.querySelector('.spinner')", timeout=30000)
        page.wait_for_timeout(700)
        if page.locator(".state.error").count():
            problems.append(f"{name}: error state shown: {page.locator('.state.error').inner_text()}")
        page.screenshot(path=f"{out}/{name}.png", full_page=True)
        print("captured", name, page.locator("h1").inner_text())
    browser.close()
if problems:
    print("\n".join(problems))
    sys.exit(1)
