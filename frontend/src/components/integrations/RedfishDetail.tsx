'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Server, Cpu, Thermometer, Fan } from 'lucide-react';

interface RedfishTemp {
  name: string;
  reading_c: number;
  status: string;
  threshold_c: number | null;
}

interface RedfishFan {
  name: string;
  rpm: number | null;
  status: string;
}

interface RedfishData {
  hostname: string;
  manufacturer: string;
  model: string;
  serial: string;
  bios_version: string;
  status: string;
  healthy: boolean;
  power_state: string;
  cpu_count: number;
  memory_gb: number;
  temperatures: RedfishTemp[];
  fans: RedfishFan[];
  power_watts: number | null;
  health_summary: string;
}

function StatCard({ label, value, icon }: { label: string; value: string | number; icon?: React.ReactNode }) {
  return (
    <GlassCard className="p-4 text-center">
      {icon && <div className="flex justify-center mb-2">{icon}</div>}
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function tempColor(temp: number, threshold: number | null): string {
  if (threshold && temp >= threshold) return 'text-red-400';
  if (temp >= 80) return 'text-red-400';
  if (temp >= 60) return 'text-amber-400';
  return 'text-slate-300';
}

export function RedfishDetail({ data }: { data: RedfishData }) {
  return (
    <div className="space-y-6">
      {/* Health banner */}
      <GlassCard className={`p-4 ${!data.healthy ? 'border-red-500/30 bg-red-500/5' : ''}`}>
        <div className="flex items-center gap-3">
          <Server className={`h-5 w-5 ${data.healthy ? 'text-emerald-400' : 'text-red-400'}`} />
          <div>
            <p className="text-sm font-medium text-slate-200">{data.hostname}</p>
            <p className="text-xs text-slate-500">{data.manufacturer} {data.model}</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Badge variant="severity" severity={data.power_state === 'On' ? 'info' : 'warning'}>
              {data.power_state}
            </Badge>
            <Badge variant="severity" severity={data.healthy ? 'info' : 'critical'}>
              {data.health_summary}
            </Badge>
          </div>
        </div>
      </GlassCard>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="CPUs" value={data.cpu_count} icon={<Cpu className="h-5 w-5 text-sky-400" />} />
        <StatCard label="Memory" value={`${data.memory_gb} GB`} />
        <StatCard label="Power" value={data.power_watts != null ? `${data.power_watts} W` : '—'} />
        <StatCard label="Fans" value={data.fans?.length ?? 0} />
      </div>

      {/* Device info */}
      <GlassCard className="p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Hardware</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
          <div><span className="text-slate-500">Manufacturer</span><p className="text-slate-300 mt-1">{data.manufacturer}</p></div>
          <div><span className="text-slate-500">Model</span><p className="text-slate-300 mt-1">{data.model}</p></div>
          <div><span className="text-slate-500">Serial</span><p className="text-slate-300 mt-1 font-mono">{data.serial}</p></div>
          <div><span className="text-slate-500">BIOS</span><p className="text-slate-300 mt-1">{data.bios_version}</p></div>
        </div>
      </GlassCard>

      {/* Temperatures */}
      {data.temperatures && data.temperatures.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-2">
            <Thermometer className="h-4 w-4 text-slate-400" />
            <h3 className="text-sm font-medium text-slate-300">Temperatures</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Sensor</th>
                  <th className="px-4 py-2 text-right">Reading</th>
                  <th className="px-4 py-2 text-right">Threshold</th>
                  <th className="px-4 py-2 text-center">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.temperatures.map((t) => (
                  <tr key={t.name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300">{t.name}</td>
                    <td className={`px-4 py-2 text-right font-mono ${tempColor(t.reading_c, t.threshold_c)}`}>
                      {t.reading_c}°C
                    </td>
                    <td className="px-4 py-2 text-right text-slate-500 font-mono">
                      {t.threshold_c != null ? `${t.threshold_c}°C` : '—'}
                    </td>
                    <td className="px-4 py-2 text-center">
                      <Badge variant="severity" severity={t.status === 'OK' ? 'info' : 'critical'}>
                        {t.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Fans */}
      {data.fans && data.fans.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06] flex items-center gap-2">
            <Fan className="h-4 w-4 text-slate-400" />
            <h3 className="text-sm font-medium text-slate-300">Fans</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Fan</th>
                  <th className="px-4 py-2 text-right">RPM</th>
                  <th className="px-4 py-2 text-center">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.fans.map((f) => (
                  <tr key={f.name} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300">{f.name}</td>
                    <td className="px-4 py-2 text-right text-slate-400 font-mono">
                      {f.rpm != null ? `${f.rpm}` : '—'}
                    </td>
                    <td className="px-4 py-2 text-center">
                      <Badge variant="severity" severity={f.status === 'OK' ? 'info' : 'critical'}>
                        {f.status}
                      </Badge>
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
