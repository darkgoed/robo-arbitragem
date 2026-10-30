import pytest
from robo.engine import funding_pnl, forecast_funding


def test_short_receives_positive_and_pays_negative_funding_at_discrete_events():
    assert funding_pnl(1000, 0.001, "short") == 1
    assert funding_pnl(1000, 0.001, "long") == -1
    assert funding_pnl(1000, -0.001, "short") == -1
    # Three settlements in 24h, not fractionally interpolated. 50% credit haircut.
    assert forecast_funding(1000, 0.001, 8, 4 * 3600000, 0, 24) == 1.5
    assert forecast_funding(1000, -0.001, 8, 4 * 3600000, 0, 24) == -3
    assert forecast_funding(1000, 0.001, 8, 4 * 3600000, 0, 3) == 0
    with pytest.raises(ValueError):
        forecast_funding(1000, 0.001, 0, 0, 0, 24)
