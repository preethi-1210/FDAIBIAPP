type HeatCell = { region: string; category: string; value: number };

const valueBands = [
  { label: "Lower values", className: "value-1" },
  { label: "Below median", className: "value-2" },
  { label: "Above median", className: "value-3" },
  { label: "Highest values", className: "value-4" },
] as const;

function formatAmount(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}

function formatCellAmount(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumSignificantDigits: 3,
  }).format(value);
}

function quantile(sortedValues: number[], fraction: number): number {
  const index = (sortedValues.length - 1) * fraction;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  const weight = index - lower;
  return sortedValues[lower] * (1 - weight) + sortedValues[upper] * weight;
}

export function RiskMatrixHeatMap({
  data,
  metricName,
}: {
  data: HeatCell[];
  metricName: string;
}) {
  const regions = [...new Set(data.map((cell) => cell.region))].sort((a, b) => a.localeCompare(b));
  const categories = [...new Set(data.map((cell) => cell.category))].sort((a, b) => a.localeCompare(b));
  const cellValues = new Map(data.map((cell) => [`${cell.region}\u0000${cell.category}`, cell.value]));
  const values = categories
    .flatMap((category) => regions.map((region) => cellValues.get(`${region}\u0000${category}`) ?? 0))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const thresholds = values.length
    ? [quantile(values, 0.25), quantile(values, 0.5), quantile(values, 0.75)]
    : [];
  const bandValues = valueBands.map((_, bandIndex) =>
    values.filter((value) => {
      const valueBand = thresholds.findIndex((threshold) => value <= threshold);
      return (valueBand === -1 ? valueBands.length - 1 : valueBand) === bandIndex;
    }),
  );

  function getBand(value: number): number {
    const index = thresholds.findIndex((threshold) => value <= threshold);
    return index === -1 ? valueBands.length - 1 : index;
  }

  return (
    <section className="dashboard-card risk-matrix" aria-labelledby="data-heatmap-title">
      <div className="risk-matrix__heading">
        <div>
          <h2 id="data-heatmap-title">{metricName} by region and category</h2>
          <p>Cell values come from the dashboard data. Color bands show relative values in this view.</p>
        </div>
      </div>

      {!data.length ? (
        <div className="dashboard-table-empty">No region and category data is available for this domain.</div>
      ) : (
        <div className="risk-matrix__layout">
          <div className="risk-matrix__board">
            <div className="risk-matrix__table-scroll" role="region" aria-label={`${metricName} by region and category`} tabIndex={0}>
              <table
                className="risk-matrix__table"
                style={{ minWidth: `${Math.max(560, 128 + regions.length * 52)}px` }}
              >
                <thead>
                  <tr>
                    <th scope="col" className="risk-matrix__corner">Category ↓<br />Region →</th>
                    {regions.map((region) => (
                      <th scope="col" key={region}>{region}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {categories.map((category) => (
                    <tr key={category}>
                      <th scope="row">{category}</th>
                      {regions.map((region) => {
                        const value = cellValues.get(`${region}\u0000${category}`) ?? 0;
                        const band = getBand(value);
                        return (
                          <td
                            className={`risk-matrix__cell risk-matrix__cell--${valueBands[band].className}`}
                            key={`${category}-${region}`}
                            aria-label={`${category} in ${region}: ${formatAmount(value)} ${metricName}`}
                            title={`${category} · ${region}: ${formatAmount(value)} ${metricName}`}
                          >
                            {formatCellAmount(value)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <aside className="risk-matrix__legend" aria-label="Relative value legend">
            <h3>Relative values</h3>
            {valueBands.map(({ label, className }, index) => {
              const range = bandValues[index];
              if (!range.length) return null;
              const rangeLabel = range[0] === range[range.length - 1]
                ? formatAmount(range[0])
                : `${formatAmount(range[0])} – ${formatAmount(range[range.length - 1])}`;
              return (
                <div className="risk-matrix__legend-item" key={className}>
                  <span className={`risk-matrix__swatch risk-matrix__swatch--${className}`} aria-hidden="true" />
                  <div>
                    <strong>{label}</strong>
                    <p>{rangeLabel} {metricName}</p>
                  </div>
                </div>
              );
            })}
          </aside>
        </div>
      )}

      {values.length ? <p className="risk-matrix__note">Color thresholds are recalculated from the current data set on each dashboard refresh.</p> : null}
    </section>
  );
}
