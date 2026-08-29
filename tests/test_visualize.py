import csv

import pytest

from batchmortal import visualize
from batchmortal.visualize import (
    prepare_dashboard_data,
    rolling_average,
)


def make_result(
    index: int,
    *,
    rating: float,
    ai_rate: str = "",
    ai_numerator: str = "",
    ai_denominator: str = "",
) -> dict:
    return {
        "nickname": "测试玩家",
        "source": "majsoul",
        "mode": "16",
        "uuid": f"game-{index}",
        "startTime": f"2026-07-{index:02d} 12:00:00",
        "resultUrl": f"https://example.com/report/{index}",
        "modelTag": "4.1b",
        "rating": str(rating),
        "aiConsistencyRate": ai_rate,
        "aiConsistencyNumerator": ai_numerator,
        "aiConsistencyDenominator": ai_denominator,
    }


def test_rolling_average_requires_a_full_window():
    assert rolling_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]

    with pytest.raises(ValueError, match="positive"):
        rolling_average([1, 2], 0)


def test_utc_timestamps_are_not_reinterpreted_as_local_time():
    assert visualize._parse_time("1970-01-01T00:00:01Z") == 1.0


def test_invalid_start_time_falls_back_to_result_timestamp_for_plot_limit(tmp_path):
    filepath = tmp_path / "results.csv"
    with filepath.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["uuid", "startTime", "timestamp", "rating"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "uuid": "old",
                    "startTime": "2026-01-01 00:00:00",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "rating": "90",
                },
                {
                    "uuid": "file",
                    "startTime": "N/A (#1)",
                    "timestamp": "2026-08-29T00:00:00Z",
                    "rating": "95",
                },
            ]
        )

    records = visualize.read_results(
        "reviewer",
        output_format="csv",
        output_root=str(tmp_path),
    )
    data = prepare_dashboard_data(records, plot_limit=1)

    assert [record["uuid"] for record in records] == ["old", "file"]
    assert data is not None
    assert [point["uuid"] for point in data["points"]] == ["file"]
    assert data["points"][0]["startedAt"] == ""


def test_dashboard_data_keeps_missing_ai_null_and_weights_valid_samples():
    missing_ai = make_result(1, rating=90)
    measured = make_result(
        2,
        rating=92,
        ai_rate="1%",
        ai_numerator="8",
        ai_denominator="10",
    )
    measured.update(
        {
            "badMoveCount5": "1",
            "badMoveCount10": "2",
            "badMoveDenominator": "20",
        }
    )

    data = prepare_dashboard_data([missing_ai, measured])

    assert data is not None
    assert data["points"][0]["aiRate"] is None
    assert data["points"][1]["aiRate"] == pytest.approx(80.0)
    assert data["aiRate"] == pytest.approx(80.0)
    assert data["aiDenominator"] == 10
    assert data["aiWeighted"] is True
    assert data["aiAxisMin"] == 60
    assert data["badRate5"] == pytest.approx(5.0)
    assert data["badRate10"] == pytest.approx(10.0)
    assert data["badRateAxisMax"] == 12
    assert data["badRateAxisInterval"] == 3
    assert data["trendWindow"] is None
    assert data["histogram"] == []


def test_dashboard_data_uses_recent_comparison_and_rolling_trend():
    records = [make_result(index, rating=79 + index) for index in range(1, 21)]

    data = prepare_dashboard_data(records)

    assert data is not None
    assert data["trendWindow"] == 10
    assert data["ratingMean"] == pytest.approx(89.5)
    assert data["comparisonWindow"] == 10
    assert data["recentAverage"] == pytest.approx(94.5)
    assert data["comparisonDelta"] == pytest.approx(10.0)
    assert data["ratingRolling"][8] is None
    assert data["ratingRolling"][9] == pytest.approx(84.5)
    assert data["ratingDenseLower"] == pytest.approx(84.75)
    assert data["ratingDenseUpper"] == pytest.approx(94.25)
    assert "ratingBandLower" not in data
    assert "ratingBandUpper" not in data
    assert len(data["histogram"]) == 10
    assert data["histogram"][0]["lower"] == 80
    assert data["histogram"][-1]["upper"] == 100


def test_rating_batches_are_newest_anchored_in_twenty_game_windows():
    records = [make_result(index, rating=80 + index) for index in range(1, 46)]

    data = prepare_dashboard_data(records)

    assert data is not None
    assert [batch["count"] for batch in data["ratingBatches"]] == [5, 20, 20]
    assert [batch["startLabel"] for batch in data["ratingBatches"]] == [
        "#1",
        "#6",
        "#26",
    ]
    assert [batch["endLabel"] for batch in data["ratingBatches"]] == [
        "#5",
        "#25",
        "#45",
    ]
    assert data["ratingBatches"][-1]["ratingMean"] == pytest.approx(115.5)


def test_bad_rate_axis_uses_padded_readable_quarters():
    assert visualize._rate_axis_scale([1.0, 14.4828]) == (16.0, 4.0)
    assert visualize._rate_axis_scale([0.0]) == (1.0, 0.25)
    assert visualize._rate_axis_scale([]) == (10.0, 2.5)


def test_generate_html_contains_professional_sections_and_escapes_nickname(
    monkeypatch,
    tmp_path,
):
    records = [
        make_result(
            index,
            rating=88 + index,
            ai_numerator=str(80 + index),
            ai_denominator="100",
        )
        for index in range(1, 11)
    ]
    monkeypatch.setattr(
        visualize,
        "read_results",
        lambda nickname, output_format, output_root=None: records,
    )
    output_path = tmp_path / "report.html"

    result = visualize.generate_html("<测试玩家>", str(output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert result == str(output_path)
    assert "&lt;测试玩家&gt;" in rendered
    assert "Rating 推移" in rendered
    assert "AI 一致率推移" in rendered
    assert "Rating 分布" in rendered
    assert "检讨候选" in rendered
    assert "半庄移动平均" in rendered
    assert '"trendWindow":10' in rendered
    assert "recentFocusMark" not in rendered
    assert "recentFocusArea" not in rendered
    assert "ratingContextArea" in rendered
    assert "ratingDensityArea" in rendered
    assert "全样本中间50%" in rendered
    assert "density-key" in rendered
    assert "Rating 中间50%" in rendered
    assert '"ratingDenseLower"' in rendered
    assert '"ratingDenseUpper"' in rendered
    assert "trendReferenceLine" in rendered
    assert "recentMarkArea" not in rendered
    assert "bandRange" not in rendered
    assert "smoothMonotone" in rendered
    assert "rawEndLabel" in rendered
    assert "trendEndLabel" not in rendered
    assert "rollingBandSeries" not in rendered
    assert "confidenceBandSeries" not in rendered
    assert "95% 置信区间" not in rendered
    assert '"ratingConfidenceLower"' not in rendered
    assert '"aiConfidenceLower"' not in rendered
    assert "rawSeriesData" in rendered
    assert "selectedRawSeriesData" in rendered
    assert "rating-selected-raw" in rendered
    assert "rating-bad-rate-10-bars" in rendered
    assert "rating-bad-rate-5-bars" in rendered
    assert "badRateBarData" in rendered
    assert "badRateXAxisConfig" in rendered
    assert '"badRateAxisMax"' in rendered
    assert '"badRateAxisInterval"' in rendered
    assert "Rating、5% 与 10% 恶手率推移" in rendered
    assert "position: 'left'" in rendered
    assert "rating-batch-summary" in rendered
    assert "bindBatchSelection" in rendered
    assert "refreshBatchSelection" in rendered
    assert rendered.count("markArea: ratingContextArea()") == 4
    assert rendered.count("barMaxWidth: 7") == 2
    assert '"ratingBatches"' in rendered
    assert "rating-bin-highlight" in rendered
    assert "rating-distribution-bars" in rendered
    assert "distributionBarData" in rendered
    assert "selectedRatingBinIndexes" in rendered
    assert "ratingSelectionPalette" in rendered
    assert "sourceEvent.ctrlKey" in rendered
    assert "sourceEvent.metaKey" in rendered
    assert "symbolSize: 12" in rendered
    assert "rgba(255, 255, 255, 0)" not in rendered
    assert "color: '#ffffff', borderColor: colors.indigoDark, borderWidth: 2" in rendered
    assert "borderWidth: 2.4" not in rendered
    assert "rating-selection-clear" in rendered
    assert "取消选择" in rendered
    assert "selectedRatingBinIndexes.length === 1 && existingPosition === 0" in rendered
    assert "updateRatingSelectionControl" in rendered
    assert "batch.startIndex" in rendered
    assert "ai-selected-raw" in rendered
    assert "rawPointData" in rendered
    assert 'data-trend-view="overview"' in rendered
    assert 'data-trend-view="detail"' in rendered
    assert "header-metrics" in rendered
    assert "header-metrics-grid" in rendered
    assert "header-metrics-vertical" not in rendered
    assert "header-metric-primary" in rendered
    assert "全样本 Rating 平均值" in rendered
    assert "header-dual-metric" in rendered
    assert "review-snapshot" not in rendered
    assert '"ratingBandLower"' not in rendered
    assert "半庄中间50%" not in rendered
    assert "观察常见水平" not in rendered
    assert "section-note" not in rendered
    assert "趋势预测线" not in rendered
    assert "linear-gradient" not in rendered
    assert "$payload" not in rendered
