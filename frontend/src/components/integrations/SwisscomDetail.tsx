'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { StatusDot } from '@/components/ui/StatusDot';
import { formatUptime } from '@/lib/utils';

interface SwisscomDevice {
  model: string;
  serial: string;
  firmware: string;
  mac: string;
  uptime_s: number;
  manufacturer: string;
  external_ip: string;
  hardware: string;
  status: string;
  reboots: number;
  first_use: string;
  mem_total_kb?: number;
  mem_free_kb?: number;
  mem_pct?: number;
}

interface SwisscomHost {
  name: string;
  mac: string;
  ip: string;
  active: boolean;
  device_type: string;
  first_seen: string;
  last_connection: string;
}

interface SwisscomData {
  device: SwisscomDevice;
  wan: Record<string, string>;
  wifi: { enabled?: boolean; scheduler?: boolean };
  hosts: SwisscomHost[];
  hosts_active: number;
  hosts_total: number;
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
      {sub && <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>}
    </GlassCard>
  );
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

export function SwisscomDetail({ data }: { data: SwisscomData }) {
  const d = data.device;
  const activeHosts = data.hosts.filter((h) => h.active);
  const inactiveHosts = data.hosts.filter((h) => !h.active);

  return (
    <div className="space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Uptime" value={formatUptime(d.uptime_s)} />
        <StatCard label="External IP" value={d.external_ip || '—'} />
        <StatCard label="Connected Devices" value={data.hosts_active} sub={`${data.hosts_total} total`} />
        <StatCard label="WAN" value={data.wan?.interface || '—'} />
      </div>

      {/* Device Info + Memory */}
      <GlassCard className="p-4 space-y-4">
        <h3 className="text-sm font-medium text-slate-300">Device Info</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
          <div>
            <span className="text-slate-500">Model</span>
            <p className="text-slate-200 mt-1 font-medium">{d.model}</p>
          </div>
          <div>
            <span className="text-slate-500">Firmware</span>
            <p className="text-slate-300 mt-1 font-mono">{d.firmware}</p>
          </div>
          <div>
            <span className="text-slate-500">Hardware</span>
            <p className="text-slate-300 mt-1">{d.hardware}</p>
          </div>
          <div>
            <span className="text-slate-500">Manufacturer</span>
            <p className="text-slate-300 mt-1">{d.manufacturer}</p>
          </div>
          <div>
            <span className="text-slate-500">Serial</span>
            <p className="text-slate-300 mt-1 font-mono text-[11px]">{d.serial}</p>
          </div>
          <div>
            <span className="text-slate-500">MAC</span>
            <p className="text-slate-300 mt-1 font-mono">{d.mac}</p>
          </div>
          <div>
            <span className="text-slate-500">Status</span>
            <p className="text-slate-300 mt-1">
              <Badge variant="severity" severity={d.status === 'Up' ? 'info' : 'critical'}>
                {d.status}
              </Badge>
            </p>
          </div>
          <div>
            <span className="text-slate-500">Reboots</span>
            <p className="text-slate-300 mt-1">{d.reboots}</p>
          </div>
        </div>

        {/* Memory bar */}
        {d.mem_pct != null && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-slate-400">
              <span>Memory</span>
              <span>
                {d.mem_pct}%
                {d.mem_total_kb ? ` (${Math.round((d.mem_total_kb - (d.mem_free_kb ?? 0)) / 1024)} / ${Math.round(d.mem_total_kb / 1024)} MB)` : ''}
              </span>
            </div>
            <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${barColor(d.mem_pct)}`} style={{ width: `${Math.min(d.mem_pct, 100)}%` }} />
            </div>
          </div>
        )}
      </GlassCard>

      {/* WAN + WiFi */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">WAN</h3>
          <div className="space-y-2 text-xs">
            {Object.entries(data.wan).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-slate-500">{k.replace(/_/g, ' ')}</span>
                <span className="text-slate-300 font-mono">{String(v)}</span>
              </div>
            ))}
          </div>
        </GlassCard>
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">WiFi</h3>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-500">Status</span>
              <Badge variant="severity" severity={data.wifi?.enabled ? 'info' : 'warning'}>
                {data.wifi?.enabled ? 'Enabled' : 'Disabled'}
              </Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Scheduler</span>
              <span className="text-slate-300">{data.wifi?.scheduler ? 'Active' : 'Off'}</span>
            </div>
          </div>
        </GlassCard>
      </div>

      {/* Connected Devices */}
      <GlassCard className="overflow-hidden">
        <div className="px-4 py-3 border-b border-white/[0.06] flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-300">
            Connected Devices
          </h3>
          <div className="flex items-center gap-2">
            <Badge>{data.hosts_active} active</Badge>
            <Badge variant="severity" severity="warning">{inactiveHosts.length} inactive</Badge>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Name</th>
                <th className="px-4 py-2 text-left">IP</th>
                <th className="px-4 py-2 text-left">MAC</th>
                <th className="px-4 py-2 text-left">Type</th>
              </tr>
            </thead>
            <tbody>
              {[...activeHosts, ...inactiveHosts].map((host) => (
                <tr key={host.mac} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                  <td className="px-4 py-2">
                    <StatusDot status={host.active ? 'online' : 'offline'} />
                  </td>
                  <td className="px-4 py-2 text-slate-200 font-medium">{host.name || '—'}</td>
                  <td className="px-4 py-2 text-slate-400 font-mono text-xs">{host.ip || '—'}</td>
                  <td className="px-4 py-2 text-slate-500 font-mono text-xs">{host.mac}</td>
                  <td className="px-4 py-2 text-slate-400 text-xs">{host.device_type || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}
