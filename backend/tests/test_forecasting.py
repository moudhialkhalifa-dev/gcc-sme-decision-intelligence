import numpy as np
import pandas as pd
import pytest

from app.core.timeseries import build_daily, model_series
from app.ml.forecasting import (FEATURES, ForecastContext, backtest, forecast_metrics, plan_splits, run_forecasting,
                                seasonal_naive)


@pytest.fixture(scope="module")
def ctx(synthetic_clean):
    daily = build_daily(synthetic_clean)
    return ForecastContext(model_series(daily), daily["status"])


def test_metrics_definitions_and_zero_denominator():
    m = forecast_metrics([10, 20, 0], [12, 16, 0])
    assert m["mae"] == pytest.approx(2.0) and m["rmse"] == pytest.approx(np.sqrt(20 / 3)) and m["wmape"] == pytest.approx(6 / 30)
    assert forecast_metrics([0, 0], [1, 2])["wmape"] is None
    assert forecast_metrics([np.nan, 5], [1, 5])["n"] == 1


def test_features_never_use_data_after_the_origin(ctx):
    origin = ctx.n - 60
    t = np.repeat(np.arange(60, origin + 1), 30)
    h = np.tile(np.arange(1, 31), origin + 1 - 60)
    before = ctx.pairs(t, h)[FEATURES + ["level"]]
    tampered = ctx.series.copy()
    tampered.iloc[origin + 1:] = tampered.iloc[origin + 1:] * 50 + 999
    after = ForecastContext(tampered, ctx.status).pairs(t, h)[FEATURES + ["level"]]
    pd.testing.assert_frame_equal(before, after)


def test_training_rows_only_have_targets_up_to_the_origin(ctx):
    origin = ctx.n - 60
    train = ctx.training_pairs(origin, 30)
    assert ((train["origin"] + train["h"]) <= origin).all() and len(train) > 500
    assert train["target"].notna().all()


def test_split_plan_is_chronological_and_test_is_untouched():
    plan = plan_splits(738)
    H = max(plan.horizons)
    assert plan.horizons == [7, 30, 90] and plan.test_start == 738 - 90
    assert max(plan.val_origins) + H < plan.test_start          # every validation target precedes the test window
    assert all(o >= plan.test_start - 1 for v in plan.test_origins.values() for o in v)
    short = plan_splits(150)
    assert short.horizons == [7] and 30 in short.disabled and 90 in short.disabled
    assert plan_splits(40).horizons == []


def test_backtest_uses_only_past_data(ctx):
    origin = ctx.n - 40
    a = backtest(ctx, "seasonal_naive", None, [origin], 14)
    tampered = ctx.series.copy()
    tampered.iloc[origin + 1:] = 1e9
    b = backtest(ForecastContext(tampered, ctx.status), "seasonal_naive", None, [origin], 14)
    assert np.allclose(a["yhat"], b["yhat"])


def test_closed_weekday_is_forecast_as_zero(ctx):
    pred, _ = seasonal_naive(ctx, ctx.n - 1, 14)
    dates = ctx.future_calendar(ctx.n - 1, 14)
    assert (pred[dates.dayofweek == 4] == 0).all() and (pred[dates.dayofweek != 4] > 0).all()


def test_full_procedure_selects_on_validation_and_reports_unsupported_horizons(ctx):
    res = run_forecasting(ctx.series, ctx.status)
    assert res["horizons"]["90"]["enabled"] is False and "days of history" in res["horizons"]["90"]["reason"]
    e = res["horizons"]["7"]
    val = {c["model"]: c["validation"]["wmape"] for c in e["comparison"]}
    assert e["selected_model"] == min(val, key=val.get)
    assert len(e["forecast"]) == 7 and all(p["yhat"] >= 0 for p in e["forecast"])
    assert pd.Timestamp(res["plan"]["validation_origins"][-1]) < pd.Timestamp(res["plan"]["test_start"])
