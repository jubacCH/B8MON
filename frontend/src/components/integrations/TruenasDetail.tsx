'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatUptime } from '@/lib/utils';

interface TruenasSystem {
  hostname: string;
  version: string;
  uptime_s: number;
  platform: string;
  model: string;
}

interface TruenasPool {
  name: string;
  status: string;
  healthy: boolean;
  size_gb: number;
  used_gb: number;
  free_gb: number;
  pct: number;
}

interface TruenasDisk {
  name: string;
  serial: string;
  model: string;
  size_gb: number;
  temp: number | null;
  type: string;
}

interface TruenasAlert {
  level: string;
  message: string;
  date: string;
}

interface TruenasTotals {
  pools_total: number;
  pools_healthy: number;
  disks_total: number;
  storage_used_gb: number;
  storage_total_gb: number;
  storage_pct: number;
}

interface TruenasData {
  system: TruenasSystem;
  storage_pools: TruenasPool[];
  disks: TruenasDisk[];
  alerts: TruenasAlert[];
  totals: TruenasTotals;
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function TruenasDetail({ data }: { data: TruenasData }) {
  const { system, storage_pools, disks, alerts, totals } = data;

  return (
    <div className="space-y-6">
      {/* System info */}
      <GlassCard className="p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">System</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-xs">
          <div><span className="text-slate-500">Hostname</span><p className="text-slate-200 mt-1 font-mono">{system.hostname}</p></div>
          <div><span className="text-slate-500">Version</span><p className="text-slate-300 mt-1">{system.version}</p></div>
          <div><span className="text-slate-500">Platform</span><p className="text-slate-300 mt-1">{system.platform}</p></div>
          <div><span className="text-slate-500">Model</span><p className="text-slate-300 mt-1">{system.model}</p></div>
          <div><span className="text-slate-500">Uptime</span><p className="text-slate-300 mt-1">{formatUptime(system.uptime_s)}</p></div>
        </div>
      </GlassCard>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Pools" value={`${totals.pools_healthy}/${totals.pools_total} healthy`} />
        <StatCard label="Disks" value={totals.disks_total} />
        <StatCard label="Used" value={`${totals.storage_used_gb.toFixed(1)} GB`} />
        <StatCard label="Storage" value={`${totals.storage_pct.toFixed(1)}%`} />
      </div>

      {/* Storage pools */}
      {storage_pools && storage_pools.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Storage Pools</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {storage_pools.map((pool) => (
              <GlassCard key={pool.name} className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{pool.name}</span>
                  <Badge variant="severity" severity={pool.healthy ? 'info' : 'critical'}>
                    {pool.status}
                  </Badge>
                </div>
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-slate-400">
                    <span>Usage</span>
                    <span>{pool.used_gb.toFixed(1)} / {pool.size_gb.toFixed(1)} GB ({pool.pct.toFixed(1)}%)</span>
                  </div>
                  <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${barColor(pool.pct)}`} style={{ width: `${Math.min(pool.pct, 100)}%` }} />
                  </div>
                </div>
              </GlassCard>
            ))}
          </div>
        </div>
      )}

      {/* Disks table */}
      {disks && disks.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Disks</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-left">Model</th>
                  <th className="px-4 py-2 text-left">Serial</th>
                  <th className="px-4 py-2 text-left">Type</th>
                  <th className="px-4 py-2 text-right">Size</th>
                  <th className="px-4 py-2 text-right">Temp</th>
                </tr>
              </thead>
              <tbody>
                {disks.map((d) => (
                  <tr key={d.name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300 font-mono">{d.name}</td>
                    <td className="px-4 py-2 text-slate-400">{d.model}</td>
                    <td className="px-4 py-2 text-slate-500 font-mono text-xs">{d.serial}</td>
                    <td className="px-4 py-2"><Badge>{d.type}</Badge></td>
                    <td className="px-4 py-2 text-right text-slate-300">{d.size_gb.toFixed(1)} GB</td>
                    <td className="px-4 py-2 text-right">
                      {d.temp != null ? (
                        <span className={d.temp >= 50 ? 'text-red-400' : d.temp >= 40 ? 'text-amber-400' : 'text-slate-300'}>
                          {d.temp}°C
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Alerts */}
      {alerts && alerts.length > 0 && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Alerts</h3>
          <div className="space-y-2">
            {alerts.map((a, i) => (
              <div key={i} className="flex items-start gap-3 text-xs">
                <Badge variant="severity" severity={a.level === 'CRITICAL' ? 'critical' : a.level === 'WARNING' ? 'warning' : 'info'}>
                  {a.level}
                </Badge>
                <div className="flex-1">
                  <p className="text-slate-300">{a.message}</p>
                  <p className="text-slate-500 mt-0.5">{a.date}</p>
                </div>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
