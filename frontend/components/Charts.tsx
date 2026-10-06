"use client";

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

export const PALETTES: Record<string, string[]> = {
  slate: ["#334155", "#64748b", "#94a3b8", "#0f766e", "#14b8a6"],
  ocean: ["#123b5d", "#176b87", "#2896a5", "#56b4a9", "#a0cbb5"],
  berry: ["#613659", "#914e72", "#c46e83", "#e49a83", "#f1c27b"],
  citrus: ["#365b3d", "#478759", "#82a94c", "#d0aa45", "#e17c45"],
};

type Series = { label: string; value: number };

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
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
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

export function PalettePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (name: string) => void;
}) {
  return (
    <div className="palette-picker" role="group" aria-label="Chart color palette">
      <span className="palette-picker__label">Palette</span>
      {Object.entries(PALETTES).map(([name, colors]) => (
        <button
          key={name}
          className={`palette-option${value === name ? " palette-option--active" : ""}`}
          type="button"
          onClick={() => onChange(name)}
          aria-label={`Use ${name} palette`}
          aria-pressed={value === name}
          title={`${name[0].toUpperCase()}${name.slice(1)}`}
        >
          <span className="palette-option__swatches" aria-hidden="true">
            {colors.slice(0, 4).map((color) => (
              <span key={color} style={{ backgroundColor: color }} />
            ))}
          </span>
          <span className="palette-option__name">{name}</span>
        </button>
      ))}
    </div>
  );
}

export function BarList({
  data,
  colors,
  formatValue: formatAxisValue = formatValue,
}: {
  data: Series[];
  colors: string[];
  formatValue?: (value: number) => string;
}) {
  if (!data.length) return <EmptyChart />;
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
        options={commonOptions<"bar">({
          indexAxis: "y",
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

export function LineChart({ data, colors }: { data: Series[]; colors: string[] }) {
  if (!data.length) return <EmptyChart />;
  return (
    <div className="chart-frame">
      <Line
        data={{
          labels: data.map((row) => row.label),
          datasets: [
            {
              label: "Volume",
              data: data.map((row) => row.value),
              borderColor: colors[1] || colors[0],
              backgroundColor: `${colors[2] || colors[0]}35`,
              pointBackgroundColor: colors[0],
              pointBorderColor: "#fff",
              pointBorderWidth: 2,
              pointRadius: 4,
              pointHoverRadius: 6,
              borderWidth: 2.5,
              tension: 0.36,
              fill: true,
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

export function PieChart({ data, colors }: { data: Series[]; colors: string[] }) {
  const total = data.reduce((sum, row) => sum + row.value, 0);
  if (!data.length || total <= 0) return <EmptyChart />;
  const chartData: ChartData<"doughnut", number[], string> = {
    labels: data.map((row) => row.label),
    datasets: [
      {
        data: data.map((row) => row.value),
        backgroundColor: data.map((_, index) => colors[index % colors.length]),
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
          options={commonOptions<"doughnut">({
            cutout: "70%",
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
          <strong>{formatValue(total)}</strong>
          <span>Total</span>
        </div>
      </div>
      <div className="chart-legend">
        {data.map((row, index) => (
          <div className="chart-legend__item" key={row.label}>
            <span className="chart-legend__dot" style={{ backgroundColor: colors[index % colors.length] }} />
            <span className="chart-legend__label">{row.label}</span>
            <strong>{((row.value / total) * 100).toFixed(1)}%</strong>
          </div>
        ))}
      </div>
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

function EmptyChart() {
  return <div className="chart-empty">No chartable data is available for this view.</div>;
}
