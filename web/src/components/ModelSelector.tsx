import type { ModelMode } from "../api";
import { useModelRouting } from "../hooks";

const descriptions = {
  local: "只调用本地模型，失败时不使用云端。",
  cloud: "直接调用云端模型，失败时不切回本地。",
  auto: "先调用本地，失败后使用已配置的云端兜底。",
};

export function ModelSelector() {
  const { data, error, pending, loading, reload, choose } = useModelRouting();
  const mode = pending || data?.mode || "local";
  return (
    <section className="model-selector" aria-label="模型调用模式">
      <div className="model-selector-controls">
        <label htmlFor="model-mode">模型调用</label>
        <select id="model-mode" value={mode} disabled={loading || !!pending || !data}
          onChange={(event) => void choose(event.target.value as ModelMode)}>
          <option value="local">仅本地</option>
          <option value="cloud" disabled={!data?.cloud_text.ready && !data?.cloud_vision.ready}>仅云端</option>
          <option value="auto">本地优先、云端兜底</option>
        </select>
        <span className="muted small" role="status">
          {pending ? "正在保存…" : loading ? "正在读取…" : error ? "当前模式未确认，请重新读取" : data ? "已保存 · 对新提交的拆分镜 / 看图质检生效" : "当前模式读取失败"}
        </span>
      </div>
      {error ? <div className="model-selector-error" role="alert">{error} <button className="btn small" onClick={reload}>重新读取</button></div> : null}
      {data ? <div className="model-selector-details">
        <span className="small">{descriptions[mode]}</span>
        <details>
          <summary className="small">云端配置{!data.cloud_text.ready && !data.cloud_vision.ready ? "未就绪" : "状态"}</summary>
          <div className="small muted">文本：{data.cloud_text.detail}</div>
          <div className="small muted">看图：{data.cloud_vision.detail}</div>
          <div className="small muted">云端调用会发送剧本或质检图片，费用由供应商收取。已排队和运行中的任务保留原模式。</div>
        </details>
      </div> : null}
    </section>
  );
}
