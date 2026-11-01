import { useState } from "react";
import { fmtSec } from "../api";

// 每次运行一根柱子，按阶段堆叠耗时。颜色按"阶段"固定分配（不随筛选变化），
// 用的是 dataviz 参考色板的深色档（已验证色盲可分），文字一律用正文颜色，不用系列色。
export const STAGES: { key: string; label: string; color: string }[] = [
  { key: "images", label: "出图 + 质检", color: "#3987e5" },
  { key: "audio", label: "配音", color: "#d95926" },
  { key: "i2v", label: "图生视频", color: "#199e70" },
  { key: "subtitle", label: "字幕", color: "#c98500" },
  { key: "video", label: "预览视频", color: "#d55181" },
];

export interface Bar { id: string; label: string; sub?: string; values: Record<string, number>; status?: string }

export function StackedBars({ bars, height = 220 }: { bars: Bar[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const used = STAGES.filter((s) => bars.some((b) => (b.values[s.key] || 0) > 0));
  const totals = bars.map((b) => used.reduce((a, s) => a + (b.values[s.key] || 0), 0));
  const max = Math.max(1, ...totals);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const W = Math.max(360, bars.length * 34 + 60), H = height, padL = 54, padB = 22, padT = 8;
  const plotW = W - padL - 8, plotH = H - padB - padT;
  const bw = Math.min(26, plotW / Math.max(1, bars.length) - 8);
  const x = (i: number) => padL + (i + 0.5) * (plotW / Math.max(1, bars.length));
  const y = (v: number) => padT + plotH - (v / top) * plotH;

  if (!bars.length) return <div className="muted">还没有运行记录</div>;
  return (
    <div className="chart">
      <div className="chart-head">
        <div className="legend">
          {used.map((s) => <span key={s.key} className="legend-item"><i style={{ background: s.color }} />{s.label}</span>)}
        </div>
        <button className="btn small ghost" onClick={() => setTable(!table)}>{table ? "看图表" : "看表格"}</button>
      </div>
      {table ? (
        <table className="table">
          <thead><tr><th>运行</th>{used.map((s) => <th key={s.key}>{s.label}</th>)}<th>合计</th></tr></thead>
          <tbody>{bars.map((b, i) => (
            <tr key={b.id}><td>{b.label}<div className="muted small">{b.sub}</div></td>
              {used.map((s) => <td key={s.key}>{fmtSec(b.values[s.key] || null)}</td>)}<td>{fmtSec(totals[i])}</td></tr>
          ))}</tbody>
        </table>
      ) : (
        <div className="chart-scroll">
          <svg width={W} height={H} role="img" aria-label="每次运行各阶段耗时">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={W - 8} y1={y(t)} y2={y(t)} className="grid" />
                <text x={padL - 6} y={y(t) + 4} className="axis" textAnchor="end">{axisLabel(t, top)}</text>
              </g>
            ))}
            {bars.map((b, i) => {
              let acc = 0;
              const segs = used.filter((s) => (b.values[s.key] || 0) > 0);
              return (
                <g key={b.id} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  {/* 命中区域比柱子大，鼠标不用对得很准 */}
                  <rect x={x(i) - (plotW / bars.length) / 2} y={padT} width={plotW / bars.length} height={plotH} fill="transparent" />
                  {segs.map((s, k) => {
                    const v = b.values[s.key];
                    const y0 = y(acc), y1 = y(acc + v);
                    acc += v;
                    const h = Math.max(1, y0 - y1 - (k < segs.length - 1 ? 2 : 0)); // 段与段之间留 2px
                    const isTop = k === segs.length - 1;
                    return isTop
                      ? <path key={s.key} d={roundTop(x(i) - bw / 2, y1, bw, Math.max(1, y0 - y1), Math.min(4, bw / 2, y0 - y1))} fill={s.color} opacity={hover === null || hover === i ? 1 : 0.45} />
                      : <rect key={s.key} x={x(i) - bw / 2} y={y0 - h} width={bw} height={h} fill={s.color} opacity={hover === null || hover === i ? 1 : 0.45} />;
                  })}
                  {b.status && b.status !== "ok" ? <text x={x(i)} y={y(totals[i]) - 4} textAnchor="middle" className="axis">✕</text> : null}
                </g>
              );
            })}
            <line x1={padL} x2={W - 8} y1={y(0)} y2={y(0)} className="baseline" />
          </svg>
          {hover !== null ? (
            <div className="tooltip" style={{ left: Math.min(x(hover) + 16, W - 200), top: 8 }}>
              <b>{bars[hover].label}</b>
              {bars[hover].sub ? <div className="muted small">{bars[hover].sub}</div> : null}
              {used.filter((s) => bars[hover].values[s.key]).map((s) => (
                <div key={s.key} className="tt-row"><i style={{ background: s.color }} />{s.label}<span>{fmtSec(bars[hover].values[s.key])}</span></div>
              ))}
              <div className="tt-row total">合计<span>{fmtSec(totals[hover])}</span></div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** 坐标轴统一一种单位：最大值超过 2 分钟就全部用"分"，否则全部用"秒"。 */
function axisLabel(v: number, top: number): string {
  return top >= 120 ? `${+(v / 60).toFixed(1)} 分` : `${v} 秒`;
}

function roundTop(x: number, y: number, w: number, h: number, r: number): string {
  r = Math.max(0, r);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

function niceTicks(max: number): number[] {
  const steps = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  const step = steps.find((s) => max / s <= 5) ?? 3600;
  const out = [];
  for (let v = 0; v <= max + step * 0.999; v += step) out.push(v);
  if (out.length < 2) out.push(step);
  return out;
}
