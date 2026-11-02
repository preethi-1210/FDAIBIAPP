"use client";

import { useEffect, useState } from "react";
import {
  BarChart3,
  LineChart as LineChartIcon,
  PieChart as PieChartIcon,
} from "lucide-react";
import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions,
  type Plugin,
} from "chart.js";
import { Bar, Doughnut, Line } from "react-chartjs-2";

ChartJS.register(
  ArcElement,
  BarElement,
  CategoryScale,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
);

export const CHART_COLORS = [
  "#4778F5",
  "#39B995",
  "#9858EA",
  "#FF884C",
  "#4AA6D2",
  "#FF5D87",
  "#8060F5",
  "#2CBFCE",
  "#F0B83F",
  "#3D8CF5",
];

export const REGION_CHART_COLORS = [
  "#6C27F2",
  "#8252F3",
  "#9877F4",
  "#B29CF7",
  "#D0C2FA",
];

const ACTIVITY_BAR_COLOR = "#B29CF7";

export type ChartType = "bar" | "line" | "pie";
const CHART_TYPE_KEY = "ryze_chart_type";
const CHART_TYPE_EVENT = "ryze-chart-type-change";
const DEFAULT_CHART_TYPE: ChartType = "line";

function isChartType(value: string | null): value is ChartType {
  return value === "bar" || value === "line" || value === "pie";
}

export function useChartType(): [ChartType, (type: ChartType) => void] {
  const [chartType, setCurrentChartType] = useState<ChartType>(DEFAULT_CHART_TYPE);

  useEffect(() => {
    const savedType = localStorage.getItem(CHART_TYPE_KEY);
    if (isChartType(savedType)) setCurrentChartType(savedType);

    const updateChartType = (event: Event) => {
      const selected = (event as CustomEvent<ChartType>).detail;
      if (isChartType(selected)) setCurrentChartType(selected);
    };
    const updateFromStorage = (event: StorageEvent) => {
      if (event.key && event.key !== CHART_TYPE_KEY) return;
      if (isChartType(event.newValue)) setCurrentChartType(event.newValue);
    };

    window.addEventListener(CHART_TYPE_EVENT, updateChartType);
    window.addEventListener("storage", updateFromStorage);
    return () => {
      window.removeEventListener(CHART_TYPE_EVENT, updateChartType);
      window.removeEventListener("storage", updateFromStorage);
    };
  }, []);

  function setChartType(type: ChartType) {
    setCurrentChartType(type);
    localStorage.setItem(CHART_TYPE_KEY, type);
    window.dispatchEvent(new CustomEvent(CHART_TYPE_EVENT, { detail: type }));
  }

  return [chartType, setChartType];
}

type Series = { label: string; value: number | null };

const axisColor = "#8292a5";

const axisOptions = {
  x: {
    grid: { display: false },
    border: { display: false },
    ticks: { color: axisColor, font: { family: "Inter, system-ui, sans-serif", size: 11 } },
  },
  y: {
    beginAtZero: true,
    border: { display: false },
    grid: { display: false },
    ticks: {
      color: axisColor,
      font: { family: "Inter, system-ui, sans-serif", size: 11 },
      callback: (value: string | number) =>
        typeof value === "number" && Math.abs(value) >= 1000
          ? `${(value / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`
          : value,
    },
  },
} satisfies ChartOptions<"bar">["scales"];

function formatValue(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function commonOptions<TType extends "bar" | "line" | "doughnut">(
  overrides: ChartOptions<TType>,
): ChartOptions<TType> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 700, easing: "easeOutQuart" },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#14283f",
        titleColor: "#f8fafc",
        bodyColor: "#e2e8f0",
        padding: 11,
        cornerRadius: 10,
        displayColors: true,
      },
    },
    ...overrides,
  } as ChartOptions<TType>;
}

export function ChartTypePicker({
  value,
  onChange,
}: {
  value: ChartType;
  onChange: (type: ChartType) => void;
}) {
  const chartTypes: { type: ChartType; label: string; icon: typeof BarChart3 }[] = [
    { type: "bar", label: "Bar chart", icon: BarChart3 },
    { type: "line", label: "Line chart", icon: LineChartIcon },
    { type: "pie", label: "Pie chart", icon: PieChartIcon },
  ];

  return (
    <div className="chart-type-picker" role="group" aria-label="Chart type">
      {chartTypes.map(({ type, label, icon: Icon }) => (
        <button
          key={type}
          type="button"
          className={`chart-type-picker__button${value === type ? " chart-type-picker__button--active" : ""}`}
          aria-label={`Use ${label.toLowerCase()}`}
          aria-pressed={value === type}
          title={label}
          onClick={() => onChange(type)}
        >
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}

export function BarList({
  data,
  colors,
  formatValue: formatAxisValue = formatValue,
  showValues = false,
}: {
  data: Series[];
  colors: string[];
  formatValue?: (value: number) => string;
  showValues?: boolean;
}) {
  if (!data.length) return <EmptyChart />;
  const valueLabels: Plugin<"bar"> = {
    id: "barEndValues",
    afterDatasetsDraw(chart) {
      if (!showValues) return;
      const context = chart.ctx;
      const dataset = chart.data.datasets[0];
      if (!dataset) return;
      context.save();
      context.fillStyle = "#51607a";
      context.font = "600 10px Inter, system-ui, sans-serif";
      context.textAlign = "left";
      context.textBaseline = "middle";
      chart.getDatasetMeta(0).data.forEach((element, index) => {
        const raw = dataset.data[index];
        if (typeof raw !== "number" || !Number.isFinite(raw)) return;
        const text = formatAxisValue(raw);
        const bar = element.getProps(["x", "y"], true);
        const textWidth = context.measureText(text).width;
        const x = Math.min(bar.x + 7, chart.chartArea.right - textWidth - 2);
        context.fillText(text, x, bar.y);
      });
      context.restore();
    },
  };
  return (
    <div className="chart-frame chart-frame--bars">
      <Bar
        data={{
          labels: data.map((row) => row.label),
          datasets: [
            {
              data: data.map((row) => row.value),
              backgroundColor: data.map((_, index) => colors[index % colors.length]),
              borderRadius: 7,
              borderSkipped: false,
              maxBarThickness: 30,
            },
          ],
        }}
        plugins={[valueLabels]}
        options={commonOptions<"bar">({
          indexAxis: "y",
          layout: { padding: { right: showValues ? 42 : 2 } },
          scales: {
            x: {
              ...axisOptions.y,
              ticks: {
                ...axisOptions.y.ticks,
                callback: (value) => formatAxisValue(Number(value)),
              },
            },
            y: { ...axisOptions.x, grid: { display: false } },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: { label: (context) => ` ${formatAxisValue(Number(context.parsed.x))}` },
            },
          },
        })}
      />
    </div>
  );
}

export function ComparisonBarChart({
  data,
  colors,
  currentLabel = "Current period",
  comparisonLabel = "Comparison period",
}: {
  data: { label: string; value: number; comparison_value: number }[];
  colors: string[];
  currentLabel?: string;
  comparisonLabel?: string;
}) {
  if (!data.length) return <EmptyChart />;
  return (
    <div className="chart-frame chart-frame--bars">
      <Bar
        data={{
          labels: data.map((row) => row.label),
          datasets: [
            {
              label: currentLabel,
              data: data.map((row) => row.value),
              backgroundColor: colors[0],
              borderRadius: 5,
              borderSkipped: false,
              maxBarThickness: 22,
            },
            {
              label: comparisonLabel,
              data: data.map((row) => row.comparison_value),
              backgroundColor: colors[2] || colors[1],
              borderRadius: 5,
              borderSkipped: false,
              maxBarThickness: 22,
            },
          ],
        }}
        options={commonOptions<"bar">({
          indexAxis: "y",
          scales: {
            x: {
              ...axisOptions.y,
              ticks: {
                ...axisOptions.y.ticks,
                callback: (value) => formatValue(Number(value)),
              },
            },
            y: { ...axisOptions.x, grid: { display: false } },
          },
          plugins: {
            legend: { display: true, position: "bottom", labels: { boxWidth: 9, boxHeight: 9, usePointStyle: true } },
            tooltip: {
              callbacks: { label: (context) => ` ${context.dataset.label}: ${formatValue(Number(context.parsed.x))}` },
            },
          },
        })}
      />
    </div>
  );
}

export function LineChart({
  data,
  colors,
  label = "Volume",
}: {
  data: Series[];
  colors: string[];
  label?: string;
}) {
  if (!data.some((row) => row.value !== null)) return <EmptyChart />;
  return (
    <div className="chart-frame">
      <Line
        data={{
          labels: data.map((row) => row.label),
          datasets: [
            {
              label,
              data: data.map((row) => row.value),
              borderColor: colors[1] || colors[0],
              backgroundColor: "#39B99524",
              pointBackgroundColor: colors[1] || colors[0],
              pointBorderColor: "#fff",
              pointBorderWidth: 2,
              pointRadius: 4,
              pointHoverRadius: 6,
              borderWidth: 2.5,
              tension: 0.36,
              fill: true,
              spanGaps: false,
            },
          ],
        }}
        options={commonOptions<"line">({
          interaction: { intersect: false, mode: "index" },
          scales: { x: axisOptions.x, y: axisOptions.y },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: { label: (context) => ` ${formatValue(Number(context.parsed.y))}` },
            },
          },
        })}
      />
    </div>
  );
}

export function PieChart({
  data,
  colors,
  showLegend = true,
  centerLabel = "Total",
}: {
  data: Series[];
  colors: string[];
  showLegend?: boolean;
  centerLabel?: string;
}) {
  const availableData = data.filter((row): row is { label: string; value: number } =>
    typeof row.value === "number" && Number.isFinite(row.value),
  );
  const total = availableData.reduce((sum, row) => sum + row.value, 0);
  if (!availableData.length || total <= 0) return <EmptyChart />;
  const formattedTotal = formatValue(total);
  const centerValue = formattedTotal.length > 12
    ? new Intl.NumberFormat(undefined, { notation: "compact", maximumSignificantDigits: 4 }).format(total)
    : formattedTotal;
  const centerFontSize = Math.max(9, Math.min(20, 118 / (centerValue.length * 0.58)));
  const chartData: ChartData<"doughnut", number[], string> = {
    labels: availableData.map((row) => row.label),
    datasets: [
      {
        data: availableData.map((row) => row.value),
        backgroundColor: availableData.map((_, index) => colors[index % colors.length]),
        borderColor: "#fff",
        borderWidth: 3,
        hoverOffset: 7,
        spacing: 2,
      },
    ],
  };
  return (
    <div className="doughnut-layout">
      <div className="chart-frame chart-frame--doughnut">
        <Doughnut
          data={chartData}
          plugins={[doughnutPercentageLabels]}
          options={commonOptions<"doughnut">({
            cutout: "70%",
            layout: { padding: 10 },
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (context) => {
                    const value = Number(context.parsed);
                    return ` ${context.label}: ${formatValue(value)} (${((value / total) * 100).toFixed(1)}%)`;
                  },
                },
              },
            },
          })}
        />
        <div className="doughnut-center">
          <strong style={{ fontSize: `${centerFontSize}px` }} title={`Exact total: ${formattedTotal}`} aria-label={`Total ${formattedTotal}`}>
            {centerValue}
          </strong>
          <span>{centerLabel}</span>
        </div>
      </div>
      {showLegend ? (
        <div className="chart-legend">
          {availableData.map((row, index) => (
            <div className="chart-legend__item" key={row.label}>
              <span className="chart-legend__dot" style={{ backgroundColor: colors[index % colors.length] }} />
              <span className="chart-legend__label">{row.label}</span>
              <strong>{((row.value / total) * 100).toFixed(1)}%</strong>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const doughnutPercentageLabels: Plugin<"doughnut"> = {
  id: "doughnutPercentageLabels",
  afterDatasetDraw(chart, args) {
    const values = chart.data.datasets[args.index]?.data;
    const arcs = chart.getDatasetMeta(args.index).data;
    if (!values) return;
    const context = chart.ctx;
    context.save();
    context.fillStyle = "#fff";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const chartArea = chart.chartArea;
    arcs.forEach((element, index) => {
      const value = Number(values[index]);
      const total = values.reduce<number>((sum, item) => sum + Number(item || 0), 0);
      if (!total || value / total < 0.045) return;
      const arc = element.getProps(["x", "y", "startAngle", "endAngle", "innerRadius", "outerRadius"], true);
      const angle = (arc.startAngle + arc.endAngle) / 2;
      const label = `${((value / total) * 100).toFixed(1)}%`;
      const fontSize = 9;
      context.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
      const radius = arc.innerRadius + (arc.outerRadius - arc.innerRadius) * 0.55;
      const x = arc.x + Math.cos(angle) * radius;
      const y = arc.y + Math.sin(angle) * radius;
      const labelWidth = context.measureText(label).width;
      const segmentWidth = Math.abs(arc.endAngle - arc.startAngle) * radius;
      if (
        segmentWidth < labelWidth + 5 ||
        x - labelWidth / 2 < chartArea.left + 2 ||
        x + labelWidth / 2 > chartArea.right - 2 ||
        y - fontSize / 2 < chartArea.top + 2 ||
        y + fontSize / 2 > chartArea.bottom - 2
      ) return;
      context.fillText(label, x, y);
    });
    context.restore();
  },
};

export function ActivityBarChart({ data }: { data: Series[] }) {
  if (!data.some((row) => row.value !== null)) return <EmptyChart />;
  return (
    <div className="chart-frame">
      <Bar
        data={{
          labels: data.map((row) => row.label),
          datasets: [{
            label: "Events",
            data: data.map((row) => row.value),
            backgroundColor: ACTIVITY_BAR_COLOR,
            borderRadius: 5,
            borderSkipped: false,
            maxBarThickness: 34,
          }],
        }}
        options={commonOptions<"bar">({
          scales: {
            x: {
              ...axisOptions.x,
              grid: { display: false },
              ticks: { ...axisOptions.x.ticks, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
            },
            y: axisOptions.y,
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: { label: (context) => ` ${formatValue(Number(context.parsed.y))} events` },
            },
          },
        })}
      />
    </div>
  );
}

export function AutoChart({
  spec,
  colors,
}: {
  spec: { type: string; x: string; y: string; data: Series[] };
  colors: string[];
}) {
  if (!spec?.data?.length) return <EmptyChart />;
  if (spec.type === "line") return <LineChart data={spec.data} colors={colors} />;
  if (spec.type === "pie") return <PieChart data={spec.data} colors={colors} />;
  return <BarList data={spec.data} colors={colors} />;
}

export function DynamicChart({
  type,
  data,
  colors,
  formatValue,
}: {
  type: ChartType;
  data: Series[];
  colors: string[];
  formatValue?: (value: number) => string;
}) {
  if (type === "pie") return <PieChart data={data} colors={colors} />;
  if (type === "bar") return <BarList data={data} colors={colors} formatValue={formatValue} />;
  return <LineChart data={data} colors={colors} />;
}

function EmptyChart() {
  return <div className="chart-empty">No data available for the selected period.</div>;
}
