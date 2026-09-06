const ruFmt = new Intl.NumberFormat('ru-RU');
const ruFmtShort = new Intl.NumberFormat('ru-RU', { notation: 'compact', maximumFractionDigits: 1 });

export function formatRUB(value: number, opts?: { compact?: boolean; sign?: boolean }): string {
  if (Number.isNaN(value)) return '—';
  const formatted = opts?.compact ? ruFmtShort.format(Math.abs(value)) : ruFmt.format(Math.abs(value));
  if (value < 0) return `−${formatted} ₽`;
  if (opts?.sign && value > 0) return `+${formatted} ₽`;
  return `${formatted} ₽`;
}

export function formatNum(value: number, opts?: { compact?: boolean; decimals?: number }): string {
  if (Number.isNaN(value)) return '—';
  if (opts?.compact) return ruFmtShort.format(value);
  return value.toLocaleString('ru-RU', { maximumFractionDigits: opts?.decimals ?? 2 });
}

export function formatPct(value: number, opts?: { decimals?: number; sign?: boolean }): string {
  if (Number.isNaN(value)) return '—';
  const decimals = opts?.decimals ?? 2;
  const sign = value > 0 ? '+' : '';
  const prefix = opts?.sign || value > 0 ? sign : '';
  return `${prefix}${value.toFixed(decimals)}%`;
}

export function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('ru-RU');
  } catch {
    return iso;
  }
}

export function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${formatDate(iso)} ${formatTime(iso)}`;
  } catch {
    return iso;
  }
}
