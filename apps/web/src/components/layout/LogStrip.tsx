import { useQuery } from '@tanstack/react-query';
import { api } from '@lib/api';
import { z } from 'zod';

const LogSchema = z.object({
  ts: z.string(),
  tone: z.enum(['ok', 'warn', 'err', 'flat']),
  text: z.string(),
});
type Log = z.infer<typeof LogSchema>;

export function LogStrip() {
  const { data } = useQuery<Log[]>({
    queryKey: ['logs'],
    queryFn: async () => LogSchema.array().parse(await api<unknown>('/logs')),
    refetchInterval: 5_000,
  });
  if (!data) return null;
  return (
    <div
      data-testid="global-log-strip"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur px-4 py-1.5 mono text-[11px] text-text-muted flex flex-wrap gap-x-4 gap-y-0.5"
    >
      {data.map((l, i) => (
        <span key={i} className={toneClass(l.tone)}>
          [{l.ts}] {l.text}
        </span>
      ))}
    </div>
  );
}

function toneClass(tone: Log['tone']): string {
  switch (tone) {
    case 'ok':
      return 'text-green';
    case 'warn':
      return 'text-amber';
    case 'err':
      return 'text-red';
    default:
      return '';
  }
}
