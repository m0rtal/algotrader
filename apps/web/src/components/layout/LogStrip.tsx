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
  });
  if (!data) return null;
  return (
    <div className="bg-surface border-t border-border px-4 py-2 mono text-[11px] text-text-muted flex flex-wrap gap-x-4 gap-y-1">
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
