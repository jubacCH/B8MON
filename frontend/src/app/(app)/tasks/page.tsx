'use client';

import { useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToastStore } from '@/stores/toast';
import { get, patch, post } from '@/lib/api';
import { timeAgo } from '@/lib/utils';
import {
  Check, X, Lock, Shield, Cable, CheckCircle, ExternalLink, RefreshCw,
} from 'lucide-react';
import Link from 'next/link';

interface PortTask {
  id: number;
  host_id: number;
  host_name: string;
  host_hostname: string;
  port: number;
  protocol: string;
  service: string | null;
  status: string;
  first_seen: string | null;
  last_seen: string | null;
}

interface SslTask {
  id: number;
  host_id: number;
  host_name: string;
  host_hostname: string;
  port: number;
  protocol: string;
  service: string | null;
  ssl_issuer: string | null;
  ssl_subject: string | null;
  ssl_expiry_days: number | null;
  ssl_expiry_date: string | null;
  ssl_status: string;
  first_seen: string | null;
  last_seen: string | null;
}

interface TasksData {
  port_tasks: PortTask[];
  ssl_tasks: SslTask[];
  summary: {
    new_ports: number;
    new_ssl: number;
    total_pending: number;
  };
}

export default function TasksPage() {
  useEffect(() => { document.title = 'Tasks | Nodeglow'; }, []);

  const qc = useQueryClient();
  const toast = useToastStore();
  const { data, isLoading } = useQuery<TasksData>({
    queryKey: ['tasks'],
    queryFn: () => get('/api/tasks'),
    refetchInterval: 30_000,
  });

  const actionMut = useMutation({
    mutationFn: ({ hostId, portId, action }: { hostId: number; portId: number; action: string }) =>
      patch(`/hosts/api/${hostId}/discovered-ports/${portId}`, { action }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['nav-counts'] });
      toast.show('Updated', 'success');
    },
    onError: () => toast.show('Action failed', 'error'),
  });

  const scanAllMut = useMutation({
    mutationFn: () => post('/api/tasks/scan-all'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['nav-counts'] });
      toast.show('Port scan completed for all hosts', 'success');
    },
    onError: () => toast.show('Scan failed', 'error'),
  });

  const bulkAction = async (items: { hostId: number; portId: number }[], action: string) => {
    for (const item of items) {
      await patch(`/hosts/api/${item.hostId}/discovered-ports/${item.portId}`, { action });
    }
    qc.invalidateQueries({ queryKey: ['tasks'] });
    qc.invalidateQueries({ queryKey: ['nav-counts'] });
    toast.show(`${items.length} items updated`, 'success');
  };

  const newPorts = (data?.port_tasks ?? []).filter(p => p.status === 'new');
  const newSsl = (data?.ssl_tasks ?? []).filter(s => s.ssl_status === 'new');
  const resolvedPorts = (data?.port_tasks ?? []).filter(p => p.status !== 'new');
  const resolvedSsl = (data?.ssl_tasks ?? []).filter(s => s.ssl_status !== 'new');

  const totalPending = newPorts.length + newSsl.length;

  return (
    <div>
      <PageHeader
        title="Tasks"
        description="Items requiring admin attention"
        actions={
          <div className="flex items-center gap-3">
            {totalPending > 0 && (
              <span className="text-xs text-amber-400 font-mono">{totalPending} pending</span>
            )}
            <Button
              size="sm"
              onClick={() => scanAllMut.mutate()}
              disabled={scanAllMut.isPending}
            >
              <RefreshCw size={14} className={scanAllMut.isPending ? 'animate-spin' : ''} />
              {scanAllMut.isPending ? 'Scanning...' : 'Scan All Hosts'}
            </Button>
          </div>
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <SummaryCard
          label="New Ports"
          value={data?.summary.new_ports ?? 0}
          color="text-sky-400"
          loading={isLoading}
        />
        <SummaryCard
          label="New SSL Certs"
          value={data?.summary.new_ssl ?? 0}
          color="text-emerald-400"
          loading={isLoading}
        />
        <SummaryCard
          label="Monitored"
          value={resolvedPorts.filter(p => p.status === 'monitored').length + resolvedSsl.filter(s => s.ssl_status === 'monitored').length}
          color="text-slate-400"
          loading={isLoading}
        />
        <SummaryCard
          label="Dismissed"
          value={resolvedPorts.filter(p => p.status === 'dismissed').length + resolvedSsl.filter(s => s.ssl_status === 'dismissed').length}
          color="text-slate-500"
          loading={isLoading}
        />
      </div>

      {/* All clear */}
      {!isLoading && totalPending === 0 && (
        <GlassCard className="p-8 text-center">
          <CheckCircle size={32} className="text-emerald-400 mx-auto mb-3" />
          <p className="text-sm text-emerald-400 font-medium">All clear — no pending tasks</p>
          <p className="text-xs text-slate-500 mt-1">Port scans run automatically every 6 hours</p>
        </GlassCard>
      )}

      {/* New Ports */}
      {(isLoading || newPorts.length > 0) && (
        <GlassCard className="mb-6">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <Cable size={16} className="text-sky-400" />
              Discovered Ports
              {newPorts.length > 0 && (
                <Badge variant="severity" severity="info">{newPorts.length} new</Badge>
              )}
            </h3>
            {newPorts.length > 1 && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => bulkAction(
                    newPorts.map(p => ({ hostId: p.host_id, portId: p.id })),
                    'monitor_port'
                  )}
                >
                  <Check size={13} /> Accept All
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => bulkAction(
                    newPorts.map(p => ({ hostId: p.host_id, portId: p.id })),
                    'dismiss_port'
                  )}
                >
                  <X size={13} /> Dismiss All
                </Button>
              </div>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Port</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Service</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">First Seen</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    {Array.from({ length: 6 }).map((_, j) => (
                      <td key={j} className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    ))}
                  </tr>
                ))}
                {newPorts.map((p) => (
                  <tr key={`port-${p.id}`} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/hosts/${p.host_id}`} className="flex items-center gap-1.5 text-slate-200 hover:text-sky-400 transition-colors">
                        {p.host_name}
                        <ExternalLink size={11} className="text-slate-500" />
                      </Link>
                      <p className="text-[10px] text-slate-500 font-mono">{p.host_hostname}</p>
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono font-bold text-slate-200">{p.port}</span>
                      <span className="text-xs text-slate-500">/{p.protocol}</span>
                    </td>
                    <td className="px-4 py-3">
                      {p.service ? <Badge>{p.service}</Badge> : <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400">
                      {p.first_seen ? timeAgo(p.first_seen) : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <StatusLabel status={p.status} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {p.status === 'new' && (
                          <>
                            <button
                              onClick={() => actionMut.mutate({ hostId: p.host_id, portId: p.id, action: 'monitor_port' })}
                              disabled={actionMut.isPending}
                              className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-colors"
                              title="Monitor this port"
                            >
                              <Check size={14} />
                            </button>
                            <button
                              onClick={() => actionMut.mutate({ hostId: p.host_id, portId: p.id, action: 'dismiss_port' })}
                              disabled={actionMut.isPending}
                              className="p-1.5 rounded-md bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] transition-colors"
                              title="Dismiss"
                            >
                              <X size={14} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* New SSL Certificates */}
      {(isLoading || newSsl.length > 0) && (
        <GlassCard className="mb-6">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <Lock size={16} className="text-emerald-400" />
              Discovered SSL Certificates
              {newSsl.length > 0 && (
                <Badge variant="severity" severity="info">{newSsl.length} new</Badge>
              )}
            </h3>
            {newSsl.length > 1 && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => bulkAction(
                    newSsl.map(s => ({ hostId: s.host_id, portId: s.id })),
                    'monitor_ssl'
                  )}
                >
                  <Shield size={13} /> Accept All
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => bulkAction(
                    newSsl.map(s => ({ hostId: s.host_id, portId: s.id })),
                    'dismiss_ssl'
                  )}
                >
                  <X size={13} /> Dismiss All
                </Button>
              </div>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Port</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Subject</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Issuer</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Expiry</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    {Array.from({ length: 7 }).map((_, j) => (
                      <td key={j} className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    ))}
                  </tr>
                ))}
                {newSsl.map((s) => (
                  <tr key={`ssl-${s.id}`} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/hosts/${s.host_id}`} className="flex items-center gap-1.5 text-slate-200 hover:text-sky-400 transition-colors">
                        {s.host_name}
                        <ExternalLink size={11} className="text-slate-500" />
                      </Link>
                      <p className="text-[10px] text-slate-500 font-mono">{s.host_hostname}</p>
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-slate-300">{s.port}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-300 max-w-[200px] truncate">
                      {s.ssl_subject || '—'}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400 max-w-[200px] truncate">
                      {s.ssl_issuer || '—'}
                    </td>
                    <td className="px-4 py-3">
                      {s.ssl_expiry_days != null ? (
                        <Badge variant="severity" severity={
                          s.ssl_expiry_days <= 14 ? 'critical' : s.ssl_expiry_days <= 30 ? 'warning' : 'info'
                        }>
                          {s.ssl_expiry_days}d
                        </Badge>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <StatusLabel status={s.ssl_status} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {s.ssl_status === 'new' && (
                          <>
                            <button
                              onClick={() => actionMut.mutate({ hostId: s.host_id, portId: s.id, action: 'monitor_ssl' })}
                              disabled={actionMut.isPending}
                              className="p-1.5 rounded-md bg-sky-500/10 text-sky-400 hover:bg-sky-500/20 transition-colors"
                              title="Monitor SSL certificate"
                            >
                              <Shield size={14} />
                            </button>
                            <button
                              onClick={() => actionMut.mutate({ hostId: s.host_id, portId: s.id, action: 'dismiss_ssl' })}
                              disabled={actionMut.isPending}
                              className="p-1.5 rounded-md bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] transition-colors"
                              title="Dismiss"
                            >
                              <X size={14} />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Resolved items (collapsed) */}
      {(resolvedPorts.length > 0 || resolvedSsl.length > 0) && (
        <GlassCard>
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-semibold text-slate-200">
              History
              <span className="ml-2 text-xs font-normal text-slate-500">
                {resolvedPorts.length + resolvedSsl.length} resolved items
              </span>
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Port</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Type</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">First Seen</th>
                </tr>
              </thead>
              <tbody>
                {resolvedPorts.map((p) => (
                  <tr key={`rp-${p.id}`} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3">
                      <Link href={`/hosts/${p.host_id}`} className="text-xs text-slate-400 hover:text-sky-400 transition-colors">
                        {p.host_name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{p.port}/{p.protocol}</td>
                    <td className="px-4 py-3 text-xs text-slate-500">Port</td>
                    <td className="px-4 py-3"><StatusLabel status={p.status} /></td>
                    <td className="px-4 py-3 text-xs text-slate-500">{p.first_seen ? timeAgo(p.first_seen) : '—'}</td>
                  </tr>
                ))}
                {resolvedSsl.map((s) => (
                  <tr key={`rs-${s.id}`} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3">
                      <Link href={`/hosts/${s.host_id}`} className="text-xs text-slate-400 hover:text-sky-400 transition-colors">
                        {s.host_name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{s.port}/{s.protocol}</td>
                    <td className="px-4 py-3 text-xs text-slate-500">SSL Cert</td>
                    <td className="px-4 py-3"><StatusLabel status={s.ssl_status} /></td>
                    <td className="px-4 py-3 text-xs text-slate-500">{s.first_seen ? timeAgo(s.first_seen) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}

function SummaryCard({ label, value, color, loading }: {
  label: string; value: number; color: string; loading: boolean;
}) {
  return (
    <GlassCard className="p-4">
      <p className="text-[10px] text-slate-500 uppercase tracking-wider font-medium">{label}</p>
      {loading ? (
        <Skeleton className="h-8 w-12 mt-1" />
      ) : (
        <p className={`text-2xl font-bold ${color}`}>{value}</p>
      )}
    </GlassCard>
  );
}

function StatusLabel({ status }: { status: string }) {
  if (status === 'new') return <Badge variant="severity" severity="warning">new</Badge>;
  if (status === 'monitored') return <span className="text-xs text-emerald-400 flex items-center gap-1"><Check size={12} /> monitored</span>;
  if (status === 'dismissed') return <span className="text-xs text-slate-500">dismissed</span>;
  return <span className="text-xs text-slate-500">{status}</span>;
}
