"""Browser test of the whole journey with a SYNTHETIC file: upload -> map columns -> process -> every page.
Usage: python scripts/e2e_upload.py [base_url]. Exits non-zero on any failure. The dataset is deleted afterwards."""
import os
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "tests"))
from conftest import make_synthetic  # noqa: E402  (synthetic test data generator)

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
out = os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots")
path = os.path.join(tempfile.mkdtemp(), "synthetic_shop.csv")
make_synthetic().to_csv(path, index=False)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(base)
    page.evaluate("localStorage.removeItem('datasetId')")
    page.reload()
    page.set_input_files("input[type=file]", path)
    expect(page.get_by_role("heading", name="Map the columns")).to_be_visible(timeout=20000)
    assert page.locator("#m-timestamp").input_value() == "Order Date"      # suggestion from column names
    assert page.locator("#m-quantity").input_value() == "Qty"
    assert page.locator("#m-customer_id").input_value() == "Member"
    process = page.get_by_role("button", name="Process data")
    expect(process).to_be_disabled()                                         # currency must be entered, never assumed
    page.get_by_placeholder("e.g. BHD").fill("BHD")
    page.screenshot(path=f"{out}/upload_mapping.png", full_page=True)
    process.click()
    expect(page.get_by_role("heading", name="What this dataset supports")).to_be_visible(timeout=180000)
    expect(page.get_by_text("Regular closed weekdays (Fri)")).to_be_visible()
    expect(page.get_by_text("Needs about", exact=False).first).to_be_visible()  # 90-day forecast disabled with a reason
    for name, text in [("overview", "Net revenue"), ("forecast", "Model comparison"), ("customers", "Share of revenue"),
                       ("anomalies", "most severe first"), ("diagnostics", "Volume or basket size?"),
                       ("recommendations", "fixed rule")]:
        page.goto(f"{base}/#{name}")
        expect(page.get_by_text(text, exact=False).first).to_be_visible(timeout=30000)
        assert page.locator(".state.error").count() == 0, name
        assert "BHD" in page.locator("main").inner_text() or name in ("customers", "recommendations"), name
    page.goto(f"{base}/#diagnostics")
    page.get_by_role("button", name="Last 7 days vs previous 7").click()
    expect(page.get_by_text("7 days").first).to_be_visible()
    page.goto(f"{base}/#data")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Delete this dataset").click()
    expect(page.get_by_role("heading", name="Upload sales data")).to_be_visible(timeout=20000)
    browser.close()
if errors:
    print("browser errors:", errors)
    sys.exit(1)
print("e2e upload journey passed")
