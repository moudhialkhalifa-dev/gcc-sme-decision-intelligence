import io

import pytest
from fastapi.testclient import TestClient

from conftest import MAPPING, make_synthetic


@pytest.fixture(scope="module")
def client():
    from app.api.main import app
    with TestClient(app) as c:
        yield c


def upload(client, df, name="shop.csv"):
    buf = io.BytesIO(df.to_csv(index=False).encode())
    return client.post("/api/datasets", files={"file": (name, buf, "text/csv")})


@pytest.fixture(scope="module")
def ready(client, synthetic_raw):
    r = upload(client, synthetic_raw)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_mapping"]["quantity"] == "Qty" and len(body["preview"]) == 8
    ds = body["dataset"]["id"]
    r = client.post(f"/api/datasets/{ds}/process", json={"mapping": MAPPING, "currency": "bhd", "timezone": "Asia/Bahrain"})
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/datasets/{ds}").json()
    assert detail["status"] == "ready", detail["job"]
    return ds, detail


def test_upload_to_dashboard_journey(client, ready, synthetic_raw):
    ds, detail = ready
    assert detail["currency"] == "BHD" and detail["quality"]["rows_raw"] == len(synthetic_raw)
    assert detail["quality"]["calendar"]["closed_weekdays"] == ["Fri"]
    caps = {c["key"]: c for c in detail["capabilities"]}
    assert caps["customer_rfm"]["enabled"] and caps["forecast_7"]["enabled"] is not None and not caps["forecast_90"]["enabled"]
    for page in ("overview", "kpis", "timeseries?freq=W", "forecast", "customers", "anomalies", "diagnostics", "recommendations"):
        r = client.get(f"/api/datasets/{ds}/{page}")
        assert r.status_code == 200, (page, r.text)
        assert "NaN" not in r.text
    ov = client.get(f"/api/datasets/{ds}/overview").json()
    k = client.get(f"/api/datasets/{ds}/kpis?days=28").json()
    assert ov["comparison"]["current"]["net_revenue"] == pytest.approx(k["current"]["net_revenue"])   # one definition everywhere
    d = client.get(f"/api/datasets/{ds}/diagnostics?days=28").json()
    assert d["comparison"]["net_revenue"] == pytest.approx(k["current"]["net_revenue"])
    assert abs(d["two_factor"]["reconciliation_error"]) < 1e-6
    assert set(d["dimensions"]) >= {"product_id", "category", "location", "customer_segment", "weekday"}


def test_customer_ids_are_never_returned(client, ready):
    ds, _ = ready
    text = client.get(f"/api/datasets/{ds}/customers").text + client.get(f"/api/datasets/{ds}/diagnostics").text
    assert "M0001" not in text and "M0150" not in text


def test_unsupported_modules_explain_themselves(client):
    df = make_synthetic(days=60, with_customers=False)
    ds = upload(client, df, "nocust.csv").json()["dataset"]["id"]
    mapping = {k: v for k, v in MAPPING.items() if k != "customer_id"}
    assert client.post(f"/api/datasets/{ds}/process", json={"mapping": mapping, "currency": "SAR"}).status_code == 200
    r = client.get(f"/api/datasets/{ds}/customers")
    assert r.status_code == 422 and "customer ID" in r.json()["detail"]["reason"]
    r = client.get(f"/api/datasets/{ds}/anomalies")
    assert r.status_code == 422 and "70 days" in r.json()["detail"]["reason"]
    fc = client.get(f"/api/datasets/{ds}/forecast").json()
    assert all(not h["enabled"] and h["reason"] for h in fc["horizons"].values())
    assert client.get(f"/api/datasets/{ds}/overview").json()["forecast"] is None


def test_bad_inputs_are_rejected_with_clear_messages(client, synthetic_raw, ready):
    ds, _ = ready
    assert client.post("/api/datasets", files={"file": ("x.exe", b"abc", "application/octet-stream")}).status_code == 400
    assert client.post("/api/datasets", files={"file": ("big.csv", b"a,b\n" + b"1,2\n" * 2_000_000, "text/csv")}).status_code == 413
    new = upload(client, synthetic_raw.head(50)).json()["dataset"]["id"]
    r = client.post(f"/api/datasets/{new}/process", json={"mapping": {"timestamp": "Order Date"}, "currency": "BHD"})
    assert r.status_code == 422 and "amount" in " ".join(r.json()["detail"]["errors"])
    r = client.post(f"/api/datasets/{new}/process", json={"mapping": {**{k: v for k, v in MAPPING.items() if k != "category"}, "timestamp": "Category"}, "currency": "BHD"})
    assert r.status_code == 200
    assert client.get(f"/api/datasets/{new}").json()["status"] == "failed"      # every date unparseable
    assert "No valid sales rows" in client.get(f"/api/datasets/{new}").json()["job"]["error"]
    assert client.get(f"/api/datasets/{new}/overview").status_code == 409
    assert client.get(f"/api/datasets/{ds}/kpis?start=2030-01-01&end=2030-01-31").status_code == 422
    assert client.get("/api/datasets/doesnotexist").status_code == 404
    assert client.delete(f"/api/datasets/{new}").status_code == 200 and client.get(f"/api/datasets/{new}").status_code == 404


def test_api_key_is_enforced_when_configured(client, monkeypatch):
    from app.api.config import settings
    monkeypatch.setattr(settings, "api_key", "secret")
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/datasets", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/api/health").status_code == 200
