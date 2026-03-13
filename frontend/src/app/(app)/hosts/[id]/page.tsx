'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useHost, useHostHistory } from '@/hooks/queries/useHosts';
import { formatLatency, uptimeColor } from '@/lib/utils';
import { EChart } from '@/components/charts/EChart';
import { ArrowLeft, RefreshCw, Cpu, MemoryStick, HardDrive, Clock } from 'lucide-react';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { get } from '@/lib/api';
import type { EChartsOption } from 'echarts';

interface SyslogEntry {
  timestamp: string;
  severity: number;
  hostname: string;
  source_ip: string;
  app_name: string;
  message: string;
}

interface AgentMetrics {
  agent_id: number;
  agent_name: string;
  platform: string | null;
  arch: string | null;
  agent_version: string | null;
  last_seen: string | null;
  cpu_pct: number | null;
  mem_pct: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  disk_pct: number | null;
  uptime_s: number | null;
  snapshot_time: string | null;
}

const sevLabels: Record<number, string> = {
  0: 'Emergency', 1: 'Alert', 2: 'Critical', 3: 'Error',
  4: 'Warning', 5: 'Notice', 6: 'Info', 7: 'Debug',
};
const sevColors: Record<number, string> = {
  0: 'text-red-500', 1: 'text-red-400', 2: 'text-red-400', 3: 'text-orange-400',
  4: 'text-amber-400', 5: 'text-blue-400', 6: 'text-slate-400', 7: 'text-slate-500',
};

type Tab = 'overview' | 'syslog';

function pctColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function pctTextColor(pct: number): string {
  if (pct >= 90) return 'text-red-400';
  if (pct >= 75) return 'text-amber-400';
  return 'text-emerald-400';
}

function formatUptime(seconds: number | null): string {
  if (!seconds || seconds <= 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function MetricCard({ icon: Icon, label, value, pct, sub, color }: {
  icon: React.ElementType;
  label: string;
  value: string;
  pct?: number | null;
  sub?: string;
  color: string;
}) {
  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={14} className={color} />
        <span className="text-xs text-slate-500 uppercase tracking-wider">{label}</span>
      </div>
      <p className={`text-2xl font-bold ${pct != null ? pctTextColor(pct) : 'text-slate-100'}`}>
        {value}
      </p>
      {pct != null && (
        <div className="mt-2 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
          <div className={`h-full rounded-full ${pctColor(pct)}`} style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
      )}
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </GlassCard>
  );
}

export default function HostDetailPage() {
  const params = useParams();
  const hostId = Number(params.id);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { data: host, isLoading } = useHost(hostId) as { data: any; isLoading: boolean };
  const { data: history, isLoading: historyLoading } = useHostHistory(hostId, 24);

  const agent: AgentMetrics | null = host?.agent ?? null;

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'syslog', label: 'Syslog' },
  ];

  const hostStatus = !host
    ? 'unknown' as const
    : !host.enabled
      ? 'disabled' as const
      : host.maintenance
        ? 'maintenance' as const
        : host.latest?.online === true
          ? 'online' as const
          : host.latest?.online === false
            ? 'offline' as const
            : 'unknown' as const;

  return (
    <div>
      <PageHeader
        title={isLoading ? 'Loading...' : (host?.name ?? 'Host')}
        description={host?.hostname}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/hosts">
              <Button variant="ghost" size="sm">
                <ArrowLeft size={16} />
                Back
              </Button>
            </Link>
            <Button variant="ghost" size="sm">
              <RefreshCw size={16} />
            </Button>
          </div>
        }
      />

      {/* Host header card */}
      <GlassCard className="p-4 mb-6">
        {isLoading ? (
          <div className="flex items-center gap-4">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="flex-1">
              <Skeleton className="h-5 w-48 mb-2" />
              <Skeleton className="h-4 w-32" />
            </div>
          </div>
        ) : host ? (
          <div className="flex items-center gap-4">
            <StatusDot status={hostStatus} pulse={hostStatus === 'offline'} className="w-4 h-4" />
            <div className="flex-1">
              <p className="text-lg font-semibold text-slate-100">{host.name}</p>
              <p className="text-sm text-slate-400 font-mono">{host.hostname}</p>
            </div>
            <div className="flex items-center gap-3">
              <Badge>{host.check_type}</Badge>
              <Badge>{host.source}</Badge>
              {host.port && <Badge>:{host.port}</Badge>}
              {host.maintenance && <Badge variant="severity" severity="warning">Maintenance</Badge>}
              {host.ssl_expiry_days != null && (
                <Badge variant="severity" severity={host.ssl_expiry_days <= 14 ? 'critical' : host.ssl_expiry_days <= 30 ? 'warning' : 'info'}>
                  SSL: {host.ssl_expiry_days}d
                </Badge>
              )}
            </div>
          </div>
        ) : (
          <p className="text-slate-400">Host not found</p>
        )}
      </GlassCard>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.06] mb-6">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-sky-400 border-b-2 border-sky-400'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Agent Metrics (CPU / RAM / Disk / Uptime) */}
          {agent && (
            <div>
              <h3 className="text-sm font-medium text-slate-300 mb-3">System Metrics</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <MetricCard
                  icon={Cpu}
                  label="CPU"
                  value={agent.cpu_pct != null ? `${agent.cpu_pct.toFixed(1)}%` : '—'}
                  pct={agent.cpu_pct}
                  color="text-sky-400"
                />
                <MetricCard
                  icon={MemoryStick}
                  label="Memory"
                  value={agent.mem_pct != null ? `${agent.mem_pct.toFixed(1)}%` : '—'}
                  pct={agent.mem_pct}
                  sub={agent.mem_used_mb != null && agent.mem_total_mb != null
                    ? `${(agent.mem_used_mb / 1024).toFixed(1)} / ${(agent.mem_total_mb / 1024).toFixed(1)} GB`
                    : undefined}
                  color="text-violet-400"
                />
                <MetricCard
                  icon={HardDrive}
                  label="Disk"
                  value={agent.disk_pct != null ? `${agent.disk_pct.toFixed(1)}%` : '—'}
                  pct={agent.disk_pct}
                  color="text-amber-400"
                />
                <MetricCard
                  icon={Clock}
                  label="System Uptime"
                  value={formatUptime(agent.uptime_s)}
                  color="text-emerald-400"
                />
              </div>
            </div>
          )}

          {/* Agent Info */}
          {agent && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">Agent Info</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-400">Platform</span>
                  <span className="text-sm text-slate-200">{agent.platform || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-400">Arch</span>
                  <span className="text-sm text-slate-200">{agent.arch || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-400">Version</span>
                  <span className="text-sm text-slate-200">{agent.agent_version || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-400">Last Seen</span>
                  <span className="text-sm text-slate-200">
                    {agent.last_seen ? new Date(agent.last_seen).toLocaleString() : '—'}
                  </span>
                </div>
              </div>
            </GlassCard>
          )}

          {/* Uptime stats */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {isLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <GlassCard key={i} className="p-4">
                  <Skeleton className="h-4 w-12 mb-2" />
                  <Skeleton className="h-8 w-20" />
                </GlassCard>
              ))
            ) : (
              <>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">24h Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime?.h24 ?? null)}`}>
                    {host?.uptime?.h24 != null ? `${host.uptime.h24.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">7d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime?.d7 ?? null)}`}>
                    {host?.uptime?.d7 != null ? `${host.uptime.d7.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">30d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime?.d30 ?? null)}`}>
                    {host?.uptime?.d30 != null ? `${host.uptime.d30.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
              </>
            )}
          </div>

          {/* Latency + Availability */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-2">Current Latency</h3>
              {isLoading ? (
                <Skeleton className="h-8 w-24" />
              ) : (
                <p className="text-2xl font-mono text-slate-100">
                  {formatLatency(host?.latest?.latency_ms ?? null)}
                </p>
              )}
              {host?.latest?.timestamp && (
                <p className="text-xs text-slate-500 mt-1">
                  Last check: {new Date(host.latest.timestamp).toLocaleString()}
                </p>
              )}
            </GlassCard>

            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">Host Details</h3>
              <div className="space-y-1.5">
                {host?.mac_address && (
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">MAC</span>
                    <span className="text-slate-200 font-mono">{host.mac_address}</span>
                  </div>
                )}
                {host?.source_detail && (
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">Source</span>
                    <span className="text-slate-200">{host.source_detail}</span>
                  </div>
                )}
                {host?.created_at && (
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">Created</span>
                    <span className="text-slate-200">{new Date(host.created_at).toLocaleDateString()}</span>
                  </div>
                )}
                {host?.latency_threshold_ms != null && (
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-400">Threshold</span>
                    <span className="text-slate-200">{host.latency_threshold_ms} ms</span>
                  </div>
                )}
              </div>
            </GlassCard>
          </div>

          {/* Availability overview */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Availability</h3>
            {isLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              <div className="space-y-3">
                {([
                  { label: '24 Hours', value: host?.uptime?.h24 ?? null },
                  { label: '7 Days', value: host?.uptime?.d7 ?? null },
                  { label: '30 Days', value: host?.uptime?.d30 ?? null },
                ] as const).map(({ label, value }) => (
                  <div key={label}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-slate-400">{label}</span>
                      <span className={`text-xs font-mono ${uptimeColor(value)}`}>
                        {value != null ? `${value.toFixed(2)}%` : '--'}
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-slate-800/60 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          value === null
                            ? 'bg-slate-700'
                            : value >= 99.9
                              ? 'bg-emerald-500'
                              : value >= 95
                                ? 'bg-amber-500'
                                : 'bg-red-500'
                        }`}
                        style={{ width: value != null ? `${Math.max(value, 1)}%` : '0%' }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>

          {/* Latency chart */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Latency Chart (24h)</h3>
            {historyLoading ? (
              <Skeleton className="h-52 w-full" />
            ) : history?.results && history.results.length > 0 ? (
              <LatencyChart results={history.results} />
            ) : (
              <p className="text-sm text-slate-500 text-center py-8">No latency data available</p>
            )}
          </GlassCard>
        </div>
      )}

      {activeTab === 'syslog' && (
        <HostSyslog hostId={hostId} />
      )}
    </div>
  );
}

/* ── Host Syslog sub-component ── */

function HostSyslog({ hostId }: { hostId: number }) {
  const { data: logs, isLoading: syslogLoading } = useQuery({
    queryKey: ['host-syslog', hostId],
    queryFn: () => get<SyslogEntry[]>(`/api/v1/syslog?host_id=${hostId}&limit=100&hours=168`),
    enabled: hostId > 0,
  });

  return (
    <GlassCard>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Time</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Severity</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">App</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Message</th>
            </tr>
          </thead>
          <tbody>
            {syslogLoading &&
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i} className="border-b border-white/[0.03]">
                  <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-full" /></td>
                </tr>
              ))}
            {logs?.map((entry, i) => (
              <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                <td className="px-4 py-3 text-xs text-slate-400 font-mono whitespace-nowrap">
                  {new Date(entry.timestamp).toLocaleString()}
                </td>
                <td className="px-4 py-3">
                  <span className={`text-xs font-medium ${sevColors[entry.severity] ?? 'text-slate-400'}`}>
                    {sevLabels[entry.severity] ?? entry.severity}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">{entry.app_name || '—'}</td>
                <td className="px-4 py-3 text-xs text-slate-300 max-w-lg truncate">{entry.message}</td>
              </tr>
            ))}
            {!syslogLoading && (!logs || logs.length === 0) && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                  No syslog entries for this host
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

/* ── Latency Chart sub-component ── */

interface HistoryResult {
  timestamp: string;
  success: boolean;
  latency_ms: number | null;
}

function LatencyChart({ results }: { results: HistoryResult[] }) {
  const option = useMemo<EChartsOption>(() => {
    const sorted = [...results].sort(
      (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
    );

    const timestamps = sorted.map((r) => r.timestamp);
    const latencies = sorted.map((r) => r.latency_ms);

    const failPoints = sorted
      .map((r, i) => (!r.success ? [i, 0] : null))
      .filter(Boolean);

    return {
      grid: { left: 48, right: 16, top: 12, bottom: 32 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        textStyle: { color: '#cbd5e1', fontSize: 12 },
        formatter(params: unknown) {
          const list = params as Array<{ dataIndex: number; value: number | null; seriesName: string }>;
          const idx = list[0]?.dataIndex;
          if (idx == null) return '';
          const r = sorted[idx];
          const time = new Date(r.timestamp).toLocaleString();
          const lat = r.latency_ms != null ? `${r.latency_ms.toFixed(1)} ms` : '--';
          const status = r.success ? '<span style="color:#34d399">OK</span>' : '<span style="color:#f87171">FAIL</span>';
          return `${time}<br/>Latency: ${lat}<br/>Status: ${status}`;
        },
      },
      xAxis: {
        type: 'category',
        data: timestamps,
        axisLabel: {
          formatter(val: string) {
            const d = new Date(val);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
          },
          color: '#64748b',
          fontSize: 10,
        },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        name: 'ms',
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        axisLabel: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
      },
      series: [
        {
          name: 'Latency',
          type: 'line',
          data: latencies,
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 2, color: '#38bdf8' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(56, 189, 248, 0.25)' },
                { offset: 1, color: 'rgba(56, 189, 248, 0.02)' },
              ],
            },
          },
        },
        ...(failPoints.length > 0
          ? [
              {
                name: 'Failed',
                type: 'scatter' as const,
                data: failPoints,
                symbol: 'circle',
                symbolSize: 8,
                itemStyle: { color: '#f87171' },
                z: 10,
              },
            ]
          : []),
      ],
    };
  }, [results]);

  return <EChart option={option} height={220} />;
}
