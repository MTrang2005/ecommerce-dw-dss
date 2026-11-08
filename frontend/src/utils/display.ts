export const money = (value: number, digits = 0) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
export const compactMoney = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "BRL",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
export const percent = (value: number | null) =>
  value === null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
export const count = (value: number) => Number(value).toLocaleString("en-US");
export const plainValue = (value: number | string | null) =>
  value === null ? "—" : typeof value === "number" ? value.toLocaleString("en-US") : value;
export const monthLabel = (value: string) =>
  /^\d{4}-\d{2}$/.test(value)
    ? new Date(`${value}-01T12:00:00Z`).toLocaleDateString("en-US", {
        month: "short",
        year: "2-digit",
        timeZone: "UTC",
      })
    : value;
export const dateLabel = (value: string | null) => {
  if (!value) return "—";
  const date = new Date(`${value.slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString("en-US", {
        month: "short",
        day: "2-digit",
        year: "numeric",
        timeZone: "UTC",
      });
};
export const axisStyle = { fontSize: 12, fill: "#64748b" };
export const tooltipStyle = {
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  fontSize: 12,
  boxShadow: "0 4px 16px #15234012",
};

