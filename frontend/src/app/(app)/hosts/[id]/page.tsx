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
import { ArrowLeft, RefreshCw, Cpu, MemoryStick, HardDrive, Clock, Activity, Network, Wifi, Pencil, Cable, Zap, Users, ArrowUpDown } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, patch } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { Modal } from '@/components/ui/Modal';
import type { EChartsOption } from 'echarts';

interface SyslogEntry {
  timestamp: string;
  severity: number;
  hostname: string;
  source_ip: string;
  app_name: string;
  message: string;
}

interface DiskInfo {
  mount: string;
  total_gb: number;
  used_gb: number;
  pct: number;
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
  load_1: number | null;
  load_5: number | null;
  load_15: number | null;
  uptime_s: number | null;
  rx_bytes: number | null;
  tx_bytes: number | null;
  snapshot_time: string | null;
  extra: {
    disks?: DiskInfo[];
    network?: { rx_bytes?: number; tx_bytes?: number; rx_rate?: number; tx_rate?: number };
    cpu_pct?: number;
    [key: string]: unknown;
  } | null;
}

const sevLabels: Record<number, string> = {
  0: 'Emergency', 1: 'Alert', 2: 'Critical', 3: 'Error',
  4: 'Warning', 5: 'Notice', 6: 'Info', 7: 'Debug',
};
const sevColors: Record<number, string> = {
  0: 'text-red-500', 1: 'text-red-400', 2: 'text-red-400', 3: 'text-orange-400',
  4: 'text-amber-400', 5: 'text-blue-400', 6: 'text-slate-400', 7: 'text-slate-500',
};

type Tab = 'overview' | 'ports' | 'syslog';

interface PortInfo {
  idx: number;
  name: string;
  enable: boolean;
  up: boolean;
  speed: number;
  speed_label: string;
  is_uplink: boolean;
  poe_enable: boolean;
  poe_power: number;
  rx_bytes_r: number;
  tx_bytes_r: number;
  rx_bytes: number;
  tx_bytes: number;
  satisfaction: number;
  op_mode: string;
}

interface ConnectedClient {
  mac: string;
  hostname: string;
  ip: string;
  sw_port?: number;
  is_wireless: boolean;
  rx_bytes_r: number;
  tx_bytes_r: number;
  vlan: number;
  ssid: string;
  signal: number;
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

function formatUptime(seconds: number | null): string {
  if (!seconds || seconds <= 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
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
  const [showEdit, setShowEdit] = useState(false);
  const qc = useQueryClient();
  const toast = useToastStore((s) => s.show);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { data: host, isLoading } = useHost(hostId) as { data: any; isLoading: boolean };
  const { data: history, isLoading: historyLoading } = useHostHistory(hostId, 24);

  const agent: AgentMetrics | null = host?.agent ?? null;
  const device = host?.integration?.device ?? null;
  const hasPorts = device?.has_ports && device?.port_table?.length > 0;

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    ...(hasPorts ? [{ key: 'ports' as const, label: `Ports (${device.port_table.length})` }] : []),
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
            <Button variant="ghost" size="sm" onClick={() => setShowEdit(true)}>
              <Pencil size={16} />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => {
              qc.invalidateQueries({ queryKey: ['host', hostId] });
              qc.invalidateQueries({ queryKey: ['host-history', hostId] });
            }}>
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
              {host.health_pct != null && (
                <div className="flex items-center gap-2" title={`Health Score: ${host.health_pct}%`}>
                  <div className="w-16 h-2 rounded-full bg-white/[0.06] overflow-hidden">
                    <div
                      className={`h-full rounded-full ${
                        host.health_pct >= 90 ? 'bg-emerald-500' : host.health_pct >= 70 ? 'bg-amber-500' : 'bg-red-500'
                      }`}
                      style={{ width: `${host.health_pct}%` }}
                    />
                  </div>
                  <span className={`text-xs font-mono ${
                    host.health_pct >= 90 ? 'text-emerald-400' : host.health_pct >= 70 ? 'text-amber-400' : 'text-red-400'
                  }`}>
                    {host.health_pct}%
                  </span>
                </div>
              )}
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
          {/* Agent Metrics */}
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
                {(agent.load_1 != null) && (
                  <MetricCard
                    icon={Activity}
                    label="Load Average"
                    value={`${agent.load_1.toFixed(2)}`}
                    sub={[agent.load_1, agent.load_5, agent.load_15].filter(v => v != null).map(v => v!.toFixed(2)).join(' / ')}
                    color="text-orange-400"
                  />
                )}
                {(agent.rx_bytes != null || agent.tx_bytes != null) && (
                  <MetricCard
                    icon={Network}
                    label="Network I/O"
                    value={`${formatBytes(agent.rx_bytes)}`}
                    sub={`TX: ${formatBytes(agent.tx_bytes)}`}
                    color="text-cyan-400"
                  />
                )}
              </div>
            </div>
          )}

          {/* Integration Device Metrics (UniFi, Proxmox, etc.) */}
          {!agent && device && (
            <div>
              <h3 className="text-sm font-medium text-slate-300 mb-3">
                Device Metrics
                {device.type_label && (
                  <Badge className="ml-2">{device.type_label}</Badge>
                )}
                {device.model && (
                  <span className="text-xs text-slate-500 ml-2 font-normal">{device.model}</span>
                )}
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {device.cpu_pct != null && (
                  <MetricCard
                    icon={Cpu}
                    label="CPU"
                    value={`${device.cpu_pct}%`}
                    pct={device.cpu_pct}
                    color="text-sky-400"
                  />
                )}
                {device.mem_pct != null && (
                  <MetricCard
                    icon={MemoryStick}
                    label="Memory"
                    value={`${device.mem_pct}%`}
                    pct={device.mem_pct}
                    color="text-violet-400"
                  />
                )}
                {device.uptime_s != null && device.uptime_s > 0 && (
                  <MetricCard
                    icon={Clock}
                    label="Device Uptime"
                    value={formatUptime(device.uptime_s)}
                    color="text-emerald-400"
                  />
                )}
                {(device.clients_wifi != null || device.clients_wired != null) && (
                  <MetricCard
                    icon={Users}
                    label="Clients"
                    value={String((device.clients_wifi ?? 0) + (device.clients_wired ?? 0))}
                    sub={`WiFi: ${device.clients_wifi ?? 0} / Wired: ${device.clients_wired ?? 0}`}
                    color="text-cyan-400"
                  />
                )}
                {(device.rx_bytes != null || device.tx_bytes != null) && (
                  <MetricCard
                    icon={Network}
                    label="Traffic"
                    value={formatBytes(device.rx_bytes)}
                    sub={`TX: ${formatBytes(device.tx_bytes)}`}
                    color="text-orange-400"
                  />
                )}
                {device.satisfaction != null && device.satisfaction >= 0 && (
                  <MetricCard
                    icon={Activity}
                    label="Satisfaction"
                    value={`${device.satisfaction}%`}
                    pct={device.satisfaction}
                    color="text-emerald-400"
                  />
                )}
              </div>
            </div>
          )}

          {/* Port Summary (quick view on overview tab) */}
          {hasPorts && (
            <GlassCard className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2">
                  <Cable size={14} className="text-sky-400" />
                  Port Summary
                </h3>
                <button
                  onClick={() => setActiveTab('ports')}
                  className="text-xs text-sky-400 hover:text-sky-300 transition-colors"
                >
                  View all ports →
                </button>
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {(device.port_table as PortInfo[]).map((port: PortInfo) => (
                  <div
                    key={port.idx}
                    className={`w-8 h-8 rounded flex items-center justify-center text-[10px] font-mono border transition-colors ${
                      !port.enable
                        ? 'bg-white/[0.02] border-white/[0.04] text-slate-600'
                        : port.up
                          ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                          : 'bg-white/[0.04] border-white/[0.06] text-slate-500'
                    }`}
                    title={`Port ${port.idx}: ${port.up ? 'Up' : port.enable ? 'Down' : 'Disabled'}${port.speed_label ? ` (${port.speed_label})` : ''}${port.is_uplink ? ' [Uplink]' : ''}${port.poe_enable ? ` PoE: ${port.poe_power}W` : ''}`}
                  >
                    {port.idx}
                  </div>
                ))}
              </div>
              <div className="flex gap-4 mt-3 text-[10px] text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded bg-emerald-500/30 border border-emerald-500/50" />
                  Up ({(device.port_table as PortInfo[]).filter((p: PortInfo) => p.up).length})
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded bg-white/[0.04] border border-white/[0.08]" />
                  Down ({(device.port_table as PortInfo[]).filter((p: PortInfo) => p.enable && !p.up).length})
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-2.5 h-2.5 rounded bg-white/[0.02] border border-white/[0.04]" />
                  Disabled ({(device.port_table as PortInfo[]).filter((p: PortInfo) => !p.enable).length})
                </span>
              </div>
            </GlassCard>
          )}

          {/* All Disks (from agent extra data) */}
          {agent?.extra?.disks && agent.extra.disks.length > 0 && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3">Disk Usage</h3>
              <div className="space-y-3">
                {agent.extra.disks.map((disk: DiskInfo) => (
                  <div key={disk.mount}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-slate-400 font-mono">{disk.mount}</span>
                      <span className="text-xs text-slate-400">
                        {disk.used_gb?.toFixed(1)} / {disk.total_gb?.toFixed(1)} GB
                        <span className={`ml-2 font-mono ${pctTextColor(disk.pct)}`}>{disk.pct?.toFixed(1)}%</span>
                      </span>
                    </div>
                    <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                      <div className={`h-full rounded-full ${pctColor(disk.pct)}`} style={{ width: `${Math.min(disk.pct, 100)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </GlassCard>
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
                {agent.snapshot_time && (
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-400">Snapshot</span>
                    <span className="text-sm text-slate-200">
                      {new Date(agent.snapshot_time).toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            </GlassCard>
          )}

          {/* Integration Device Details */}
          {host?.integration && (
            <GlassCard className="p-4">
              <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
                <Wifi size={14} className="text-violet-400" />
                Integration: {host.integration.type} — {host.integration.config_name}
              </h3>
              {device ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-2">
                  {device.ip && (
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">IP</span>
                      <span className="text-xs text-slate-300 font-mono">{device.ip}</span>
                    </div>
                  )}
                  {device.mac && (
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">MAC</span>
                      <span className="text-xs text-slate-300 font-mono">{device.mac}</span>
                    </div>
                  )}
                  {device.version && (
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">Firmware</span>
                      <span className="text-xs text-slate-300 font-mono">{device.version}</span>
                    </div>
                  )}
                  {device.type_label && (
                    <div className="flex justify-between">
                      <span className="text-xs text-slate-500">Type</span>
                      <span className="text-xs text-slate-300">{device.type_label}</span>
                    </div>
                  )}
                </div>
              ) : (() => {
                let data = host.integration.data;
                if (typeof data === 'string') {
                  try { data = JSON.parse(data); } catch { /* keep as-is */ }
                }
                if (!data || typeof data !== 'object') return null;
                const entries = Object.entries(data as Record<string, unknown>).filter(
                  ([, val]) => val != null && typeof val !== 'object'
                );
                if (!entries.length) return null;
                return (
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2">
                    {entries.map(([key, val]) => (
                      <div key={key} className="flex justify-between">
                        <span className="text-xs text-slate-500">{key.replace(/_/g, ' ')}</span>
                        <span className="text-xs text-slate-300 font-mono truncate max-w-[60%]">{String(val)}</span>
                      </div>
                    ))}
                  </div>
                );
              })()}
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

      {activeTab === 'ports' && hasPorts && (
        <PortsTab ports={device.port_table} clients={device.connected_clients ?? []} />
      )}

      {activeTab === 'syslog' && (
        <HostSyslog hostId={hostId} />
      )}

      {/* Edit Host Modal */}
      {host && (
        <EditHostModal
          open={showEdit}
          onClose={() => setShowEdit(false)}
          host={host}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['host', hostId] });
            qc.invalidateQueries({ queryKey: ['hosts'] });
            toast('Host updated', 'success');
          }}
        />
      )}
    </div>
  );
}

/* ── Ports Tab ── */

function formatRate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null || bytesPerSec <= 0) return '—';
  const bits = bytesPerSec * 8;
  if (bits < 1000) return `${bits.toFixed(0)} bps`;
  if (bits < 1_000_000) return `${(bits / 1000).toFixed(1)} Kbps`;
  if (bits < 1_000_000_000) return `${(bits / 1_000_000).toFixed(1)} Mbps`;
  return `${(bits / 1_000_000_000).toFixed(2)} Gbps`;
}

function PortsTab({ ports, clients }: { ports: PortInfo[]; clients: ConnectedClient[] }) {
  // Group clients by switch port
  const clientsByPort: Record<number, ConnectedClient[]> = {};
  for (const c of clients) {
    if (c.sw_port != null) {
      if (!clientsByPort[c.sw_port]) clientsByPort[c.sw_port] = [];
      clientsByPort[c.sw_port].push(c);
    }
  }

  const portsUp = ports.filter(p => p.up).length;
  const totalPoe = ports.filter(p => p.poe_enable && p.up).reduce((s, p) => s + p.poe_power, 0);

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard icon={Cable} label="Ports Up" value={`${portsUp} / ${ports.length}`} color="text-emerald-400" />
        <MetricCard icon={Users} label="Connected Clients" value={String(clients.length)} color="text-cyan-400" />
        {totalPoe > 0 && (
          <MetricCard icon={Zap} label="PoE Power" value={`${totalPoe.toFixed(1)}W`} color="text-amber-400" />
        )}
        <MetricCard
          icon={ArrowUpDown}
          label="Total Traffic"
          value={formatRate(ports.reduce((s, p) => s + (p.rx_bytes_r || 0), 0))}
          sub={`TX: ${formatRate(ports.reduce((s, p) => s + (p.tx_bytes_r || 0), 0))}`}
          color="text-violet-400"
        />
      </div>

      {/* Port table */}
      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Port</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Speed</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">PoE</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">RX Rate</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">TX Rate</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Clients</th>
              </tr>
            </thead>
            <tbody>
              {ports.map((port) => {
                const portClients = clientsByPort[port.idx] ?? [];
                return (
                  <tr key={port.idx} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${
                          !port.enable ? 'bg-slate-600' : port.up ? 'bg-emerald-400' : 'bg-slate-500'
                        }`} />
                        <span className="text-slate-200 font-medium">{port.name}</span>
                        {port.is_uplink && (
                          <Badge variant="severity" severity="info">Uplink</Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs font-medium ${
                        !port.enable ? 'text-slate-600' : port.up ? 'text-emerald-400' : 'text-slate-500'
                      }`}>
                        {!port.enable ? 'Disabled' : port.up ? 'Up' : 'Down'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-300 font-mono">
                        {port.up && port.speed_label ? port.speed_label : '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {port.poe_enable ? (
                        <span className="text-xs text-amber-400 flex items-center gap-1">
                          <Zap size={10} />
                          {port.poe_power > 0 ? `${port.poe_power}W` : 'On'}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-300 font-mono">
                        {port.up ? formatRate(port.rx_bytes_r) : '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs text-slate-300 font-mono">
                        {port.up ? formatRate(port.tx_bytes_r) : '—'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {portClients.length > 0 ? (
                        <span className="text-xs text-cyan-400">{portClients.length}</span>
                      ) : (
                        <span className="text-xs text-slate-600">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </GlassCard>

      {/* Connected clients list */}
      {clients.length > 0 && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <Users size={14} className="text-cyan-400" />
            Connected Clients ({clients.length})
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">Client</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">IP</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">Port</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">Type</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">VLAN</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 uppercase">Traffic</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <tr key={c.mac} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                    <td className="px-3 py-2">
                      <p className="text-xs text-slate-200">{c.hostname}</p>
                      <p className="text-[10px] text-slate-500 font-mono">{c.mac}</p>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-300 font-mono">{c.ip || '—'}</td>
                    <td className="px-3 py-2 text-xs text-slate-400">{c.sw_port ?? '—'}</td>
                    <td className="px-3 py-2">
                      <span className={`text-xs ${c.is_wireless ? 'text-violet-400' : 'text-cyan-400'}`}>
                        {c.is_wireless ? `WiFi${c.ssid ? ` (${c.ssid})` : ''}` : 'Wired'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-400">{c.vlan || '—'}</td>
                    <td className="px-3 py-2 text-xs text-slate-400 font-mono">
                      ↓{formatRate(c.rx_bytes_r)} ↑{formatRate(c.tx_bytes_r)}
                    </td>
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

/* ── Edit Host Modal ── */

const editInputClass = 'w-full px-3 py-2 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50';
const editSelectClass = 'w-full px-3 py-2 text-sm bg-[#111621] border border-white/[0.08] rounded-lg text-slate-200 focus:outline-none focus:border-sky-500/50 [&>option]:bg-[#111621] [&>option]:text-slate-200';

function EditHostModal({ open, onClose, host, onSaved }: {
  open: boolean;
  onClose: () => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  host: any;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: '',
    hostname: '',
    check_type: 'icmp',
    port: '',
    latency_threshold_ms: '',
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  // Sync form when modal opens
  useEffect(() => {
    if (open && host) {
      setForm({
        name: host.name ?? '',
        hostname: host.hostname ?? '',
        check_type: host.check_type ?? 'icmp',
        port: host.port ? String(host.port) : '',
        latency_threshold_ms: host.latency_threshold_ms ? String(host.latency_threshold_ms) : '',
        enabled: host.enabled !== false,
      });
    }
  }, [open, host]);

  async function handleSave() {
    setSaving(true);
    try {
      await patch(`/api/v1/hosts/${host.id}`, {
        name: form.name,
        hostname: form.hostname,
        check_type: form.check_type,
        port: form.port ? Number(form.port) : null,
        latency_threshold_ms: form.latency_threshold_ms ? Number(form.latency_threshold_ms) : null,
        enabled: form.enabled,
      });
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit Host">
      <div className="space-y-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1">Name</label>
          <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={editInputClass} />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">Hostname / IP</label>
          <input type="text" value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} className={editInputClass} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Check Type</label>
            <select value={form.check_type} onChange={(e) => setForm({ ...form, check_type: e.target.value })} className={editSelectClass}>
              <option value="icmp">ICMP (Ping)</option>
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="tcp">TCP</option>
              <option value="dns">DNS</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Port</label>
            <input type="text" placeholder="optional" value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} className={editInputClass} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Latency Threshold (ms)</label>
            <input type="number" placeholder="200" value={form.latency_threshold_ms} onChange={(e) => setForm({ ...form, latency_threshold_ms: e.target.value })} className={editInputClass} />
          </div>
          <div className="flex items-end pb-1">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="rounded border-white/20 bg-white/[0.06]" />
              <span className="text-sm text-slate-300">Enabled</span>
            </label>
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={handleSave} disabled={saving || !form.name || !form.hostname}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </div>
      </div>
    </Modal>
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
