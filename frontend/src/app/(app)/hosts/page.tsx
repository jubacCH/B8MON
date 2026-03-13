'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Skeleton } from '@/components/ui/Skeleton';
import { useHosts } from '@/hooks/queries/useHosts';
import { formatLatency } from '@/lib/utils';
import { Plus, Search } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';

function HostsPageInner() {
  const searchParams = useSearchParams();
  const [search, setSearch] = useState(searchParams.get('q') ?? '');
  const { data: hosts, isLoading } = useHosts();

  const filteredHosts = hosts?.filter((host) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      host.name.toLowerCase().includes(q) ||
      host.hostname.toLowerCase().includes(q)
    );
  });

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
            <Button size="sm">
              <Plus size={16} />
              Add Host
            </Button>
          </div>
        }
      />

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Latency</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">24h</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">7d</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">30d</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
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
                         host.maintenance ? 'Maintenance' :
                         host.online === null ? 'No data' :
                         host.online ? 'Online' : 'Offline'}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {formatLatency(host.latency_ms)}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs">—</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs">—</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs">—</span>
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
