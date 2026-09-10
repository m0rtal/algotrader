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
  const empty = data.length === 0;
  return (
    <div
      data-testid="global-log-strip"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur px-4 py-1.5 mono text-[11px] text-text-muted max-h-16 overflow-hidden flex flex-col gap-0.5"
    >
      {empty ? (
        <span className="text-text-dim">— Нет событий —</span>
      ) : (
        data.slice(0, 4).map((l, i) => (
          <span key={i} className={`whitespace-nowrap ${toneClass(l.tone)}`}>
            [{l.ts}] {l.text}
          </span>
        ))
      )}
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
