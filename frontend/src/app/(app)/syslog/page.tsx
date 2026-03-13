'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { SyslogLiveTail } from '@/components/syslog/SyslogLiveTail';
import { GlassCard } from '@/components/ui/GlassCard';
import { Skeleton } from '@/components/ui/Skeleton';
import { useSyslog } from '@/hooks/queries/useSyslog';
import { useState } from 'react';
import Link from 'next/link';
import { MessageSquare, Brain } from 'lucide-react';

const SEVERITY_LABELS: Record<number, string> = {
  0: 'Emergency',
  1: 'Alert',
  2: 'Critical',
  3: 'Error',
  4: 'Warning',
  5: 'Notice',
  6: 'Info',
  7: 'Debug',
};

const SEVERITY_COLORS: Record<number, string> = {
  0: 'bg-red-500 text-white',
  1: 'bg-red-400 text-white',
  2: 'bg-red-400/80 text-white',
  3: 'bg-orange-400 text-black',
  4: 'bg-amber-400 text-black',
  5: 'bg-blue-400 text-white',
  6: 'bg-sky-400/60 text-white',
  7: 'bg-slate-500 text-white',
};

export default function SyslogPage() {
  const [search, setSearch] = useState('');
  const [selectedSeverity, setSelectedSeverity] = useState<string | undefined>(undefined);
  const [liveEnabled, setLiveEnabled] = useState(false);
  const { data: messages, isLoading } = useSyslog({
    severity: selectedSeverity,
    limit: 200,
  });

  const filtered = messages?.filter((m) =>
    !search || m.message.toLowerCase().includes(search.toLowerCase()) ||
    m.hostname.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <PageHeader
          title="Syslog"
          description="Live syslog messages from all sources"
        />
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4">
        <span className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-white/[0.06] text-slate-100">
          <MessageSquare size={15} /> Messages
        </span>
        <Link href="/syslog/templates" className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-white/[0.03] transition-colors">
          <Brain size={15} /> Intelligence
        </Link>
      </div>

      <div className="flex items-center justify-end mb-2">
        <button
          onClick={() => setLiveEnabled((v) => !v)}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
            liveEnabled
              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30'
              : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
          }`}
        >
          {liveEnabled && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          )}
          Live
        </button>
      </div>

      <SyslogLiveTail
        enabled={liveEnabled}
        severity={selectedSeverity}
      />

      {/* Search bar */}
      <div className="mb-4">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search messages or hosts..."
          className="w-full px-4 py-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/50"
        />
      </div>

      {/* Severity filter pills */}
      <div className="flex flex-wrap gap-2 mb-6">
        <button
          onClick={() => setSelectedSeverity(undefined)}
          className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
            selectedSeverity === undefined
              ? 'bg-sky-500/30 text-sky-300 border border-sky-500/50'
              : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
          }`}
        >
          All
        </button>
        {Array.from({ length: 8 }).map((_, sev) => (
          <button
            key={sev}
            onClick={() => setSelectedSeverity(String(sev))}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              selectedSeverity === String(sev)
                ? 'bg-sky-500/30 text-sky-300 border border-sky-500/50'
                : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
            }`}
          >
            {sev} - {SEVERITY_LABELS[sev]}
          </button>
        ))}
      </div>

      {/* Messages table */}
      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Timestamp</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Sev</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Message</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 12 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-36" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-full" /></td>
                  </tr>
                ))}
              {filtered?.map((msg, i) => (
                <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-3 text-xs text-slate-400 font-mono whitespace-nowrap">
                    {new Date(msg.timestamp).toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_COLORS[msg.severity] ?? 'bg-slate-500 text-white'}`}>
                      {msg.severity}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-300 font-mono whitespace-nowrap">
                    {msg.hostname}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-300 max-w-md truncate">
                    {msg.message}
                  </td>
                </tr>
              ))}
              {!isLoading && (!filtered || filtered.length === 0) && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                    No syslog messages found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}
