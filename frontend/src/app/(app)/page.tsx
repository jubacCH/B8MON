'use client';

import { useEffect, useMemo, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { AnimatedCounter } from '@/components/data/AnimatedCounter';
import { HeatmapGrid } from '@/components/charts/HeatmapGrid';
import { EChart } from '@/components/charts/EChart';
import { DraggableDashboard, type WidgetDef } from '@/components/dashboard/DraggableDashboard';
import { GravityWidget } from '@/components/dashboard/GravityWidget';
import { useDashboard } from '@/hooks/queries/useDashboard';
import { useSSE } from '@/hooks/useSSE';
import type { SyslogMessage } from '@/types';
import { formatLatency } from '@/lib/utils';
import {
  Server, ServerOff, Gauge, ShieldAlert, Zap, Clock,
  ArrowUpDown, HardDrive, Activity, AlertTriangle,
  Container, BatteryCharging, Lock, Trophy, Timer,
  TrendingUp, Wifi, Radio,
} from 'lucide-react';
import Link from 'next/link';

export default function DashboardPage() {
  useEffect(() => { document.title = 'Dashboard | Nodeglow'; }, []);
  const { data, isLoading } = useDashboard();

  const avgLatency = data?.host_stats
    ? Math.round(
        data.host_stats
          .filter((h) => h.latency !== null && h.online)
          .reduce((sum, h) => sum + (h.latency ?? 0), 0) /
          (data.host_stats.filter((h) => h.latency !== null && h.online).length || 1),
      )
    : 0;

  const widgets = useMemo<WidgetDef[]>(() => {
    const w: WidgetDef[] = [];

    // ── Hosts ──
    w.push({
      id: 'hosts',
      title: 'Hosts',
      defaultLayout: { x: 0, y: 0, w: 1, h: 4, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <Server size={16} className="text-sky-400" /> Hosts
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-0.5 max-h-[340px] overflow-y-auto">
              {data?.host_stats?.slice(0, 20).map((h) => (
                <Link
                  key={h.host.id}
                  href={`/hosts/${h.host.id}`}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={h.host.maintenance ? 'maintenance' : h.online === null ? 'unknown' : h.online === false ? 'offline' : h.host.port_error ? 'error' : 'online'} />
                  <span className="flex-1 text-sm text-slate-200 truncate">{h.host.name}</span>
                  <span className="text-xs font-mono text-slate-500">
                    {formatLatency(h.latency)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </>
      ),
    });

    // ── Integrations ──
    w.push({
      id: 'integrations',
      title: 'Integrations',
      defaultLayout: { x: 1, y: 0, w: 1, h: 4, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <Zap size={16} className="text-violet-400" /> Integrations
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-0.5 max-h-[340px] overflow-y-auto">
              {data?.integration_health?.map((int, i) => (
                <Link
                  key={i}
                  href={int.single_instance ? `/integration/${int.type}` : `/integration/${int.type}/${int.config_id}`}
                  className="flex items-center gap-3 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={int.ok ? 'online' : int.ok === false ? 'offline' : 'unknown'} />
                  <span className="flex-1 text-sm truncate" style={{ color: int.color }}>
                    {int.label}
                  </span>
                  <span className="text-xs text-slate-500 truncate max-w-[100px]">{int.name}</span>
                </Link>
              ))}
            </div>
          )}
        </>
      ),
    });

    // ── Incidents ──
    w.push({
      id: 'incidents',
      title: 'Incidents',
      defaultLayout: { x: 2, y: 0, w: 1, h: 4, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <ShieldAlert size={16} className="text-red-400" /> Recent Incidents
          </h3>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : data?.recent_incidents?.length ? (
            <div className="space-y-1.5 max-h-[340px] overflow-y-auto">
              {data.recent_incidents.slice(0, 10).map((inc) => (
                <Link
                  key={inc.id}
                  href={`/incidents/${inc.id}`}
                  className="flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/[0.04] transition-colors"
                >
                  <Badge variant="severity" severity={inc.severity as 'critical' | 'warning' | 'info'}>
                    {inc.severity}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <span className="text-sm text-slate-200 truncate block">{inc.title}</span>
                    {inc.summary && (
                      <span className="text-xs text-slate-400 truncate block">{inc.summary}</span>
                    )}
                  </div>
                  <Badge>{inc.status}</Badge>
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
              All clear — no active incidents
            </p>
          )}
        </>
      ),
    });

    // ── Syslog Rate ──
    w.push({
      id: 'syslog-rate',
      title: 'Syslog Rate',
      defaultLayout: { x: 0, y: 4, w: 2, h: 3, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <ArrowUpDown size={16} className="text-sky-400" /> Syslog Rate (24h)
            {data?.syslog_stats && (
              <span className="ml-auto text-xs text-slate-500 font-mono">
                {data.syslog_stats.total_24h.toLocaleString()} total
              </span>
            )}
          </h3>
          {isLoading || !data?.syslog_stats?.rate_data ? (
            <Skeleton className="h-[180px] w-full" />
          ) : (
            <EChart
              height={180}
              option={{
                tooltip: { trigger: 'axis' },
                legend: { show: false },
                grid: { left: 40, right: 12, top: 8, bottom: 24 },
                xAxis: { type: 'category', data: data.syslog_stats.rate_data.labels, axisLabel: { fontSize: 10 } },
                yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
                series: [
                  { name: 'Errors', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.errors, color: '#F87171' },
                  { name: 'Warnings', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.warnings, color: '#FBBF24' },
                  { name: 'Info', type: 'bar', stack: 'total', data: data.syslog_stats.rate_data.info, color: '#38BDF8' },
                ],
              }}
            />
          )}
        </>
      ),
    });

    // ── Anomalies & Warnings ──
    const anomalyCount = (data?.anomalies?.length ?? 0) + (data?.warnings?.length ?? 0);
    if (anomalyCount > 0 || !data) {
      w.push({
        id: 'anomalies',
        title: 'Anomalies',
        defaultLayout: { x: 2, y: 4, w: 1, h: 3, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <AlertTriangle size={16} className="text-amber-400" /> Anomalies
              {anomalyCount > 0 && (
                <span className="ml-auto text-xs font-mono text-amber-400">{anomalyCount}</span>
              )}
            </h3>
            {isLoading ? (
              <Skeleton className="h-[200px] w-full" />
            ) : anomalyCount === 0 ? (
              <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
                No anomalies detected
              </p>
            ) : (
              <div className="space-y-1.5 max-h-[240px] overflow-y-auto">
                {data?.anomalies?.slice(0, 8).map((a, i) => (
                  <div key={`a-${i}`} className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-white/[0.02]">
                    <span className="text-xs text-red-400 font-mono shrink-0">{a.metric}</span>
                    <span className="flex-1 text-sm text-slate-200 truncate">{a.name}</span>
                    <span className="text-xs font-mono text-slate-400">
                      {typeof a.current === 'number' ? (a.metric === 'Latency' ? `${Math.round(a.current)}ms` : a.metric === 'RAM' ? `${a.current}GB` : `${a.current}%`) : ''}
                    </span>
                    {a.factor != null && (
                      <span className="text-[10px] text-amber-400">{a.factor}x</span>
                    )}
                  </div>
                ))}
                {data?.warnings?.slice(0, 5).map((w, i) => (
                  <div key={`w-${i}`} className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-white/[0.02]">
                    <span className="text-xs text-amber-400 font-mono shrink-0">{w.metric}</span>
                    <span className="flex-1 text-sm text-slate-200 truncate">{w.name}</span>
                    <span className="text-xs font-mono text-slate-400">
                      {w.current}% &ge; {w.threshold}%
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        ),
      });
    }

    // ── 30-Day Heatmap ──
    w.push({
      id: 'heatmap',
      title: '30-Day Availability',
      defaultLayout: { x: 0, y: 7, w: 3, h: 3, minW: 2, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <Activity size={16} className="text-sky-400" /> 30-Day Availability
          </h3>
          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : data?.heatmap_data ? (
            <HeatmapGrid data={data.heatmap_data} days={data.heatmap_days} />
          ) : (
            <p className="text-sm text-slate-500">No data</p>
          )}
        </>
      ),
    });

    // ── Uptime Ranking ──
    w.push({
      id: 'uptime',
      title: 'Uptime Ranking',
      defaultLayout: { x: 0, y: 10, w: 1, h: 3, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <Trophy size={16} className="text-amber-400" /> Uptime Ranking
          </h3>
          {isLoading ? (
            <Skeleton className="h-[200px] w-full" />
          ) : data?.uptime_ranking?.length ? (
            <div className="space-y-1 max-h-[260px] overflow-y-auto">
              {data.uptime_ranking.map((h, i) => {
                const pct = h.uptime;
                const color = pct >= 99.9 ? 'text-emerald-400' : pct >= 95 ? 'text-amber-400' : 'text-red-400';
                const barColor = pct >= 99.9 ? 'bg-emerald-500/30' : pct >= 95 ? 'bg-amber-500/30' : 'bg-red-500/30';
                return (
                  <Link
                    key={h.host_id}
                    href={`/hosts/${h.host_id}`}
                    className="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-white/[0.04] transition-colors relative overflow-hidden"
                  >
                    <div className={`absolute inset-0 ${barColor}`} style={{ width: `${pct}%` }} />
                    <span className="relative text-xs text-slate-500 w-4 text-right">{i + 1}</span>
                    <span className="relative flex-1 text-sm text-slate-200 truncate">{h.name}</span>
                    <span className={`relative text-xs font-mono ${color}`}>{pct.toFixed(1)}%</span>
                  </Link>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-500 text-center py-4">No data</p>
          )}
        </>
      ),
    });

    // ── Top Latency ──
    w.push({
      id: 'top-latency',
      title: 'Top Latency',
      defaultLayout: { x: 1, y: 10, w: 1, h: 3, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <Timer size={16} className="text-rose-400" /> Highest Latency
          </h3>
          {isLoading ? (
            <Skeleton className="h-[200px] w-full" />
          ) : data?.top_latency?.length ? (
            <div className="space-y-1 max-h-[260px] overflow-y-auto">
              {data.top_latency.map((h, i) => {
                const color = h.value > 200 ? 'text-red-400' : h.value > 100 ? 'text-amber-400' : 'text-slate-400';
                return (
                  <Link
                    key={h.id}
                    href={`/hosts/${h.id}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                  >
                    <span className="text-xs text-slate-500 w-4 text-right">{i + 1}</span>
                    <span className="flex-1 text-sm text-slate-200 truncate">{h.name}</span>
                    <span className={`text-xs font-mono ${color}`}>{h.value.toFixed(1)}ms</span>
                  </Link>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-500 text-center py-4">No data</p>
          )}
        </>
      ),
    });

    // ── Storage ──
    if (data?.storage_pools?.length) {
      w.push({
        id: 'storage',
        title: 'Storage',
        defaultLayout: { x: 2, y: 10, w: 1, h: 3, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <HardDrive size={16} className="text-sky-400" /> Storage
            </h3>
            <div className="space-y-3 max-h-[260px] overflow-y-auto">
              {data!.storage_pools.map((pool, i) => (
                <div key={i}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`w-1.5 h-1.5 rounded-full ${pool.healthy ? 'bg-emerald-400' : 'bg-red-400'}`} />
                      <span className="text-xs text-slate-300 truncate">{pool.name}</span>
                    </div>
                    <span className="text-xs font-mono text-slate-400">
                      {pool.pct.toFixed(0)}%
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(pool.pct, 100)}%`,
                        background: pool.pct >= 90 ? '#F87171' : pool.pct >= 75 ? '#FBBF24' : '#34D399',
                      }}
                    />
                  </div>
                  <div className="flex items-center justify-between mt-0.5">
                    <span className="text-[10px] text-slate-600">{pool.source}</span>
                    {pool.days_until_full != null && pool.days_until_full < 90 && (
                      <span className="text-[10px] text-amber-400">
                        ~{pool.days_until_full}d until full
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </>
        ),
      });
    }

    // ── Speedtest ──
    if (data?.speedtest_data) {
      w.push({
        id: 'speedtest',
        title: 'Speedtest',
        defaultLayout: { x: 0, y: 13, w: 1, h: 2, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <Wifi size={16} className="text-blue-400" /> Speedtest
            </h3>
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <p className="text-2xl font-bold text-sky-400">{Math.round(data!.speedtest_data!.download_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Down Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-violet-400">{Math.round(data!.speedtest_data!.upload_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Up Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-emerald-400">{Math.round(data!.speedtest_data!.ping_ms)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Ping ms</p>
              </div>
            </div>
            <div className="flex items-center justify-between mt-2">
              <p className="text-[10px] text-slate-600">{data!.speedtest_data!.server_name}</p>
              <p className="text-[10px] text-slate-600">{data!.speedtest_data!.timestamp}</p>
            </div>
          </>
        ),
      });
    }

    // ── UPS ──
    if (data?.ups_data) {
      w.push({
        id: 'ups',
        title: 'UPS',
        defaultLayout: { x: 1, y: 13, w: 1, h: 2, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <BatteryCharging size={16} className={data!.ups_data!.on_battery ? 'text-amber-400' : 'text-emerald-400'} />
              UPS
              {data!.ups_data!.on_battery && (
                <Badge variant="severity" severity="warning">On Battery</Badge>
              )}
            </h3>
            <div className="space-y-3">
              {data!.ups_data!.units.map((unit, i) => (
                <div key={i} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-slate-300">{unit.name}</span>
                    <span className={`text-xs font-mono ${unit.on_battery ? 'text-amber-400' : 'text-emerald-400'}`}>
                      {unit.status_label}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="text-lg font-bold text-emerald-400">{unit.battery_pct}%</p>
                      <p className="text-[10px] text-slate-500">Battery</p>
                    </div>
                    <div>
                      <p className="text-lg font-bold text-sky-400">{unit.load_pct}%</p>
                      <p className="text-[10px] text-slate-500">Load</p>
                    </div>
                    <div>
                      <p className="text-lg font-bold text-slate-300">{Math.round(unit.runtime_s / 60)}m</p>
                      <p className="text-[10px] text-slate-500">Runtime</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        ),
      });
    }

    // ── SSL Certificates ──
    if (data?.ssl_certs?.length) {
      w.push({
        id: 'ssl',
        title: 'SSL Certificates',
        defaultLayout: { x: 2, y: 13, w: 1, h: 2, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <Lock size={16} className="text-emerald-400" /> SSL Certificates
            </h3>
            <div className="space-y-1 max-h-[160px] overflow-y-auto">
              {data!.ssl_certs.map((cert) => {
                const color = cert.days === null ? 'text-slate-500'
                  : cert.days <= 7 ? 'text-red-400'
                  : cert.days <= 30 ? 'text-amber-400'
                  : 'text-emerald-400';
                return (
                  <Link
                    key={cert.host_id}
                    href={`/hosts/${cert.host_id}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${cert.days === null ? 'bg-slate-500' : cert.days <= 7 ? 'bg-red-400' : cert.days <= 30 ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                    <span className="flex-1 text-sm text-slate-200 truncate">{cert.name}</span>
                    <span className={`text-xs font-mono ${color}`}>
                      {cert.days !== null ? `${cert.days}d` : '?'}
                    </span>
                  </Link>
                );
              })}
            </div>
          </>
        ),
      });
    }

    // ── Containers ──
    if (data?.container_data) {
      w.push({
        id: 'containers',
        title: 'Containers',
        defaultLayout: { x: 0, y: 15, w: 1, h: 2, minW: 1, minH: 2 },
        render: () => (
          <>
            <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
              <Container size={16} className="text-cyan-400" /> Containers
            </h3>
            <div className="grid grid-cols-2 gap-3 text-center mb-3">
              <div>
                <p className="text-2xl font-bold text-emerald-400">{data!.container_data!.running}</p>
                <p className="text-[10px] text-slate-500 uppercase">Running</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-slate-400">{data!.container_data!.stopped}</p>
                <p className="text-[10px] text-slate-500 uppercase">Stopped</p>
              </div>
            </div>
            <div className="space-y-1 max-h-[100px] overflow-y-auto">
              {data!.container_data!.environments.map((env, i) => (
                <div key={i} className="flex items-center gap-2 px-2 py-1">
                  <span className="flex-1 text-xs text-slate-300 truncate">{env.name}</span>
                  <span className="text-xs font-mono text-emerald-400">{env.containers_running}</span>
                  <span className="text-[10px] text-slate-600">/</span>
                  <span className="text-xs font-mono text-slate-500">
                    {env.containers_running + env.containers_stopped}
                  </span>
                </div>
              ))}
            </div>
          </>
        ),
      });
    }

    // ── Alert Trends ──
    w.push({
      id: 'alert-trends',
      title: 'Alert Trends',
      defaultLayout: { x: 0, y: 17, w: 2, h: 3, minW: 1, minH: 2 },
      render: () => (
        <>
          <h3 className="text-base font-semibold text-slate-200 mb-4 flex items-center gap-2">
            <TrendingUp size={16} className="text-amber-400" /> Alert Trends (14d)
          </h3>
          {isLoading || !data?.incident_trend ? (
            <Skeleton className="h-[180px] w-full" />
          ) : data.incident_trend.every((d) => d.critical === 0 && d.warning === 0 && d.info === 0) ? (
            <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
              No incidents in the last 14 days
            </p>
          ) : (
            <EChart
              height={180}
              option={{
                tooltip: { trigger: 'axis' },
                legend: { show: false },
                grid: { left: 40, right: 12, top: 8, bottom: 24 },
                xAxis: { type: 'category', data: data.incident_trend.map((d) => d.date), axisLabel: { fontSize: 10 } },
                yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
                series: [
                  { name: 'Critical', type: 'bar', stack: 'total', data: data.incident_trend.map((d) => d.critical), color: '#F87171' },
                  { name: 'Warning', type: 'bar', stack: 'total', data: data.incident_trend.map((d) => d.warning), color: '#FBBF24' },
                  { name: 'Info', type: 'bar', stack: 'total', data: data.incident_trend.map((d) => d.info), color: '#38BDF8' },
                ],
              }}
            />
          )}
        </>
      ),
    });

    // ── Live Syslog ──
    w.push({
      id: 'live-syslog',
      title: 'Live Syslog',
      defaultLayout: { x: 2, y: 17, w: 1, h: 3, minW: 1, minH: 2 },
      render: () => <LiveSyslogWidget />,
    });

    return w;
  }, [data, isLoading]);

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Infrastructure overview"
        actions={data?.nodeglow_uptime ? (
          <span className="text-xs text-slate-500 flex items-center gap-1.5">
            <Clock size={12} /> Uptime: {data.nodeglow_uptime}
          </span>
        ) : undefined}
      />

      {/* ── Quick Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        <StatCard icon={Server} label="Online" value={data?.online_count} color="text-emerald-400" loading={isLoading} />
        <StatCard icon={ServerOff} label="Offline" value={data?.offline_count} color="text-red-400" loading={isLoading} />
        <StatCard icon={Gauge} label="Avg Latency" value={avgLatency} suffix="ms" color="text-sky-400" loading={isLoading} />
        <StatCard icon={ShieldAlert} label="Incidents" value={data?.active_incidents} color="text-amber-400" loading={isLoading} />
        <StatCard icon={ArrowUpDown} label="Syslog 24h" value={data?.syslog_stats?.total_24h} color="text-violet-400" loading={isLoading} />
        <StatCard icon={TrendingUp} label="Total" value={data?.total_count} color="text-slate-300" loading={isLoading} />
      </div>

      {/* ── Gravity Globe ── */}
      {data?.host_stats && (
        <div className="mb-6">
          <GravityWidget hosts={data.host_stats} />
        </div>
      )}

      {/* ── Draggable Widgets ── */}
      <DraggableDashboard widgets={widgets} />
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  loading,
  suffix,
}: {
  icon: React.ElementType;
  label: string;
  value?: number;
  color: string;
  loading: boolean;
  suffix?: string;
}) {
  return (
    <GlassCard className="p-4 flex items-center gap-4">
      <div className={`p-2.5 rounded-xl bg-white/[0.06] ${color}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className="text-[11px] text-slate-500 uppercase tracking-wider font-medium">{label}</p>
        {loading ? (
          <Skeleton className="h-8 w-16 mt-1" />
        ) : (
          <p className={`text-3xl font-bold tracking-tight ${color}`}>
            <AnimatedCounter value={value ?? 0} suffix={suffix} />
          </p>
        )}
      </div>
    </GlassCard>
  );
}

/* ── Live Syslog Widget ── */

const SEV_COLORS: Record<number, string> = {
  0: 'bg-red-500 text-white',
  1: 'bg-red-400 text-white',
  2: 'bg-red-400/80 text-white',
  3: 'bg-orange-400 text-black',
  4: 'bg-amber-400 text-black',
  5: 'bg-blue-400 text-white',
  6: 'bg-sky-400/60 text-white',
  7: 'bg-slate-500 text-white',
};

const SEV_LABELS: Record<number, string> = {
  0: 'EMERG', 1: 'ALERT', 2: 'CRIT', 3: 'ERR',
  4: 'WARN', 5: 'NOTICE', 6: 'INFO', 7: 'DEBUG',
};

function LiveSyslogWidget() {
  const [enabled, setEnabled] = useState(false);
  const { messages, isStreaming, clear } = useSSE<SyslogMessage>({
    url: '/syslog/stream',
    enabled,
    maxMessages: 100,
  });

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2">
          <Radio size={16} className="text-cyan-400" /> Live Syslog
        </h3>
        <div className="flex items-center gap-2">
          {enabled && messages.length > 0 && (
            <button
              onClick={clear}
              className="px-2 py-0.5 text-[10px] text-slate-400 hover:text-slate-200 transition-colors"
            >
              Clear
            </button>
          )}
          <button
            onClick={() => setEnabled((v) => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
              enabled
                ? 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
                : 'bg-white/[0.06] text-slate-400 hover:bg-white/[0.10]'
            }`}
          >
            {enabled && isStreaming && (
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
            )}
            {enabled ? 'Live' : 'Start'}
          </button>
        </div>
      </div>

      {!enabled ? (
        <p className="text-sm text-slate-500 text-center py-6">
          Click Start to stream syslog messages
        </p>
      ) : messages.length === 0 ? (
        <p className="text-sm text-slate-500 text-center py-6">
          Waiting for messages...
        </p>
      ) : (
        <div className="max-h-[260px] overflow-y-auto space-y-0">
          {messages.map((msg, i) => (
            <div
              key={`${msg.timestamp}-${i}`}
              className="flex items-start gap-2 px-1 py-1 hover:bg-white/[0.06] transition-colors"
            >
              <span className="text-[10px] text-slate-600 font-mono whitespace-nowrap shrink-0 pt-0.5 w-12">
                {msg.timestamp?.slice(-8, -3) || ''}
              </span>
              <span
                className={`inline-block px-1 py-0 rounded text-[9px] font-bold shrink-0 leading-4 ${SEV_COLORS[msg.severity] ?? 'bg-slate-500 text-white'}`}
              >
                {SEV_LABELS[msg.severity] ?? msg.severity}
              </span>
              <span className="text-[10px] text-sky-300/60 font-mono whitespace-nowrap shrink-0 max-w-[80px] truncate">
                {msg.hostname}
              </span>
              <span className="text-[11px] text-slate-400 truncate min-w-0">
                {msg.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
