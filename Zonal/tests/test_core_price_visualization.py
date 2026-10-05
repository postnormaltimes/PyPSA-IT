"""Targeted checks of the reused VIS-X1 price renderers; no model fixtures."""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from mem_model.visualization.vis_x1 import CONFIG, _toolkit


def _prices_module():
    return _toolkit(yaml.safe_load(CONFIG.read_text(encoding='utf-8')))[5]


def test_duration_keeps_timestamps_negative_extreme_prices_and_unequal_durations():
    prices = _prices_module()
    index = pd.date_range('2019-01-01', periods=4, freq='h')
    raw = pd.DataFrame({'NORD': [-25., 0., 120., 10000.]}, index=index)
    duration = pd.Series([.5, 1., 1.5, 2.], index=index)
    table = prices.price_duration_table(raw, weights=duration)
    assert table.snapshot.is_unique and set(table.snapshot) == set(index)
    assert table.price.tolist() == [10000., 120., 0., -25.]
    assert table.weight.sum() == duration.sum() == table.x_end.iloc[-1]
    np.testing.assert_array_equal(table.weight, duration.loc[table.snapshot])
    fig = prices.plot_price_duration(raw, weights=duration)
    np.testing.assert_array_equal(fig.axes[0].lines[0].get_ydata()[:-1], table.price)
    assert fig.axes[0].get_ylim()[0] < -25. and fig.axes[0].get_ylim()[1] > 10000.
    plt.close(fig)


def test_heatmap_preserves_calendar_cells_and_unclipped_negative_extreme_prices():
    prices = _prices_module()
    index = pd.date_range('2019-01-01', periods=48, freq='h')
    raw = pd.DataFrame({'NORD': np.arange(48.)}, index=index)
    raw.iloc[0, 0] = -25.; raw.iloc[-1, 0] = 10000.
    grid = prices.price_day_hour_table(raw, 'NORD')
    np.testing.assert_array_equal(grid.to_numpy().T.ravel(), raw.NORD)
    fig = prices.plot_price_heatmap(raw, 'NORD')
    image = fig.axes[0].images[0]
    np.testing.assert_array_equal(image.get_array(), grid)
    assert image.get_clim() == (-25., 10000.)
    assert image.get_interpolation() == 'none'
    plt.close(fig)
