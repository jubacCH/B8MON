'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useHosts } from '@/hooks/queries/useHosts';
import { formatLatency, uptimeColor } from '@/lib/utils';
import { post } from '@/lib/api';
import { Plus, Search, X } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Suspense, useEffect, useRef, useState } from 'react';

const inputClass = 'w-full px-3 py-2 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50';
const selectClass = 'w-full px-3 py-2 text-sm bg-[#111621] border border-white/[0.08] rounded-lg text-slate-200 focus:outline-none focus:border-sky-500/50 [&>option]:bg-[#111621] [&>option]:text-slate-200';

function UptimeBar({ h24, d7, d30 }: { h24: number | null; d7: number | null; d30: number | null }) {
  const bars = [
    { label: '30d', value: d30 },
    { label: '7d', value: d7 },
    { label: '24h', value: h24 },
  ];
  const allNull = bars.every((b) => b.value === null);
  if (allNull) return <span className="text-xs text-slate-600">—</span>;

  function barColor(v: number | null): string {
    if (v === null) return 'bg-slate-700';
    if (v >= 99.9) return 'bg-emerald-500';
    if (v >= 95) return 'bg-amber-500';
    return 'bg-red-500';
  }

  // Show the worst value prominently
  const worst = bars.reduce((min, b) => (b.value !== null && (min === null || b.value < min)) ? b.value : min, null as number | null);

  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5" title={bars.map((b) => `${b.label}: ${b.value != null ? b.value.toFixed(1) + '%' : '—'}`).join(' | ')}>
        {bars.map((b) => (
          <div key={b.label} className={`w-3 h-3 rounded-sm ${barColor(b.value)}`} />
        ))}
      </div>
      <span className={`text-xs font-mono ${uptimeColor(worst)}`}>
        {worst != null ? `${worst.toFixed(1)}%` : ''}
      </span>
    </div>
  );
}

function HostsPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const qParam = searchParams.get('q') ?? '';
  const [search, setSearch] = useState(qParam);
  const { data: hosts, isLoading } = useHosts();
  const redirected = useRef(false);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: '', hostname: '', check_type: 'icmp', port: '' });

  const filteredHosts = hosts?.filter((host) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      host.name.toLowerCase().includes(q) ||
      host.hostname.toLowerCase().includes(q) ||
      (host.check_type && host.check_type.toLowerCase().includes(q)) ||
      (host.source && host.source.toLowerCase().includes(q))
    );
  });

  // Auto-redirect to host detail when ?q= yields exactly 1 match
  useEffect(() => {
    if (qParam && !isLoading && filteredHosts && filteredHosts.length === 1 && !redirected.current) {
      redirected.current = true;
      router.replace(`/hosts/${filteredHosts[0].id}`);
    }
  }, [qParam, isLoading, filteredHosts, router]);

  async function handleAdd() {
    setSaving(true);
    try {
      await post('/hosts/api/create', {
        name: form.name,
        hostname: form.hostname,
        check_type: form.check_type,
        port: form.port || undefined,
      });
      qc.invalidateQueries({ queryKey: ['hosts'] });
      setShowAdd(false);
      setForm({ name: '', hostname: '', check_type: 'icmp', port: '' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Hosts"
        description="Monitor network hosts and services"
        actions={
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                placeholder="Search hosts..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50"
              />
            </div>
            <Button size="sm" onClick={() => setShowAdd(true)}>
              <Plus size={16} />
              Add Host
            </Button>
          </div>
        }
      />

      {/* Add Host form */}
      {showAdd && (
        <GlassCard className="p-6 mb-6 border border-sky-500/20">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-slate-200">Add New Host</h3>
            <button onClick={() => setShowAdd(false)} className="text-slate-400 hover:text-slate-200">
              <X size={16} />
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">Name <span className="text-red-400">*</span></label>
              <input
                type="text"
                placeholder="My Server"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Hostname / IP <span className="text-red-400">*</span></label>
              <input
                type="text"
                placeholder="192.168.1.1 or example.com"
                value={form.hostname}
                onChange={(e) => setForm({ ...form, hostname: e.target.value })}
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Check Type</label>
              <select
                value={form.check_type}
                onChange={(e) => setForm({ ...form, check_type: e.target.value })}
                className={selectClass}
              >
                <option value="icmp">ICMP (Ping)</option>
                <option value="http">HTTP</option>
                <option value="https">HTTPS</option>
                <option value="tcp">TCP</option>
                <option value="dns">DNS</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Port (optional)</label>
              <input
                type="text"
                placeholder="443"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: e.target.value })}
                className={inputClass}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" size="sm" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button size="sm" onClick={handleAdd} disabled={saving || !form.name || !form.hostname}>
              {saving ? 'Adding...' : 'Add Host'}
            </Button>
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Type</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Source</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Latency</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Availability</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                  </tr>
                ))}
              {filteredHosts?.map((host) => (
                <tr
                  key={host.id}
                  className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors cursor-pointer"
                >
                  <td className="px-4 py-3">
                    <Link href={`/hosts/${host.id}`} className="block">
                      <p className="font-medium text-slate-200">{host.name}</p>
                      <p className="text-xs text-slate-500 font-mono">{host.hostname}</p>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <StatusDot
                        status={
                          !host.enabled ? 'disabled' :
                          host.maintenance ? 'maintenance' :
                          host.online === null ? 'unknown' :
                          host.online ? 'online' : 'offline'
                        }
                        pulse={host.online === false}
                      />
                      <span className="text-xs text-slate-400">
                        {!host.enabled ? 'Disabled' :
                         host.maintenance ? 'Maint.' :
                         host.online === null ? '—' :
                         host.online ? 'Online' : 'Offline'}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge>{host.check_type}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-slate-400">{host.source ?? 'manual'}</span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {formatLatency(host.latency_ms)}
                  </td>
                  <td className="px-4 py-3">
                    <UptimeBar h24={host.uptime_h24} d7={host.uptime_d7} d30={host.uptime_d30} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}

export default function HostsPage() {
  return (
    <Suspense>
      <HostsPageInner />
    </Suspense>
  );
}
