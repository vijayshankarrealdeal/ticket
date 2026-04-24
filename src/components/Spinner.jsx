export function Spinner({ size = 20, stroke = 2, className = '', style }) {
  const s = Number(size);
  return (
    <span
      className={`spinner ${className}`.trim()}
      style={{ width: s, height: s, borderWidth: stroke, ...style }}
      role="status"
      aria-label="Loading"
    />
  );
}

export function LoadingBlock({ size = 28, label, fullHeight = true, style }) {
  return (
    <div
      className="loading-block"
      style={{ minHeight: fullHeight ? '100%' : undefined, ...style }}
    >
      <Spinner size={size} stroke={Math.max(2, Math.round(size / 12))} />
      {label ? <div className="loading-block-label">{label}</div> : null}
    </div>
  );
}
