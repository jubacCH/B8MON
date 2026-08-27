'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatusDot } from '@/components/ui/StatusDot';
import { useQuery } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { useCallback, useEffect, useState } from 'react';
import { UpdateProgress } from '@/components/system/UpdateProgress';
import type { UpdateRunState } from '@/lib/updateSteps';
import {
  Database, Clock, Server, RefreshCw, Download, Cpu, HardDrive,
  MemoryStick, Activity, Shield, AlertTriangle, Plug, Timer,
} from 'lucide-react';

/* ---------- Types ---------- */

interface SystemStatus {
  application: {
    version: string;
    uptime_seconds: number;
    uptime_human: string;
    python_version: string;
    platform: string;
    hostname: string;
    pid: number;
    start_time: string;
    git_commit: string;
  };
  system: {
    cpu_count: number;
    cpu_pct: number;
    load_1m: number;
    load_5m: number;
    load_15m: number;
    mem_total_gb: number;
    mem_used_gb: number;
    mem_pct: number;
    swap_total_gb: number;
    swap_used_gb: number;
    swap_pct: number;
    disk_total_gb: number;
    disk_used_gb: number;
    disk_pct: number;
    disk_io: { read_gb?: number; write_gb?: number };
    net_io: { sent_gb?: number; recv_gb?: number };
    net_if: Array<{ name: string; ip: string; up: boolean; speed: number }>;
  };
  process: {
    rss_mb: number;
    vms_mb: number;
    threads: number;
    open_files: number;
    connections: number;
    cpu_user: number;
    cpu_system: number;
  };
  database: {
    db_size?: string;
    host_count?: number;
    result_count?: number;
    config_count?: number;
    snapshot_count?: number;
    syslog_count?: number;
    oldest_ping?: string;
    newest_ping?: string;
    error?: string;
  };
  disk_forecast: {
    growth_gb_per_day: number;
    days_until_full: number | null;
    trend: 'stable' | 'normal' | 'warning' | 'critical';
  } | null;
  top_tables: Array<{ name: string; size: string; rows: number }>;
  pool: {
    size?: number;
    checked_in?: number;
    checked_out?: number;
    overflow?: number;
    max_overflow?: number;
  };
  scheduler_jobs: Array<{
    id: string;
    name: string;
    trigger: string;
    next_run: string;
  }>;
  integrations: Array<{
    type: string;
    name: string;
    ok: boolean | null;
    last_check: string;
    error: string | null;
  }>;
  dashboard_perf: {
    total_ms: number;
    sections: Array<{ name: string; ms: number }>;
    timestamp: string;
  } | null;
  operational: {
    ping_stats: {
      checks_1h?: number;
      success_rate?: number;
      avg_latency?: number;
      max_latency?: number;
    };
    syslog_status: {
      running?: boolean;
      buffer_size?: number;
      msg_per_min?: number;
    };
    ssl_expiring: Array<{ name: string; hostname: string; days: number }>;
    notification_channels: { telegram?: boolean; discord?: boolean; email?: boolean };
    incidents: { open?: number; acknowledged?: number; resolved?: number; total?: number };
    alert_rules: { total?: number; enabled?: number; syslog_rules?: number; last_triggered?: string };
    maintenance: { active?: number; timed?: number; indefinite?: number };
    log_intelligence: { templates?: number; baselines?: number; precursors?: number };
    data_retention: { ping_age?: string; snap_age?: string; syslog_age?: string };
  };
}

/* ---------- Progress Bar ---------- */

function ProgressBar({ value, color }: { value: number; color: string }) {
  const clamped = Math.min(100, Math.max(0, value));
  return (
    <div className="w-full h-2 rounded-full bg-white/[0.06] overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

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

/* ---------- Stat Row ---------- */

function StatRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between items-center py-1">
      <span className="text-sm text-slate-400">{label}</span>
      <span className="text-sm font-medium text-slate-200">{value}</span>
    </div>
  );
}

/* ---------- Page ---------- */

export default function SystemStatusPage() {
  useEffect(() => { document.title = 'System Status | Nodeglow'; }, []);

  // A run started in another tab (or before a reload) keeps rendering here.
  useEffect(() => {
    let cancelled = false;
    get<UpdateRunState>('/api/update/status')
      .then((s) => { if (!cancelled && s?.status === 'running') setRunActive(true); })
      .catch(() => { /* sidecar unavailable — nothing to resume */ });
    return () => { cancelled = true; };
  }, []);
  const toast = useToastStore((s) => s.show);
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [runActive, setRunActive] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<{
    update_available: boolean;
    local: { commit?: string; version?: string };
    remote_commit?: string;
    remote_version?: string;
    commits_behind?: number;
    changelog?: { hash: string; message: string }[];
    error?: string;
  } | null>(null);

  const { data: status, isLoading } = useQuery({
    queryKey: ['system-status'],
    queryFn: () => get<SystemStatus>('/api/system/status'),
    refetchInterval: 15_000,
  });

  async function checkUpdate() {
    setChecking(true);
    try {
      const info = await get<typeof updateInfo>('/api/update/check');
      setUpdateInfo(info);
    } catch {
      toast('Failed to check for updates', 'error');
    } finally {
      setChecking(false);
    }
  }

  async function applyUpdate() {
    if (!confirm('Update now? NODEGLOW will back up the database, migrate and restart.')) return;
    setUpdating(true);
    try {
      const result = await post<{ ok: boolean; run_id?: string; message?: string; error?: string }>('/api/update/apply');
      if (result.ok) {
        setRunActive(true);
        toast(result.message || 'Update started', 'success');
      } else {
        toast(result.error || 'Update failed', 'error');
      }
    } catch (err) {
      let msg = 'Failed to apply update';
      if (err instanceof Error && 'data' in err) {
        try {
          const parsed = JSON.parse((err as { data?: string }).data ?? '');
          if (parsed?.error) msg = parsed.error;
        } catch { /* use default */ }
      }
      toast(msg, 'error');
    } finally {
      setUpdating(false);
    }
  }

  const handleRunFinished = useCallback((s: UpdateRunState) => {
    setRunActive(false);
    if (s.status === 'done') {
      toast('Update complete', 'success');
    } else if (s.status === 'failed') {
      toast(s.error || 'Update failed', 'error');
    }
  }, [toast]);

  const app = status?.application;
  const sys = status?.system;
  const db = status?.database;
  const ops = status?.operational;

  return (
    <div>
      <PageHeader
        title="System Status"
        description="Backend health, metrics and diagnostics"
        actions={
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>
            <RefreshCw size={14} />
            Refresh
          </Button>
        }
      />

      {/* ── Application Info ─────────────────────────────── */}
      <GlassCard className="p-4 mb-4">
        <div className="flex items-center gap-2 mb-3">
          <Server size={16} className="text-emerald-400" />
          <h3 className="text-sm font-medium text-slate-300">Application</h3>
          {app && <Badge>{app.version || 'dev'}</Badge>}
        </div>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : app ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-1">
            <StatRow label="Uptime" value={app.uptime_human} />
            <StatRow label="Hostname" value={app.hostname} />
            <StatRow label="Git Commit" value={app.git_commit} />
            <StatRow label="Python" value={app.python_version} />
            <StatRow label="PID" value={app.pid} />
            <StatRow label="Started" value={app.start_time} />
            <StatRow label="Platform" value={app.platform.split('-').slice(0, 2).join(' ')} />
          </div>
        ) : null}
      </GlassCard>

      {/* ── System Metrics (CPU / Memory / Disk) ─────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        {/* CPU */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Cpu size={16} className="text-sky-400" />
            <h3 className="text-sm font-medium text-slate-300">CPU</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : sys ? (
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-xs text-slate-400">{sys.cpu_count} cores</span>
                <span className={`text-lg font-bold ${pctTextColor(sys.cpu_pct)}`}>
                  {sys.cpu_pct}%
                </span>
              </div>
              <ProgressBar value={sys.cpu_pct} color={pctColor(sys.cpu_pct)} />
              <div className="mt-2 text-xs text-slate-500">
                Load: {sys.load_1m} / {sys.load_5m} / {sys.load_15m}
              </div>
            </div>
          ) : null}
        </GlassCard>

        {/* Memory */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <MemoryStick size={16} className="text-violet-400" />
            <h3 className="text-sm font-medium text-slate-300">Memory</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : sys ? (
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-xs text-slate-400">
                  {sys.mem_used_gb} / {sys.mem_total_gb} GB
                </span>
                <span className={`text-lg font-bold ${pctTextColor(sys.mem_pct)}`}>
                  {sys.mem_pct}%
                </span>
              </div>
              <ProgressBar value={sys.mem_pct} color={pctColor(sys.mem_pct)} />
              {sys.swap_total_gb > 0 && (
                <div className="mt-2 text-xs text-slate-500">
                  Swap: {sys.swap_used_gb} / {sys.swap_total_gb} GB ({sys.swap_pct}%)
                </div>
              )}
            </div>
          ) : null}
        </GlassCard>

        {/* Disk */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <HardDrive size={16} className="text-amber-400" />
            <h3 className="text-sm font-medium text-slate-300">Disk</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : sys ? (
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-xs text-slate-400">
                  {sys.disk_used_gb} / {sys.disk_total_gb} GB
                </span>
                <span className={`text-lg font-bold ${pctTextColor(sys.disk_pct)}`}>
                  {sys.disk_pct}%
                </span>
              </div>
              <ProgressBar value={sys.disk_pct} color={pctColor(sys.disk_pct)} />
              {sys.disk_io.read_gb !== undefined && (
                <div className="mt-2 text-xs text-slate-500">
                  I/O: {sys.disk_io.read_gb} GB read / {sys.disk_io.write_gb} GB write
                </div>
              )}
              {status?.disk_forecast && (
                <div className={`mt-2 pt-2 border-t border-white/[0.06] text-xs ${
                  status.disk_forecast.trend === 'critical' ? 'text-red-400' :
                  status.disk_forecast.trend === 'warning' ? 'text-amber-400' :
                  status.disk_forecast.trend === 'stable' ? 'text-emerald-400' :
                  'text-slate-400'
                }`}>
                  {status.disk_forecast.trend === 'stable' ? (
                    <span>Stable — no significant growth</span>
                  ) : (
                    <>
                      <span>+{status.disk_forecast.growth_gb_per_day} GB/day</span>
                      {status.disk_forecast.days_until_full != null && (
                        <span className="ml-1">
                          — full in ~{status.disk_forecast.days_until_full < 1
                            ? '<1 day'
                            : status.disk_forecast.days_until_full < 30
                              ? `${Math.round(status.disk_forecast.days_until_full)}d`
                              : `${Math.round(status.disk_forecast.days_until_full / 30)}mo`
                          }
                        </span>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ) : null}
        </GlassCard>
      </div>

      {/* ── Process + DB + Pool row ──────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        {/* Database */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Database size={16} className="text-sky-400" />
            <h3 className="text-sm font-medium text-slate-300">Database</h3>
            {db?.db_size && <Badge>{db.db_size}</Badge>}
          </div>
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : db && !db.error ? (
            <div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 mb-3">
                <StatRow label="Hosts" value={(db.host_count ?? 0).toLocaleString()} />
                <StatRow label="Ping Results" value={(db.result_count ?? 0).toLocaleString()} />
                <StatRow label="Integrations" value={(db.config_count ?? 0).toLocaleString()} />
                <StatRow label="Snapshots" value={(db.snapshot_count ?? 0).toLocaleString()} />
                <StatRow label="Syslog Messages" value={(db.syslog_count ?? 0).toLocaleString()} />
              </div>
              {status?.top_tables && status.top_tables.length > 0 && (
                <div className="border-t border-white/[0.06] pt-2 mt-2">
                  <p className="text-xs text-slate-500 mb-1">Top tables by size</p>
                  <div className="space-y-0.5">
                    {status.top_tables.slice(0, 5).map((t) => (
                      <div key={t.name} className="flex justify-between text-xs">
                        <span className="text-slate-400 font-mono">{t.name}</span>
                        <span className="text-slate-500">
                          {t.size} ({t.rows.toLocaleString()} rows)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : db?.error ? (
            <p className="text-sm text-red-400">{db.error}</p>
          ) : null}
        </GlassCard>

        {/* Operational Summary */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={16} className="text-emerald-400" />
            <h3 className="text-sm font-medium text-slate-300">Operational Summary</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : ops ? (
            <div className="space-y-3">
              {/* Ping */}
              {ops.ping_stats.checks_1h !== undefined && (
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <StatusDot status={
                      (ops.ping_stats.success_rate ?? 0) >= 95 ? 'online' :
                      (ops.ping_stats.success_rate ?? 0) >= 80 ? 'maintenance' : 'offline'
                    } />
                    <span className="text-sm text-slate-300">Ping</span>
                    <span className="text-xs text-slate-500 ml-auto">
                      {ops.ping_stats.success_rate}% success ({ops.ping_stats.checks_1h} checks/1h)
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 ml-5">
                    Avg: {ops.ping_stats.avg_latency} ms / Max: {ops.ping_stats.max_latency} ms
                  </div>
                </div>
              )}

              {/* Syslog */}
              <div className="flex items-center gap-2">
                <StatusDot status={ops.syslog_status.running ? 'online' : 'offline'} />
                <span className="text-sm text-slate-300">Syslog Receiver</span>
                <span className="text-xs text-slate-500 ml-auto">
                  {ops.syslog_status.msg_per_min !== undefined
                    ? `${ops.syslog_status.msg_per_min} msg/min`
                    : ops.syslog_status.running ? 'running' : 'stopped'}
                </span>
              </div>

              {/* Incidents */}
              {ops.incidents.total !== undefined && (
                <div className="flex items-center gap-2">
                  <AlertTriangle size={14} className={
                    (ops.incidents.open ?? 0) > 0 ? 'text-red-400' : 'text-slate-500'
                  } />
                  <span className="text-sm text-slate-300">Incidents</span>
                  <span className="text-xs text-slate-500 ml-auto">
                    {ops.incidents.open} open / {ops.incidents.acknowledged} ack / {ops.incidents.resolved} resolved
                  </span>
                </div>
              )}

              {/* Notifications */}
              <div className="flex items-center gap-2 flex-wrap">
                <Shield size={14} className="text-slate-500" />
                <span className="text-sm text-slate-300">Notifications</span>
                <div className="flex gap-1 ml-auto">
                  {ops.notification_channels.telegram && <Badge>Telegram</Badge>}
                  {ops.notification_channels.discord && <Badge>Discord</Badge>}
                  {ops.notification_channels.email && <Badge>Email</Badge>}
                  {!ops.notification_channels.telegram && !ops.notification_channels.discord && !ops.notification_channels.email && (
                    <span className="text-xs text-slate-600">none configured</span>
                  )}
                </div>
              </div>

              {/* Maintenance */}
              {(ops.maintenance.active ?? 0) > 0 && (
                <div className="flex items-center gap-2">
                  <Timer size={14} className="text-amber-400" />
                  <span className="text-sm text-slate-300">Maintenance</span>
                  <span className="text-xs text-slate-500 ml-auto">
                    {ops.maintenance.active} hosts in maintenance
                  </span>
                </div>
              )}
            </div>
          ) : null}
        </GlassCard>
      </div>

      {/* ── Integration Health + Background Services row ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        {/* Integration Health */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Plug size={16} className="text-sky-400" />
            <h3 className="text-sm font-medium text-slate-300">Integration Health</h3>
            {status?.integrations && (
              <Badge>{status.integrations.length} active</Badge>
            )}
          </div>
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : status?.integrations && status.integrations.length > 0 ? (
            <div className="space-y-1.5 max-h-[260px] overflow-y-auto">
              {status.integrations.map((int) => (
                <div key={`${int.type}-${int.name}`} className="flex items-center gap-2">
                  <StatusDot status={int.ok === true ? 'online' : int.ok === false ? 'offline' : 'unknown'} />
                  <span className="text-sm text-slate-300 truncate flex-1">{int.name}</span>
                  <Badge>{int.type}</Badge>
                  <span className="text-xs text-slate-500">{int.last_check}</span>
                  {int.error && (
                    <span className="text-xs text-red-400 truncate max-w-[120px]" title={int.error}>
                      {int.error}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">No integrations configured</p>
          )}
        </GlassCard>

        {/* Background Services (scheduler jobs) */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Clock size={16} className="text-amber-400" />
            <h3 className="text-sm font-medium text-slate-300">Background Services</h3>
            {status?.scheduler_jobs && (
              <Badge>{status.scheduler_jobs.length} jobs</Badge>
            )}
          </div>
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : status?.scheduler_jobs && status.scheduler_jobs.length > 0 ? (
            <div className="space-y-1.5 max-h-[260px] overflow-y-auto">
              {status.scheduler_jobs.map((job) => (
                <div key={job.id} className="flex items-center gap-2">
                  <StatusDot status={job.next_run === 'paused' ? 'maintenance' : 'online'} />
                  <span className="text-sm text-slate-300 truncate flex-1">{job.name}</span>
                  <span className="text-xs text-slate-500 font-mono">{job.next_run}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">No scheduler jobs found</p>
          )}
        </GlassCard>
      </div>

      {/* ── Process Info + Connection Pool ────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        {/* Process */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={16} className="text-violet-400" />
            <h3 className="text-sm font-medium text-slate-300">Process</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : status?.process ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <StatRow label="RSS" value={`${status.process.rss_mb} MB`} />
              <StatRow label="VMS" value={`${status.process.vms_mb} MB`} />
              <StatRow label="Threads" value={status.process.threads} />
              <StatRow label="Open Files" value={status.process.open_files} />
              <StatRow label="Connections" value={status.process.connections} />
              <StatRow label="CPU (user/sys)" value={`${status.process.cpu_user}s / ${status.process.cpu_system}s`} />
            </div>
          ) : null}
        </GlassCard>

        {/* Connection Pool */}
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Database size={16} className="text-emerald-400" />
            <h3 className="text-sm font-medium text-slate-300">Connection Pool</h3>
          </div>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : status?.pool ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <StatRow label="Pool Size" value={status.pool.size ?? 0} />
              <StatRow label="Checked In" value={status.pool.checked_in ?? 0} />
              <StatRow label="Checked Out" value={status.pool.checked_out ?? 0} />
              <StatRow label="Overflow" value={`${status.pool.overflow ?? 0} / ${status.pool.max_overflow ?? 0}`} />
            </div>
          ) : null}
        </GlassCard>
      </div>

      {/* ── Software Updates ─────────────────────────────── */}
      <GlassCard className="p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Download size={16} className="text-violet-400" />
            <h3 className="text-sm font-medium text-slate-300">Software Updates</h3>
          </div>
          <div className="flex gap-2">
            {updateInfo?.update_available && (
              <Button size="sm" variant="primary" onClick={applyUpdate} disabled={updating || runActive}>
                <Download size={14} className={updating || runActive ? 'animate-bounce' : ''} />
                {updating || runActive ? 'Updating...' : 'Update Now'}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={checkUpdate} disabled={checking}>
              <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
              {checking ? 'Checking...' : 'Check Now'}
            </Button>
          </div>
        </div>
        {updateInfo ? (
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Badge>{updateInfo.local?.version || updateInfo.local?.commit || '—'}</Badge>
              {updateInfo.update_available ? (
                <Badge variant="severity" severity="warning">
                  {updateInfo.commits_behind} commit{updateInfo.commits_behind !== 1 ? 's' : ''} behind
                </Badge>
              ) : (
                <Badge variant="severity" severity="info">Up to date</Badge>
              )}
              {updateInfo.error && (
                <span className="text-xs text-amber-400">{updateInfo.error}</span>
              )}
            </div>
            {updateInfo.changelog && updateInfo.changelog.length > 0 && (
              <div className="bg-white/[0.02] rounded p-3 mt-2 max-h-[200px] overflow-y-auto space-y-1">
                {updateInfo.changelog.map((entry) => (
                  <div key={entry.hash} className="flex gap-2 text-xs">
                    <code className="text-sky-400 shrink-0">{entry.hash}</code>
                    <span className="text-slate-400">{entry.message}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Click &quot;Check Now&quot; to check for updates</p>
        )}
        <UpdateProgress active={runActive} onFinished={handleRunFinished} />
      </GlassCard>

      {/* Dashboard API Performance */}
      {status?.dashboard_perf?.sections && (
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={16} className="text-emerald-400" />
            <h3 className="text-sm font-semibold text-slate-200">Dashboard API Performance</h3>
          </div>
          <div className="flex items-center gap-3 mb-3">
            <span className="text-2xl font-bold text-slate-100">{status.dashboard_perf.total_ms}ms</span>
            <Badge variant="severity" severity={status.dashboard_perf.total_ms < 1000 ? 'info' : status.dashboard_perf.total_ms < 3000 ? 'warning' : 'critical'}>
              {status.dashboard_perf.total_ms < 1000 ? 'Fast' : status.dashboard_perf.total_ms < 3000 ? 'Slow' : 'Very Slow'}
            </Badge>
            {status.dashboard_perf.timestamp && (
              <span className="text-xs text-slate-500 ml-auto">Last: {new Date(status.dashboard_perf.timestamp + 'Z').toLocaleTimeString('de-CH')}</span>
            )}
          </div>
          <div className="space-y-1">
            {status.dashboard_perf.sections.filter(s => s.name !== 'init' && s.name !== 'done').map((s) => {
              const pct = status.dashboard_perf!.total_ms > 0 ? (s.ms / status.dashboard_perf!.total_ms) * 100 : 0;
              const color = s.ms < 50 ? 'bg-emerald-500' : s.ms < 200 ? 'bg-sky-500' : s.ms < 1000 ? 'bg-amber-500' : 'bg-red-500';
              return (
                <div key={s.name} className="flex items-center gap-2 text-xs">
                  <span className="w-32 text-slate-400 truncate">{s.name}</span>
                  <div className="flex-1 h-2 bg-white/5 rounded-full overflow-hidden">
                    <div className={`h-full ${color} rounded-full`} style={{ width: `${Math.max(1, pct)}%` }} />
                  </div>
                  <span className="w-16 text-right text-slate-500 tabular-nums">{s.ms}ms</span>
                </div>
              );
            })}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
