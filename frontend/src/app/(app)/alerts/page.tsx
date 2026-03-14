'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useIncidents } from '@/hooks/queries/useAlerts';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Wrench, Clock, ShieldCheck, Bell, Search } from 'lucide-react';
import { timeAgo } from '@/lib/utils';
import { useConfirm } from '@/hooks/useConfirm';

interface MaintenanceHost {
  id: number;
  name: string;
  hostname: string;
  source: string;
  maintenance: boolean;
  maintenance_until?: string | null;
}

type Tab = 'alerts' | 'incidents' | 'maintenance';

export default function AlertsPage() {
  useEffect(() => { document.title = 'Alerts | Nodeglow'; }, []);
  const [activeTab, setActiveTab] = useState<Tab>('alerts');
  const [incidentSearch, setIncidentSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();
  const { data: incidents, isLoading } = useIncidents();

  const { data: maintHosts, isLoading: maintLoading } = useQuery({
    queryKey: ['maintenance-hosts'],
    queryFn: () => get<MaintenanceHost[]>('/api/v1/hosts?status=maintenance'),
    enabled: activeTab === 'maintenance',
  });

  const tabs: { key: Tab; label: string }[] = [
    { key: 'alerts', label: 'Alerts' },
    { key: 'incidents', label: 'Incidents' },
    { key: 'maintenance', label: 'Maintenance' },
  ];

  const openIncidents = incidents?.filter((i) => i.status === 'open') ?? [];

  async function removeMaintenance(hostId: number) {
    const ok = await confirm({
      title: 'Remove maintenance',
      description: 'Remove maintenance mode from this host?',
      confirmLabel: 'Remove',
    });
    if (!ok) return;
    await post(`/hosts/api/${hostId}/maintenance`);
    qc.invalidateQueries({ queryKey: ['maintenance-hosts'] });
  }

  return (
    <div>
      <PageHeader
        title="Alerts"
        description="Active alerts, incidents, and maintenance windows"
      />

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'accent-text border-b-2 border-current'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
            {tab.key === 'alerts' && openIncidents.length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-xs bg-red-500/20 text-red-400">
                {openIncidents.length}
              </span>
            )}
            {tab.key === 'maintenance' && maintHosts && maintHosts.length > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-xs bg-amber-500/20 text-amber-400">
                {maintHosts.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {activeTab === 'alerts' && (
        <div className="space-y-3">
          {isLoading &&
            Array.from({ length: 4 }).map((_, i) => (
              <GlassCard key={i} className="p-4">
                <div className="flex items-center gap-3">
                  <Skeleton className="h-5 w-5 rounded-full" />
                  <Skeleton className="h-5 w-64" />
                  <Skeleton className="h-5 w-16 ml-auto" />
                </div>
              </GlassCard>
            ))}
          {openIncidents.map((inc) => (
            <Link key={inc.id} href={`/incidents/${inc.id}`}>
              <GlassCard className="p-4 hover:bg-white/[0.04] transition-colors cursor-pointer">
                <div className="flex items-center gap-3">
                  <StatusDot
                    status={inc.severity === 'critical' ? 'offline' : inc.severity === 'warning' ? 'maintenance' : 'unknown'}
                    pulse={inc.severity === 'critical'}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-200 truncate">{inc.title}</p>
                    {inc.summary && (
                      <p className="text-xs text-slate-400 truncate mt-0.5">{inc.summary}</p>
                    )}
                    <p className="text-xs text-slate-500 mt-0.5">{timeAgo(inc.created_at)}</p>
                  </div>
                  <Badge variant="severity" severity={inc.severity}>{inc.severity}</Badge>
                </div>
              </GlassCard>
            </Link>
          ))}
          {!isLoading && openIncidents.length === 0 && (
            <GlassCard className="p-12">
              <div className="flex flex-col items-center gap-3">
                <ShieldCheck size={48} className="text-emerald-500/60" />
                <p className="text-base font-semibold text-slate-300">All clear</p>
                <p className="text-sm text-slate-500">No active alerts — everything is running smoothly.</p>
              </div>
            </GlassCard>
          )}
        </div>
      )}

      {activeTab === 'incidents' && (() => {
        const filtered = (incidents ?? []).filter((inc) => {
          if (severityFilter !== 'all' && inc.severity !== severityFilter) return false;
          if (statusFilter !== 'all' && inc.status !== statusFilter) return false;
          if (incidentSearch) {
            const q = incidentSearch.toLowerCase();
            if (!inc.title.toLowerCase().includes(q) && !inc.rule.toLowerCase().includes(q)) return false;
          }
          return true;
        });
        return (
          <div className="space-y-4">
            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
              <div className="relative flex-1 min-w-[200px] max-w-sm">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  placeholder="Search incidents..."
                  value={incidentSearch}
                  onChange={(e) => setIncidentSearch(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50"
                />
              </div>
              <div className="flex gap-1">
                {['all', 'critical', 'warning', 'info'].map((s) => (
                  <button
                    key={s}
                    onClick={() => setSeverityFilter(s)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                      severityFilter === s
                        ? s === 'critical' ? 'bg-red-500/20 text-red-400 border border-red-500/40'
                          : s === 'warning' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                          : s === 'info' ? 'bg-blue-500/20 text-blue-400 border border-blue-500/40'
                          : 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                        : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                    }`}
                  >
                    {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                  </button>
                ))}
              </div>
              <div className="flex gap-1">
                {['all', 'open', 'acknowledged', 'resolved'].map((s) => (
                  <button
                    key={s}
                    onClick={() => setStatusFilter(s)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                      statusFilter === s
                        ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                        : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
                    }`}
                  >
                    {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            {/* Results */}
            <div className="space-y-3">
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <GlassCard key={i} className="p-4">
                    <Skeleton className="h-5 w-full" />
                  </GlassCard>
                ))}
              {filtered.map((inc) => (
                <Link key={inc.id} href={`/incidents/${inc.id}`}>
                  <GlassCard className="p-4 hover:bg-white/[0.04] transition-colors cursor-pointer">
                    <div className="flex items-center gap-3">
                      <StatusDot
                        status={
                          inc.status === 'resolved' ? 'online' :
                          inc.status === 'acknowledged' ? 'maintenance' : 'offline'
                        }
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-slate-200 truncate">{inc.title}</p>
                        {inc.summary && (
                          <p className="text-xs text-slate-400 truncate mt-0.5">{inc.summary}</p>
                        )}
                        <p className="text-xs text-slate-500 mt-0.5">
                          {inc.rule} &middot; {timeAgo(inc.created_at)}
                        </p>
                      </div>
                      <Badge variant="severity" severity={inc.severity}>{inc.severity}</Badge>
                      <Badge>{inc.status}</Badge>
                    </div>
                  </GlassCard>
                </Link>
              ))}
              {!isLoading && filtered.length === 0 && (
                <GlassCard className="p-12">
                  <div className="flex flex-col items-center gap-3">
                    <Bell size={48} className="text-slate-600" />
                    <p className="text-base font-semibold text-slate-300">
                      {incidentSearch || severityFilter !== 'all' || statusFilter !== 'all'
                        ? 'No incidents match your filters'
                        : 'No incidents yet'}
                    </p>
                    <p className="text-sm text-slate-500">
                      {incidentSearch || severityFilter !== 'all' || statusFilter !== 'all'
                        ? 'Try adjusting your filters.'
                        : 'Incidents from correlation rules will appear here.'}
                    </p>
                  </div>
                </GlassCard>
              )}
            </div>
          </div>
        );
      })()}

      {activeTab === 'maintenance' && (
        <div className="space-y-3">
          {maintLoading &&
            Array.from({ length: 3 }).map((_, i) => (
              <GlassCard key={i} className="p-4">
                <Skeleton className="h-5 w-full" />
              </GlassCard>
            ))}
          {maintHosts?.map((h) => (
            <GlassCard key={h.id} className="p-4">
              <div className="flex items-center gap-3">
                <Wrench className="h-4 w-4 text-amber-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <Link href={`/hosts/${h.id}`} className="text-sm font-medium text-slate-200 hover:text-sky-400 transition-colors">
                    {h.name}
                  </Link>
                  <p className="text-xs text-slate-500 font-mono">{h.hostname}</p>
                </div>
                {h.maintenance_until && (
                  <span className="flex items-center gap-1 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    Until {new Date(h.maintenance_until).toLocaleString()}
                  </span>
                )}
                <Badge>{h.source}</Badge>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeMaintenance(h.id)}
                  className="text-xs text-amber-400 hover:text-amber-300"
                >
                  Remove
                </Button>
              </div>
            </GlassCard>
          ))}
          {!maintLoading && (!maintHosts || maintHosts.length === 0) && (
            <GlassCard className="p-12">
              <div className="flex flex-col items-center gap-3">
                <Wrench size={48} className="text-slate-600" />
                <p className="text-base font-semibold text-slate-300">No maintenance windows</p>
                <p className="text-sm text-slate-500">Hosts in maintenance mode will appear here.</p>
              </div>
            </GlassCard>
          )}
        </div>
      )}
      {ConfirmDialogElement}
    </div>
  );
}
