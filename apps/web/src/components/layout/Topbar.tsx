export function Topbar() {
  return (
    <div className="flex items-center justify-between px-5 h-12 bg-surface border-b border-border">
      <div className="flex items-center gap-2.5 font-semibold text-sm">
        <span className="w-2 h-2 rounded-full bg-green shadow-[0_0_6px_var(--color-green)] animate-pulse" />
        <span>ALGOTRADER</span>
        <span className="text-text-muted mono font-normal text-xs">· MOEX</span>
      </div>
      <div className="flex gap-5 text-xs text-text-muted">
        <span>
          Сессия <span className="text-text mono ml-1.5">CLOSED</span>
        </span>
        <span>
          IMOEX <span className="text-green mono ml-1.5">3 142.8</span>{' '}
          <span className="text-green mono ml-1">+0.42%</span>
        </span>
        <span>
          Обновлено <span className="text-text mono ml-1.5">19:34 МСК</span>
        </span>
        <span>Sandbox</span>
      </div>
    </div>
  );
}
