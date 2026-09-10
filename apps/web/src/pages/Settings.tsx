import { Link } from 'wouter';
import { SettingsTab } from '@features/settings/SettingsTab';

export function Settings() {
  return (
    <div className="min-h-screen flex flex-col bg-bg text-text overflow-hidden">
      <header className="flex items-center justify-between px-5 h-12 bg-surface border-b border-border gap-4">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="text-text-muted hover:text-text text-sm flex items-center gap-1"
          >
            ← Назад
          </Link>
          <span className="text-sm font-semibold">НАСТРОЙКИ</span>
        </div>
        <span className="text-xs text-text-muted">Sandbox · v1.0</span>
      </header>
      <main className="flex-1 overflow-y-auto pb-10">
        <SettingsTab />
      </main>
    </div>
  );
}
