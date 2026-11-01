"""Validate example templates and raw chart generators without real account data."""

import json
import shutil
import subprocess
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from homeassistant.helpers.template import Template

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("hour", [0, 11, 12, 23])
@pytest.mark.parametrize("month,day", [(4, 5), (9, 27), (11, 1)])
def test_daily_chart_span_keeps_oldest_noon_marker(hour, month, day):
    config = yaml.safe_load((ROOT / "dashboard-charts-example.yaml").read_text())
    span = timedelta(days=int(config["cards"][0]["graph_span"].removesuffix("d")))
    zone = ZoneInfo("Pacific/Auckland")
    now = datetime(2026, month, day, hour, 59, tzinfo=zone)
    oldest = datetime.combine(now.date() - timedelta(days=93), time(12), zone)
    assert now.astimezone(UTC) - span <= oldest.astimezone(UTC)


async def test_native_dashboard_templates_handle_empty_zero_and_unknown(hass):
    config = yaml.safe_load((ROOT / "dashboard-example.yaml").read_text())
    templates = [
        c["content"] for c in config["views"][0]["cards"] if c["type"] == "markdown"
    ]
    for content in templates:
        assert isinstance(Template(content, hass).async_render(parse_result=False), str)
    hass.states.async_set(
        "sensor.example_history",
        1,
        {
            "daily": [
                {
                    "start": "2026-09-27T00:00:00+12:00",
                    "total_kwh": 23,
                    "free_kwh": 0,
                    "paid_kwh": 23,
                },
                {
                    "start": "2026-09-28T00:00:00+13:00",
                    "total_kwh": None,
                    "free_kwh": None,
                    "paid_kwh": None,
                },
            ]
        },
    )
    hass.states.async_set(
        "sensor.example_profile",
        1,
        {
            "hourly_average_kwh": {0: 0, 1: None},
            "hourly_samples": {0: 1, 1: 0},
            "weekday_average_kwh": {0: 0, 1: None},
            "weekday_samples": {0: 1, 1: 0},
        },
    )
    rendered = [
        Template(
            content.replace("REPLACE_HISTORY", "example_history").replace(
                "REPLACE_PROFILE", "example_profile"
            ),
            hass,
        ).async_render(parse_result=False)
        for content in templates
    ]
    assert "2026-09-27" in rendered[0] and "2026-09-28" in rendered[0]
    assert "Unknown" in rendered[0] and "| 0.0 |" in rendered[0]
    assert "\n\n| 2026" not in rendered[0]
    assert "00:00" in rendered[1] and "Unknown" in rendered[1]
    assert "Mon" in rendered[2] and "Unknown" in rendered[2]


def test_raw_chart_generators_preserve_zero_null_dates_and_corrections():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the optional dashboard generators")
    config = yaml.safe_load((ROOT / "dashboard-charts-example.yaml").read_text())
    series = [s for card in config["cards"] if "series" in card for s in card["series"]]
    assert len(series) == 4
    for item in series:
        assert item["group_by"] == {"func": "raw"}
        assert item["extend_to"] is False
    script = """
      const assert = require('node:assert/strict');
      const fs = require('node:fs');
      const series = JSON.parse(fs.readFileSync(0, 'utf8'));
      for (const s of series) {
        const generate = new Function('entity', 'hass', 'moment', s.data_generator);
        // The card supplies Moment; emulate only its current offset interface here.
        const moment = () => ({utcOffset:()=>0,tz:()=>({utcOffset:()=>780})});
        const hass = {locale:{time_zone:'server'},
          config:{time_zone:'Pacific/Auckland'}};
        const offset = -780 * 60000;
        const run = entity => generate(entity,hass,moment);
        assert.deepEqual(run({attributes: {}}), []);
        const rows = [
          {start:'2026-09-28T00:00:00+13:00',free_kwh:null,paid_kwh:null},
          {start:'2026-09-27T00:00:00+12:00',free_kwh:0,paid_kwh:23},
          {start:'invalid',free_kwh:1,paid_kwh:2},
        ];
        const entity = {attributes:{daily:rows,monthly:rows}};
        const points = run(entity);
        assert.deepEqual(points, [
          [new Date('2026-09-27T12:00:00').getTime()+offset,
            s.name === 'Free' ? 0 : 23],
          [new Date('2026-09-28T12:00:00').getTime()+offset, null],
        ]);
        hass.locale.time_zone = 'local';
        assert.equal(run(entity)[0][0],new Date('2026-09-27T12:00:00').getTime());
        hass.locale.time_zone = 'server';
        rows[1].free_kwh = null;
        assert.equal(run(entity)[0][1], null);
        rows[1].free_kwh = 4;
        rows[1].paid_kwh = 19;
        assert.equal(run(entity)[0][1], s.name === 'Free' ? 4 : 19);
      }
    """
    subprocess.run(
        [node, "-e", script],
        input=json.dumps(series),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
