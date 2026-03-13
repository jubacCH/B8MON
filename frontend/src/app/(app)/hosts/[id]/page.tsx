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
import { ArrowLeft, RefreshCw } from 'lucide-react';
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

const sevLabels: Record<number, string> = {
  0: 'Emergency', 1: 'Alert', 2: 'Critical', 3: 'Error',
  4: 'Warning', 5: 'Notice', 6: 'Info', 7: 'Debug',
};
const sevColors: Record<number, string> = {
  0: 'text-red-500', 1: 'text-red-400', 2: 'text-red-400', 3: 'text-orange-400',
  4: 'text-amber-400', 5: 'text-blue-400', 6: 'text-slate-400', 7: 'text-slate-500',
};

type Tab = 'overview' | 'results' | 'syslog';

export default function HostDetailPage() {
  const params = useParams();
  const hostId = Number(params.id);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const { data: host, isLoading } = useHost(hostId);
  const { data: history, isLoading: historyLoading } = useHostHistory(hostId, 24);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'results', label: 'Check Results' },
    { key: 'syslog', label: 'Syslog' },
  ];

  const hostStatus = !host
    ? 'unknown' as const
    : !host.enabled
      ? 'disabled' as const
      : host.maintenance
        ? 'maintenance' as const
        : host.latest?.success
          ? 'online' as const
          : host.latest?.success === false
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
              {host.maintenance && <Badge variant="severity" severity="warning">Maintenance</Badge>}
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
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.h24 ?? null)}`}>
                    {host?.uptime.h24 != null ? `${host.uptime.h24.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">7d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.d7 ?? null)}`}>
                    {host?.uptime.d7 != null ? `${host.uptime.d7.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
                <GlassCard className="p-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wider">30d Uptime</p>
                  <p className={`text-2xl font-bold mt-1 ${uptimeColor(host?.uptime.d30 ?? null)}`}>
                    {host?.uptime.d30 != null ? `${host.uptime.d30.toFixed(1)}%` : '--'}
                  </p>
                </GlassCard>
              </>
            )}
          </div>

          {/* Latency */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-2">Current Latency</h3>
            {isLoading ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <p className="text-2xl font-mono text-slate-100">
                {formatLatency(host?.latest?.latency_ms ?? null)}
              </p>
            )}
          </GlassCard>

          {/* Availability overview */}
          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-3">Availability</h3>
            {isLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : (
              <div className="space-y-3">
                {([
                  { label: '24 Hours', value: host?.uptime.h24 ?? null },
                  { label: '7 Days', value: host?.uptime.d7 ?? null },
                  { label: '30 Days', value: host?.uptime.d30 ?? null },
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
            <h3 className="text-sm font-medium text-slate-300 mb-3">Latency Chart</h3>
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

      {activeTab === 'results' && (
        <GlassCard>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Timestamp</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Latency</th>
                </tr>
              </thead>
              <tbody>
                {historyLoading &&
                  Array.from({ length: 10 }).map((_, i) => (
                    <tr key={i} className="border-b border-white/[0.03]">
                      <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                      <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    </tr>
                  ))}
                {history?.results.map((r) => (
                  <tr key={r.id} className="border-b border-white/[0.03]">
                    <td className="px-4 py-3 text-xs text-slate-400 font-mono">
                      {new Date(r.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <StatusDot status={r.success ? 'online' : 'offline'} />
                        <span className="text-xs text-slate-300">{r.success ? 'OK' : 'Fail'}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {formatLatency(r.latency_ms)}
                    </td>
                  </tr>
                ))}
                {!historyLoading && (!history?.results || history.results.length === 0) && (
                  <tr>
                    <td colSpan={3} className="px-4 py-8 text-center text-sm text-slate-500">
                      No check results available
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </GlassCard>
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

    // Mark failed pings as red scatter points at y=0
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
