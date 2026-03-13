'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { formatUptime } from '@/lib/utils';
import { Battery, Zap, Thermometer } from 'lucide-react';

interface UpsData {
  status: string;
  status_label: string;
  on_battery: boolean;
  battery_pct: number;
  runtime_s: number;
  load_pct: number;
  input_voltage: number;
  output_voltage: number;
  battery_voltage: number;
  temp: number | null;
  power_w: number | null;
  manufacturer: string;
  model: string;
  serial: string;
  firmware: string;
}

function barColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 75) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function batteryColor(pct: number): string {
  if (pct <= 20) return 'bg-red-500';
  if (pct <= 50) return 'bg-amber-500';
  return 'bg-emerald-500';
}

function ProgressBar({ label, pct, color, detail }: { label: string; pct: number; color?: string; detail?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span>{detail ?? `${pct.toFixed(1)}%`}</span>
      </div>
      <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color ?? barColor(pct)}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
    </div>
  );
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

export function UpsDetail({ data }: { data: UpsData }) {
  return (
    <div className="space-y-6">
      {/* Status banner */}
      <GlassCard className={`p-4 ${data.on_battery ? 'border-amber-500/30 bg-amber-500/5' : ''}`}>
        <div className="flex items-center gap-3">
          <Zap className={`h-5 w-5 ${data.on_battery ? 'text-amber-400' : 'text-emerald-400'}`} />
          <div>
            <p className="text-sm font-medium text-slate-200">{data.status_label}</p>
            <p className="text-xs text-slate-500">Status: {data.status}</p>
          </div>
          <div className="ml-auto">
            <Badge variant="severity" severity={data.on_battery ? 'warning' : 'info'}>
              {data.on_battery ? 'On Battery' : 'Online'}
            </Badge>
          </div>
        </div>
      </GlassCard>

      {/* Key metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Battery"
          value={`${data.battery_pct?.toFixed(0) ?? '—'}%`}
          icon={<Battery className="h-5 w-5 text-emerald-400" />}
        />
        <StatCard label="Runtime" value={data.runtime_s ? formatUptime(data.runtime_s) : '—'} />
        <StatCard label="Load" value={`${data.load_pct?.toFixed(1) ?? '—'}%`} />
        <StatCard
          label="Temperature"
          value={data.temp != null ? `${data.temp}°C` : '—'}
          icon={data.temp != null ? <Thermometer className="h-5 w-5 text-slate-400" /> : undefined}
        />
      </div>

      {/* Bars */}
      <GlassCard className="p-4 space-y-4">
        <h3 className="text-sm font-medium text-slate-300">Metrics</h3>
        <ProgressBar label="Battery" pct={data.battery_pct ?? 0} color={batteryColor(data.battery_pct ?? 0)} />
        <ProgressBar label="Load" pct={data.load_pct ?? 0} />
      </GlassCard>

      {/* Voltage + device info */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Electrical</h3>
          <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-xs">
            <span className="text-slate-500">Input Voltage</span>
            <span className="text-slate-300">{data.input_voltage?.toFixed(1) ?? '—'} V</span>
            <span className="text-slate-500">Output Voltage</span>
            <span className="text-slate-300">{data.output_voltage?.toFixed(1) ?? '—'} V</span>
            <span className="text-slate-500">Battery Voltage</span>
            <span className="text-slate-300">{data.battery_voltage?.toFixed(1) ?? '—'} V</span>
            {data.power_w != null && (
              <>
                <span className="text-slate-500">Power</span>
                <span className="text-slate-300">{data.power_w.toFixed(0)} W</span>
              </>
            )}
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Device Info</h3>
          <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-xs">
            <span className="text-slate-500">Manufacturer</span>
            <span className="text-slate-300">{data.manufacturer}</span>
            <span className="text-slate-500">Model</span>
            <span className="text-slate-300">{data.model}</span>
            <span className="text-slate-500">Serial</span>
            <span className="text-slate-300 font-mono">{data.serial}</span>
            <span className="text-slate-500">Firmware</span>
            <span className="text-slate-300">{data.firmware}</span>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}
