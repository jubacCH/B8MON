'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatUptime } from '@/lib/utils';

interface FirewallInterface {
  name?: string;
  status?: string;
  ipaddr?: string;
  media?: string;
  [key: string]: unknown;
}

interface FirewallData {
  fw_type: string;
  version: string;
  hostname: string;
  cpu_pct: number;
  mem_pct: number;
  uptime_s: number;
  interfaces: FirewallInterface[];
  alerts: number;
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function ProgressBar({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span>{pct.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${barColor(pct)}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function FirewallDetail({ data }: { data: FirewallData }) {
  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Type" value={data.fw_type.toUpperCase()} />
        <StatCard label="Uptime" value={formatUptime(data.uptime_s)} />
        <StatCard label="Interfaces" value={data.interfaces?.length ?? 0} />
        <StatCard label="Alerts" value={data.alerts ?? 0} />
      </div>

      {/* System info + metrics */}
      <GlassCard className="p-4 space-y-4">
        <h3 className="text-sm font-medium text-slate-300">System</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-xs">
          <div><span className="text-slate-500">Hostname</span><p className="text-slate-200 mt-1 font-mono">{data.hostname}</p></div>
          <div><span className="text-slate-500">Version</span><p className="text-slate-300 mt-1">{data.version}</p></div>
          <div><span className="text-slate-500">Firewall</span><p className="text-slate-300 mt-1">{data.fw_type}</p></div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ProgressBar label="CPU" pct={data.cpu_pct} />
          <ProgressBar label="Memory" pct={data.mem_pct} />
        </div>
      </GlassCard>

      {/* Interfaces */}
      {data.interfaces && data.interfaces.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Interfaces</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-left">IP Address</th>
                  <th className="px-4 py-2 text-left">Media</th>
                </tr>
              </thead>
              <tbody>
                {data.interfaces.map((iface, i) => (
                  <tr key={iface.name ?? i} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300 font-mono">{iface.name ?? '—'}</td>
                    <td className="px-4 py-2">
                      <Badge variant="severity" severity={iface.status === 'up' ? 'info' : 'warning'}>
                        {iface.status ?? 'unknown'}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-slate-400 font-mono">{iface.ipaddr ?? '—'}</td>
                    <td className="px-4 py-2 text-slate-400">{iface.media ?? '—'}</td>
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
