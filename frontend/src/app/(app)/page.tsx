'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { AnimatedCounter } from '@/components/data/AnimatedCounter';
import dynamic from 'next/dynamic';

const HeatmapGrid = dynamic(
  () => import('@/components/charts/HeatmapGrid').then(m => ({ default: m.HeatmapGrid })),
  { loading: () => <Skeleton className="h-40 w-full" />, ssr: false }
);
const EChart = dynamic(
  () => import('@/components/charts/EChart').then(m => ({ default: m.EChart })),
  { loading: () => <Skeleton className="h-[180px] w-full" />, ssr: false }
);
const GravityWidget = dynamic(
  () => import('@/components/dashboard/GravityWidget').then(m => ({ default: m.GravityWidget })),
  { loading: () => <div className="h-[380px] bg-slate-500/10 rounded-lg animate-pulse" />, ssr: false }
);
import { FirstRunWelcome } from '@/components/dashboard/FirstRunWelcome';
import { useDashboard } from '@/hooks/queries/useDashboard';
import { useSSE } from '@/hooks/useSSE';
import type { SyslogMessage } from '@/types';
import { cn, formatLatency } from '@/lib/utils';
import {
  Server, ServerOff, Gauge, ShieldAlert, Zap, Clock,
  ArrowUpDown, HardDrive, Activity, AlertTriangle,
  Container, BatteryCharging, Lock, Trophy, Timer,
  TrendingUp, Wifi, EyeOff,
} from 'lucide-react';
import Link from 'next/link';
import { ngColors } from '@/styles/tokens.gen';
import { WidgetErrorBoundary } from '@/components/ui/WidgetErrorBoundary';

function WidgetHeader({ icon: Icon, iconColor, title, trailing }: {
  icon: React.ElementType;
  iconColor: string;
  title: string;
  trailing?: React.ReactNode;
}) {
  // Compact header (Phase: density pass) — was 56px, now ~28px. The icon
  // tint background was decoration; now it's just the icon in its accent
  // color directly next to the title. Title becomes a label-style uppercase
  // tracking-widest piece, which reads as a section marker rather than
  // a hero element.
  return (
    <div
      className="flex items-center gap-2 mb-3 pb-2 border-b"
      style={{ borderColor: 'var(--ng-card-border)' }}
    >
      <Icon size={13} className={iconColor} />
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-slate-300 flex-1">
        {title}
      </h3>
      {trailing}
    </div>
  );
}

export default function DashboardPage() {
  useEffect(() => { document.title = 'Dashboard | Nodeglow'; }, []);
  const { data, isLoading, dataUpdatedAt } = useDashboard();

  // "Just refreshed" indicator — flashes a small sky pulse next to the
  // page header for ~1.6s every time a new dashboard payload arrives. Tells
  // the user the screen is alive and what they're looking at is current.
  const [justRefreshed, setJustRefreshed] = useState(false);
  useEffect(() => {
    if (!dataUpdatedAt) return;
    setJustRefreshed(true);
    const t = setTimeout(() => setJustRefreshed(false), 1600);
    return () => clearTimeout(t);
  }, [dataUpdatedAt]);

  const avgLatency = data?.host_stats
    ? Math.round(
        data.host_stats
          .filter((h) => h.latency !== null && h.online)
          .reduce((sum, h) => sum + (h.latency ?? 0), 0) /
          (data.host_stats.filter((h) => h.latency !== null && h.online).length || 1),
      )
    : 0;

  const anomalyCount = (data?.anomalies?.length ?? 0) + (data?.warnings?.length ?? 0);

  // Hosts nobody is currently observing (probe gone silent, or never checked
  // yet) — distinct from both online and offline. The backend doesn't send
  // this count directly, so it's derived from the same host_stats list the
  // Online/Offline tiles use.
  const unknownCount = data?.host_stats
    ? data.host_stats.filter((h) => h.online === null && !h.host.maintenance).length
    : 0;

  // Chart options are memoized so EChart only rebuilds when the underlying
  // data changes — inline objects would retrigger setOption on every render.
  const rateData = data?.syslog_stats?.rate_data;
  const syslogRateOption = useMemo(() => rateData && ({
    tooltip: { trigger: 'axis' as const },
    legend: { show: false },
    grid: { left: 40, right: 12, top: 8, bottom: 24 },
    xAxis: { type: 'category' as const, data: rateData.labels, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' as const, axisLabel: { fontSize: 10 } },
    series: [
      { name: 'Errors', type: 'bar' as const, stack: 'total', data: rateData.errors, color: ngColors.critical },
      { name: 'Warnings', type: 'bar' as const, stack: 'total', data: rateData.warnings, color: ngColors.warning },
      { name: 'Info', type: 'bar' as const, stack: 'total', data: rateData.info, color: ngColors.primary },
    ],
  }), [rateData]);

  const incidentTrend = data?.incident_trend;
  const alertTrendOption = useMemo(() => incidentTrend && ({
    tooltip: { trigger: 'axis' as const },
    legend: { show: false },
    grid: { left: 40, right: 12, top: 8, bottom: 24 },
    xAxis: { type: 'category' as const, data: incidentTrend.map((d) => d.date), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' as const, minInterval: 1, axisLabel: { fontSize: 10 } },
    series: [
      { name: 'Critical', type: 'bar' as const, stack: 'total', data: incidentTrend.map((d) => d.critical), color: ngColors.critical },
      { name: 'Warning', type: 'bar' as const, stack: 'total', data: incidentTrend.map((d) => d.warning), color: ngColors.warning },
      { name: 'Info', type: 'bar' as const, stack: 'total', data: incidentTrend.map((d) => d.info), color: ngColors.primary },
    ],
  }), [incidentTrend]);

  // First-run detection: nothing to show. We branch BEFORE rendering the
  // empty grid of widgets so the user gets a guided onboarding instead of
  // a void.
  const isFirstRun =
    !isLoading &&
    !!data &&
    (data.total_count ?? 0) === 0 &&
    (data.host_stats?.length ?? 0) === 0 &&
    (data.integration_health?.length ?? 0) === 0;

  if (isFirstRun) {
    return (
      <div>
        <PageHeader title="Dashboard" description="Infrastructure overview" />
        <FirstRunWelcome />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Infrastructure overview"
        actions={
          <div className="flex items-center gap-3">
            {/* Live-refresh indicator: tiny sky dot that pulses when fresh
                data lands. Reads as "this screen is alive". */}
            <span
              className={cn(
                'inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-slate-500',
                justRefreshed && 'text-sky-400',
              )}
              title={dataUpdatedAt ? `Last refresh: ${new Date(dataUpdatedAt).toLocaleTimeString()}` : undefined}
            >
              <span
                className={cn(
                  'inline-block w-1.5 h-1.5 rounded-full bg-sky-400 transition-opacity',
                  justRefreshed ? 'opacity-100 animate-ping' : 'opacity-40',
                )}
              />
              live
            </span>
            {data?.nodeglow_uptime && (
              <span className="text-xs text-slate-500 flex items-center gap-1.5 tabular-nums">
                <Clock size={12} /> Uptime: {data.nodeglow_uptime}
              </span>
            )}
          </div>
        }
      />

      {/* ── Quick Stats — compact row, ~64px tall ──
          justRefreshed re-applies .ng-just-changed for 1.6s on every refresh,
          which restarts the CSS animation (globals.css) without remounting
          the cards. That's the subtle sky ring pulse that tells you "fresh
          data just landed" without being loud about it. */}
      <div
        className={cn(
          'grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3 mb-4 rounded-lg',
          justRefreshed && 'ng-just-changed',
        )}
      >
        <StatCard icon={Server} label="Online" value={data?.online_count} color="text-emerald-400" tint="bg-emerald-500/10" loading={isLoading} href="/hosts?status=online" deltaGoodWhen="up" />
        <StatCard icon={ServerOff} label="Offline" value={data?.offline_count} color="text-red-400" tint="bg-red-500/10" alert={!!data?.offline_count} loading={isLoading} href="/hosts?status=offline" deltaGoodWhen="down" />
        <StatCard icon={EyeOff} label="Not Observed" value={unknownCount} color="text-slate-400" tint="bg-slate-400/10" loading={isLoading} href="/hosts?status=unknown" deltaGoodWhen="down" />
        <StatCard icon={Gauge} label="Avg Latency" value={avgLatency} suffix="ms" color="text-sky-400" tint="bg-sky-500/10" loading={isLoading} href="/hosts" deltaGoodWhen="down" />
        <StatCard icon={ShieldAlert} label="Incidents" value={data?.active_incidents} color="text-amber-400" tint="bg-amber-500/10" alert={!!data?.active_incidents} loading={isLoading} href="/alerts?tab=incidents" deltaGoodWhen="down" />
        <StatCard icon={ArrowUpDown} label="Syslog 24h" value={data?.syslog_stats?.total_24h} color="text-violet-400" tint="bg-violet-500/10" loading={isLoading} href="/syslog" deltaGoodWhen="down" />
        <StatCard icon={TrendingUp} label="Total" value={data?.total_count} color="text-slate-300" tint="bg-slate-400/10" loading={isLoading} href="/hosts" deltaGoodWhen="up" />
      </div>

      {/* ── Gravity Widget (Hero) ── */}
      {data?.host_stats && (
        <div className="mb-4">
          <WidgetErrorBoundary label="Gravity globe">
            <GravityWidget hosts={data.host_stats} />
          </WidgetErrorBoundary>
        </div>
      )}

      {/* ── Command Center Grid — tighter gaps ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">

        {/* Row 1: Hosts | Incidents | Syslog Rate (2-col)
            Severity-driven accent: cards gain a colored border when their
            content contains active alerts. The eye picks up border color
            before it picks up the list contents. */}
        <GlassCard
          className={cn(
            'p-4',
            (data?.offline_count ?? 0) > 0 && 'border-red-500/25',
          )}
        >
          <WidgetHeader
            icon={Server}
            iconColor="text-sky-400"
            title="Hosts"
            trailing={
              (data?.offline_count ?? 0) > 0 && (
                <span className="text-[10px] font-mono text-red-400 tabular-nums">
                  {data?.offline_count} offline
                </span>
              )
            }
          />
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
                  className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={h.host.maintenance ? 'maintenance' : h.online === null ? 'unknown' : h.online === false ? 'offline' : h.host.port_error ? 'error' : 'online'} />
                  <span className="flex-1 text-sm text-slate-200 truncate">{h.host.name}</span>
                  <span className="text-xs font-mono text-slate-500 tabular-nums">
                    {formatLatency(h.latency)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </GlassCard>

        <GlassCard
          className={cn(
            'p-4',
            (data?.active_incidents ?? 0) > 0 && 'border-amber-500/30',
          )}
        >
          <WidgetHeader
            icon={ShieldAlert}
            iconColor="text-red-400"
            title="Recent Incidents"
            trailing={
              (data?.active_incidents ?? 0) > 0 && (
                <span className="text-[10px] font-mono text-amber-400 tabular-nums">
                  {data?.active_incidents} active
                </span>
              )
            }
          />
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
                  className="flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/[0.04] transition-colors"
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
        </GlassCard>

        <GlassCard className="p-4 lg:col-span-2">
          <WidgetHeader icon={ArrowUpDown} iconColor="text-sky-400" title="Syslog Rate (24h)" trailing={
            data?.syslog_stats ? (
              <span className="text-xs text-slate-500 font-mono">
                {data.syslog_stats.total_24h.toLocaleString()} total
              </span>
            ) : undefined
          } />
          {isLoading || !syslogRateOption ? (
            <Skeleton className="h-[180px] w-full" />
          ) : (
            <WidgetErrorBoundary label="Syslog Rate">
              <EChart height={180} option={syslogRateOption} />
            </WidgetErrorBoundary>
          )}
          <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--ng-card-border)' }}>
            <LiveSyslogWidget />
          </div>
        </GlassCard>

        {/* Row 2: 30-Day Heatmap (full-width) */}
        <GlassCard className="p-4 lg:col-span-4 md:col-span-2">
          <WidgetHeader icon={Activity} iconColor="text-sky-400" title="30-Day Availability" />
          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : data?.heatmap_data ? (
            <WidgetErrorBoundary label="Heatmap">
              <HeatmapGrid data={data.heatmap_data} days={data.heatmap_days} />
            </WidgetErrorBoundary>
          ) : (
            <p className="text-sm text-slate-500">No data</p>
          )}
        </GlassCard>

        {/* Row 3: Uptime | Latency | Alert Trends (2-col) */}
        <GlassCard className="p-4">
          <WidgetHeader icon={Trophy} iconColor="text-amber-400" title="Uptime Ranking" />
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
                    className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-white/[0.04] transition-colors relative overflow-hidden"
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
        </GlassCard>

        <GlassCard className="p-4">
          <WidgetHeader icon={Timer} iconColor="text-rose-400" title="Highest Latency" />
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
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-colors"
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
        </GlassCard>

        <GlassCard className="p-4 lg:col-span-2">
          <WidgetHeader icon={TrendingUp} iconColor="text-amber-400" title="Alert Trends (14d)" />
          {isLoading || !data?.incident_trend ? (
            <Skeleton className="h-[180px] w-full" />
          ) : data.incident_trend.every((d) => d.critical === 0 && d.warning === 0 && d.info === 0) ? (
            <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
              No incidents in the last 14 days
            </p>
          ) : (
            <WidgetErrorBoundary label="Alert Trends">
              {alertTrendOption && <EChart height={180} option={alertTrendOption} />}
            </WidgetErrorBoundary>
          )}
        </GlassCard>

        {/* Row 4: Integrations | Anomalies | Storage (cond.) | Live Syslog */}
        <GlassCard className="p-4">
          <WidgetHeader icon={Zap} iconColor="text-violet-400" title="Integrations" />
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
                  className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-colors"
                >
                  <StatusDot status={int.ok ? 'online' : int.ok === false ? 'offline' : 'unknown'} />
                  <span className="flex-1 text-sm text-slate-200 truncate">
                    {int.label}
                  </span>
                  <span className="text-xs text-slate-500 truncate max-w-[100px]">{int.name}</span>
                </Link>
              ))}
            </div>
          )}
        </GlassCard>

        <GlassCard className="p-4">
          <WidgetHeader icon={AlertTriangle} iconColor="text-amber-400" title="Anomalies" trailing={
            anomalyCount > 0 ? (
              <span className="text-xs font-mono text-amber-400">{anomalyCount}</span>
            ) : undefined
          } />
          {isLoading ? (
            <Skeleton className="h-[200px] w-full" />
          ) : anomalyCount === 0 ? (
            <p className="text-sm text-emerald-400 flex items-center gap-2 py-4 justify-center">
              No anomalies detected
            </p>
          ) : (
            <div className="space-y-1.5 max-h-[240px] overflow-y-auto">
              {data?.anomalies?.slice(0, 8).map((a, i) => (
                <div key={`a-${i}`} className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.02]">
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
                <div key={`w-${i}`} className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-white/[0.02]">
                  <span className="text-xs text-amber-400 font-mono shrink-0">{w.metric}</span>
                  <span className="flex-1 text-sm text-slate-200 truncate">{w.name}</span>
                  <span className="text-xs font-mono text-slate-400">
                    {w.current}% &ge; {w.threshold}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </GlassCard>

        {data?.storage_pools?.length ? (
          <GlassCard className="p-4">
            <WidgetHeader icon={HardDrive} iconColor="text-sky-400" title="Storage" />
            <div className="space-y-3 max-h-[260px] overflow-y-auto">
              {data.storage_pools.map((pool, i) => (
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
                  <div className="h-2 rounded-lg bg-white/[0.06] overflow-hidden">
                    <div
                      className="h-full rounded-lg transition-all"
                      style={{
                        width: `${Math.min(pool.pct, 100)}%`,
                        background: pool.pct >= 90 ? ngColors.critical : pool.pct >= 75 ? ngColors.warning : ngColors.success,
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
          </GlassCard>
        ) : (
          <div className="hidden lg:block" />
        )}

        {/* Row 5: Conditional infra widgets */}
        {data?.speedtest_data && (
          <GlassCard className="p-4">
            <WidgetHeader icon={Wifi} iconColor="text-blue-400" title="Speedtest" />
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <p className="text-2xl font-bold text-sky-400">{Math.round(data.speedtest_data.download_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Down Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-violet-400">{Math.round(data.speedtest_data.upload_mbps)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Up Mbps</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-emerald-400">{data.speedtest_data.ping_ms === null ? '—' : Math.round(data.speedtest_data.ping_ms)}</p>
                <p className="text-[10px] text-slate-500 uppercase">Ping ms</p>
              </div>
            </div>
            <div className="flex items-center justify-between mt-2">
              <p className="text-[10px] text-slate-600">{data.speedtest_data.server_name}</p>
              <p className="text-[10px] text-slate-600">{data.speedtest_data.timestamp}</p>
            </div>
          </GlassCard>
        )}

        {data?.ups_data && (
          <GlassCard className="p-4">
            <WidgetHeader
              icon={BatteryCharging}
              iconColor={data.ups_data.on_battery ? 'text-amber-400' : 'text-emerald-400'}
              title="UPS"
              trailing={data.ups_data.on_battery ? (
                <Badge variant="severity" severity="warning">On Battery</Badge>
              ) : undefined}
            />
            <div className="space-y-3">
              {data.ups_data.units.map((unit, i) => (
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
          </GlassCard>
        )}

        {(data?.ssl_certs?.length ?? 0) > 0 && (
          <GlassCard className="p-4">
            <WidgetHeader icon={Lock} iconColor="text-emerald-400" title="SSL Certificates" />
            <div className="space-y-1 max-h-[160px] overflow-y-auto">
              {data!.ssl_certs.map((cert, i) => {
                const color = cert.days === null ? 'text-slate-500'
                  : cert.days <= 7 ? 'text-red-400'
                  : cert.days <= 30 ? 'text-amber-400'
                  : 'text-emerald-400';
                const dotColor = cert.days === null ? 'bg-slate-500' : cert.days <= 7 ? 'bg-red-400' : cert.days <= 30 ? 'bg-amber-400' : 'bg-emerald-400';
                const inner = (
                  <>
                    <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
                    <span className="flex-1 text-sm text-slate-200 truncate">{cert.name}</span>
                    {cert.source && cert.source !== 'host' && (
                      <span className="text-[10px] text-slate-600">{cert.source}</span>
                    )}
                    <span className={`text-xs font-mono ${color}`}>
                      {cert.days !== null ? `${cert.days}d` : '?'}
                    </span>
                  </>
                );
                return cert.host_id ? (
                  <Link
                    key={`host-${cert.host_id}`}
                    href={`/hosts/${cert.host_id}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/[0.04] transition-colors"
                  >
                    {inner}
                  </Link>
                ) : (
                  <div
                    key={`int-${i}`}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg"
                  >
                    {inner}
                  </div>
                );
              })}
            </div>
          </GlassCard>
        )}

        {data?.container_data && (
          <GlassCard className="p-4">
            <WidgetHeader icon={Container} iconColor="text-cyan-400" title="Containers"
              trailing={data.container_data.updates_available ? (
                <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-[10px] font-medium text-amber-400">
                  {data.container_data.updates_available} update{data.container_data.updates_available > 1 ? 's' : ''}
                </span>
              ) : undefined}
            />
            <div className="grid grid-cols-3 gap-3 text-center mb-3">
              <div>
                <p className="text-2xl font-bold text-emerald-400">{data.container_data.running}</p>
                <p className="text-[10px] text-slate-500 uppercase">Running</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-slate-400">{data.container_data.stopped}</p>
                <p className="text-[10px] text-slate-500 uppercase">Stopped</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-slate-200">{data.container_data.containers?.length ?? 0}</p>
                <p className="text-[10px] text-slate-500 uppercase">Total</p>
              </div>
            </div>
            <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
              {(data.container_data.containers ?? []).map((ct, i) => {
                const stateColor = ct.state === 'running' ? 'bg-emerald-500'
                  : ct.state === 'exited' ? 'bg-red-500' : 'bg-amber-500';
                const healthIcon = ct.health === 'healthy' ? '✓'
                  : ct.health === 'unhealthy' ? '✗' : null;
                return (
                  <div key={`${ct.host}-${ct.name}-${i}`} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-white/[0.03]">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${stateColor}`} />
                    <span className="flex-1 text-xs text-slate-200 truncate">{ct.name}</span>
                    {healthIcon && (
                      <span className={`text-[10px] ${ct.health === 'healthy' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {healthIcon}
                      </span>
                    )}
                    {ct.cpu_pct != null && ct.cpu_pct > 0 && (
                      <span className="text-[10px] font-mono text-sky-400 w-10 text-right">{ct.cpu_pct.toFixed(1)}%</span>
                    )}
                    {ct.mem_mb != null && ct.mem_mb > 0 && (
                      <span className="text-[10px] font-mono text-violet-400 w-14 text-right">
                        {ct.mem_mb >= 1024 ? `${(ct.mem_mb / 1024).toFixed(1)}G` : `${Math.round(ct.mem_mb)}M`}
                      </span>
                    )}
                    {(ct.restart_count ?? 0) > 0 && (
                      <span className="text-[10px] text-amber-400">↻{ct.restart_count}</span>
                    )}
                    {ct.update_available && (
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" title="Update available" />
                    )}
                    <span className="text-[10px] text-slate-600 truncate max-w-[60px]">{ct.host}</span>
                  </div>
                );
              })}
            </div>
          </GlassCard>
        )}
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  tint,
  loading,
  suffix,
  alert,
  href,
  deltaGoodWhen = 'down',
}: {
  icon: React.ElementType;
  label: string;
  value?: number;
  color: string;
  tint?: string;
  loading: boolean;
  suffix?: string;
  alert?: boolean;
  href?: string;
  /** Which direction of delta is "good news" — e.g. latency going down
   *  is good, online count going up is good. Controls the arrow color. */
  deltaGoodWhen?: 'up' | 'down';
}) {
  // Remember the previous value across renders so we can flash a delta
  // arrow when it changes. null until the first value arrives, so we
  // don't flash on initial mount.
  const prevRef = useRef<number | null>(null);
  const [delta, setDelta] = useState<number | null>(null);
  const [deltaVisible, setDeltaVisible] = useState(false);
  useEffect(() => {
    if (value == null) return;
    if (prevRef.current == null) {
      prevRef.current = value;
      return;
    }
    const diff = value - prevRef.current;
    if (diff !== 0) {
      setDelta(diff);
      setDeltaVisible(true);
      const t = setTimeout(() => setDeltaVisible(false), 4000);
      prevRef.current = value;
      return () => clearTimeout(t);
    }
  }, [value]);

  const deltaIsGood =
    delta == null
      ? false
      : deltaGoodWhen === 'up'
        ? delta > 0
        : delta < 0;

  // Compact StatCard (density pass) — was 96px tall, now ~64px. Icon
  // shrinks from 22 to 16, padding p-5 → p-3, value from text-3xl to
  // text-2xl. Top accent bar stays for visual identification of the stat
  // family. Tabular nums prevent layout shift on changing values.
  const card = (
    <GlassCard className={cn(
      'p-3 flex items-center gap-3 stat-card-hover relative overflow-hidden',
      alert && value ? 'border-red-500/25' : '',
      href && 'cursor-pointer',
    )}>
      <div className={`absolute top-0 left-0 right-0 h-[2px] ${tint?.replace('/10', '/40') || ''}`} />
      <div className={`p-2 rounded-lg ${tint || 'bg-white/[0.06]'} ${color}`}>
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[10px] text-slate-500 uppercase tracking-widest font-medium leading-none mb-1">{label}</p>
        {loading ? (
          <Skeleton className="h-6 w-14" />
        ) : (
          <div className="flex items-baseline gap-1.5">
            <p className="text-2xl font-bold tracking-tight text-slate-100 tabular-nums leading-none">
              <AnimatedCounter value={value ?? 0} suffix={suffix} />
            </p>
            {delta != null && deltaVisible && (
              <span
                className={cn(
                  'text-[10px] font-mono tabular-nums leading-none transition-opacity',
                  deltaIsGood ? 'text-emerald-400' : 'text-red-400',
                )}
                title={`Changed by ${delta > 0 ? '+' : ''}${delta} since last refresh`}
              >
                {delta > 0 ? '↑' : '↓'}{Math.abs(delta)}
              </span>
            )}
          </div>
        )}
      </div>
    </GlassCard>
  );
  if (href) return <Link href={href}>{card}</Link>;
  return card;
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
  // WARN-and-above filter is the default — the dashboard is for ambient
  // awareness, not full log browsing. Click "View all" to see everything.
  const [warnOnly, setWarnOnly] = useState(true);
  const { messages, isStreaming } = useSSE<SyslogMessage>({
    url: '/syslog/stream',
    enabled: true,
    maxMessages: 50,
  });

  const visible = warnOnly
    ? messages.filter((m) => typeof m.severity === 'number' && m.severity <= 4)
    : messages;

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-medium text-slate-400 uppercase tracking-wider">Live Feed</h3>
          {isStreaming && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setWarnOnly((v: boolean) => !v)}
            className={`text-[10px] px-1.5 py-0.5 rounded font-mono transition-colors ${
              warnOnly
                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                : 'bg-slate-500/10 text-slate-400 border border-slate-500/20 hover:text-slate-200'
            }`}
            title="Toggle WARN+ filter"
          >
            {warnOnly ? 'WARN+' : 'ALL'}
          </button>
          <Link href="/syslog" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
            View all
          </Link>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="text-xs text-slate-500 text-center py-4">
          {warnOnly && messages.length > 0
            ? `Quiet — no WARN+ in last ${messages.length} messages.`
            : 'Waiting for messages...'}
        </p>
      ) : (
        <div className="max-h-[200px] overflow-y-auto space-y-0">
          {visible.map((msg) => (
            <div
              key={msg.__sseId}
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
