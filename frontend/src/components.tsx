import { useState, type ReactNode } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { ChevronDown, ArrowUpDown, AlertTriangle } from "lucide-react";
import type { z } from "zod";
import { money, reason } from "./api";
import type { Stats, bookSchema } from "./schemas";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-symbol">{"\u25C7"}</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading() {
  return (
    <div
      className="skeleton-grid"
      role="status"
      aria-label="Loading data"
      aria-busy="true"
    >
      {[0, 1, 2, 3].map((x) => (
        <div className="skeleton" key={x} />
      ))}
    </div>
  );
}
export function ErrorBox({ error }: { error: Error | null }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <AlertTriangle size={16} />
      {error.message}
    </div>
  );
}
export function Panel({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function DataTable<T>({
  rows,
  columns,
  empty = "No records yet",
}: {
  rows: T[];
  columns: ColumnDef<T>[];
  empty?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  if (!rows.length)
    return (
      <Empty title={empty}>
        The scanner never creates a trade to meet a daily target.
      </Empty>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => (
                <th key={header.id}>
                  <button
                    className="table-heading"
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    {flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    )}
                    {header.column.getCanSort() && <ArrowUpDown size={11} />}
                  </button>
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Pagination({
  total,
  offset,
  onChange,
}: {
  total: number;
  offset: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="pagination">
      <span>
        {total} observations · Showing {total ? offset + 1 : 0}–
        {Math.min(total, offset + 50)}
      </span>
      <div>
        <button
          disabled={!offset}
          onClick={() => onChange(Math.max(0, offset - 50))}
        >
          Previous
        </button>
        <button
          disabled={offset + 50 >= total}
          onClick={() => onChange(offset + 50)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
export function JsonEvidence({
  data,
  title = "Reproducible inputs",
}: {
  data: unknown;
  title?: string;
}) {
  return (
    <details className="evidence">
      <summary>
        {title}
        <ChevronDown size={14} />
      </summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
export function Reasons({ reasons }: { reasons: string[] }) {
  return (
    <div className="reason-list">
      {reasons.map((x) => (
        <Badge tone="warning" key={x}>
          {reason(x)}
        </Badge>
      ))}
    </div>
  );
}
export function BookView({
  book,
  title,
}: {
  book: z.infer<typeof bookSchema>;
  title: string;
}) {
  return (
    <div className="book">
      <div className="book-title">
        <h3>{title}</h3>
        <Badge>{book.transport}</Badge>
      </div>
      <div className="book-columns">
        <div>
          <small>BID / SIZE</small>
          {book.bids.slice(0, 6).map((x) => (
            <div className="book-level bid" key={x.price}>
              <span>{money(x.price)}</span>
              <span>{Number(x.quantity).toLocaleString()}</span>
            </div>
          ))}
        </div>
        <div>
          <small>ASK / SIZE</small>
          {book.asks.slice(0, 6).map((x) => (
            <div className="book-level ask" key={x.price}>
              <span>{money(x.price)}</span>
              <span>{Number(x.quantity).toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
export function Chart({
  series,
  mode,
}: {
  series: Stats["series"];
  mode: "count" | "profit";
}) {
  const rows = series.map((x) => ({
    ...x,
    simulated: Number(x.simulated),
    expected: Number(x.expected),
  }));
  const axes = (
    <>
      <CartesianGrid vertical={false} stroke="#222b39" strokeDasharray="3 3" />
      <XAxis
        dataKey="hour"
        tick={{ fill: "#8591a5", fontSize: 11 }}
        interval={5}
        tickLine={false}
        axisLine={false}
      />
      <YAxis
        tick={{ fill: "#8591a5", fontSize: 11 }}
        tickLine={false}
        axisLine={false}
        width={45}
      />
      <Tooltip
        contentStyle={{
          background: "#111924",
          border: "1px solid #303c50",
          borderRadius: 8,
        }}
      />
    </>
  );
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={240}>
        {mode === "count" ? (
          <AreaChart data={rows}>
            {axes}
            <defs>
              <linearGradient id="signalFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#6887ff" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#6887ff" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area
              isAnimationActive={false}
              type="monotone"
              dataKey="count"
              stroke="#8197ff"
              fill="url(#signalFill)"
              strokeWidth={2}
            />
          </AreaChart>
        ) : (
          <LineChart data={rows}>
            {axes}
            <Line
              isAnimationActive={false}
              type="monotone"
              dataKey="expected"
              stroke="#718198"
              strokeDasharray="4 4"
              dot={false}
            />
            <Line
              isAnimationActive={false}
              type="monotone"
              dataKey="simulated"
              stroke="#6fcda9"
              strokeWidth={2}
              dot={false}
            />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
