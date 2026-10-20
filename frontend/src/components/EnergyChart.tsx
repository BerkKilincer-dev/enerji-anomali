import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import type { Anomaly, ForecastPoint, Reading } from "../api";
import { anomalyLabel, hourAfter } from "../format";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkAreaComponent, CanvasRenderer]);

type Props = { readings: Reading[]; forecast: ForecastPoint[]; anomalies: Anomaly[] };

const LAYOUT = {
  tooltip: { trigger: "axis" },
  legend: { top: 0, data: ["Actual", "Expected"] },
  grid: { left: 56, right: 16, top: 48, bottom: 64 },
  xAxis: { type: "time" },
  yAxis: { type: "value", name: "kWh" },
  dataZoom: [{ type: "inside" }, { type: "slider" }],
};

export function EnergyChart({ readings, forecast, anomalies }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts>(undefined);

  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current);
    chart.current = instance;
    const resize = () => instance.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      instance.dispose();
    };
  }, []);

  useEffect(() => {
    const first = readings[0]?.ts ?? "";
    const marks = anomalies
      .filter((a) => a.ts_end >= first)
      .map((a) => [{ xAxis: a.ts_start, name: anomalyLabel(a.source, a.rule_type) }, { xAxis: hourAfter(a.ts_end) }]);
    chart.current?.setOption(
      {
        ...LAYOUT,
        series: [
          {
            name: "Actual",
            type: "line",
            showSymbol: false,
            data: readings.map((r) => [r.ts, r.kwh]),
            markArea: { itemStyle: { color: "rgba(220, 38, 38, 0.15)" }, data: marks },
          },
          {
            name: "Expected",
            type: "line",
            showSymbol: false,
            lineStyle: { type: "dashed" },
            data: forecast.map((f) => [f.ts, f.expected_kwh]),
          },
        ],
      },
      { notMerge: true },
    );
  }, [readings, forecast, anomalies]);

  return <div ref={ref} className="chart" role="img" aria-label="Actual and expected consumption" />;
}
