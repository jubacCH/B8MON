'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { EChart } from '@/components/charts/EChart';
import { Download, Upload, Gauge } from 'lucide-react';
import Link from 'next/link';

interface UnifiPort {
  idx: number;
  name?: string;
  up: boolean;
  speed: number;
  speed_label?: string;
  is_uplink?: boolean;
  poe_enable?: boolean;
  poe_power?: number;
  rx_bytes_r?: number;
  tx_bytes_r?: number;
}

interface UnifiDevice {
  name: string;
  model: string;
  mac: string;
  ip: string;
  type_label: string;
  state: number;
  version: string;
  cpu_pct: number;
  mem_pct: number;
  clients_wifi: number;
  clients_wired: number;
  rx_bytes: number;
  tx_bytes: number;
  satisfaction: number;
  has_ports: boolean;
  port_table?: UnifiPort[];
}

interface SpeedtestResult {
  timestamp: string;
  download_mbps: number;
  upload_mbps: number;
  latency_ms: number;
}

interface UnifiData {
  devices: UnifiDevice[];
  speedtest?: SpeedtestResult[];
  speedtest_latest?: SpeedtestResult | null;
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

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function UnifiDetail({ data }: { data: UnifiData }) {
  const devices = data.devices ?? [];
  const totalWifi = devices.reduce((sum, d) => sum + (d.clients_wifi ?? 0), 0);
  const totalWired = devices.reduce((sum, d) => sum + (d.clients_wired ?? 0), 0);

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Devices" value={devices.length} />
        <StatCard label="WiFi Clients" value={totalWifi} />
        <StatCard label="Wired Clients" value={totalWired} />
        <StatCard label="Total Clients" value={totalWifi + totalWired} />
      </div>

      {/* Speedtest */}
      {data.speedtest_latest && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Gateway Speedtest</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <GlassCard className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <Download size={14} className="text-emerald-400" />
                <span className="text-xs text-slate-500 uppercase tracking-wider">Download</span>
              </div>
              <p className="text-2xl font-bold text-emerald-400">
                {data.speedtest_latest.download_mbps.toFixed(1)}
                <span className="text-sm font-normal text-slate-500 ml-1">Mbps</span>
              </p>
            </GlassCard>
            <GlassCard className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <Upload size={14} className="text-sky-400" />
                <span className="text-xs text-slate-500 uppercase tracking-wider">Upload</span>
              </div>
              <p className="text-2xl font-bold text-sky-400">
                {data.speedtest_latest.upload_mbps.toFixed(1)}
                <span className="text-sm font-normal text-slate-500 ml-1">Mbps</span>
              </p>
            </GlassCard>
            <GlassCard className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <Gauge size={14} className="text-amber-400" />
                <span className="text-xs text-slate-500 uppercase tracking-wider">Latency</span>
              </div>
              <p className="text-2xl font-bold text-amber-400">
                {data.speedtest_latest.latency_ms}
                <span className="text-sm font-normal text-slate-500 ml-1">ms</span>
              </p>
            </GlassCard>
          </div>
          {data.speedtest && data.speedtest.length > 1 && (
            <GlassCard className="p-4">
              <EChart
                height={220}
                option={{
                  tooltip: { trigger: 'axis' },
                  legend: { data: ['Download', 'Upload', 'Latency'] },
                  xAxis: {
                    type: 'category',
                    data: [...data.speedtest].reverse().map((s) => s.timestamp),
                  },
                  yAxis: [
                    { type: 'value', name: 'Mbps', axisLabel: { formatter: '{value}' } },
                    { type: 'value', name: 'ms', axisLabel: { formatter: '{value}' } },
                  ],
                  series: [
                    {
                      name: 'Download',
                      type: 'line',
                      data: [...data.speedtest].reverse().map((s) => s.download_mbps),
                      color: '#34D399',
                      smooth: true,
                      areaStyle: { opacity: 0.08 },
                    },
                    {
                      name: 'Upload',
                      type: 'line',
                      data: [...data.speedtest].reverse().map((s) => s.upload_mbps),
                      color: '#38BDF8',
                      smooth: true,
                      areaStyle: { opacity: 0.08 },
                    },
                    {
                      name: 'Latency',
                      type: 'line',
                      yAxisIndex: 1,
                      data: [...data.speedtest].reverse().map((s) => s.latency_ms),
                      color: '#FBBF24',
                      smooth: true,
                    },
                  ],
                }}
              />
              {data.speedtest_latest.timestamp && (
                <p className="text-[10px] text-slate-500 mt-2 text-right">
                  Last test: {data.speedtest_latest.timestamp}
                </p>
              )}
            </GlassCard>
          )}
        </div>
      )}

      {/* Device cards */}
      {devices.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-slate-300 mb-3">Devices</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {devices.map((d) => (
              <GlassCard key={d.mac} className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{d.name || d.mac}</span>
                  <Badge>{d.type_label}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-400">
                  <span>Model</span><span className="text-slate-300">{d.model}</span>
                  <span>IP</span><Link href={'/hosts?q=' + encodeURIComponent(d.ip)} className="text-sky-400 hover:underline font-mono">{d.ip}</Link>
                  <span>Version</span><span className="text-slate-300">{d.version}</span>
                  <span>WiFi</span><span className="text-slate-300">{d.clients_wifi}</span>
                  <span>Wired</span><span className="text-slate-300">{d.clients_wired}</span>
                  <span>Satisfaction</span>
                  <span className="text-slate-300">{d.satisfaction >= 0 ? `${d.satisfaction}%` : '—'}</span>
                  <span>RX</span><span className="text-slate-300">{formatBytes(d.rx_bytes)}</span>
                  <span>TX</span><span className="text-slate-300">{formatBytes(d.tx_bytes)}</span>
                </div>
                <div className="space-y-2">
                  <ProgressBar label="CPU" pct={d.cpu_pct} />
                  <ProgressBar label="Memory" pct={d.mem_pct} />
                </div>
              </GlassCard>
            ))}
          </div>
        </div>
      )}

      {/* Port tables for switches */}
      {devices.filter((d) => d.has_ports && d.port_table && d.port_table.length > 0).map((d) => (
        <GlassCard key={`ports-${d.mac}`} className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">
              Ports &mdash; {d.name || d.mac}
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">#</th>
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-center">Status</th>
                  <th className="px-4 py-2 text-left">Speed</th>
                  <th className="px-4 py-2 text-right">PoE</th>
                  <th className="px-4 py-2 text-right">RX</th>
                  <th className="px-4 py-2 text-right">TX</th>
                </tr>
              </thead>
              <tbody>
                {d.port_table!.map((p) => (
                  <tr key={p.idx} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300 font-mono">{p.idx}</td>
                    <td className="px-4 py-2 text-slate-400">
                      {p.name || '—'}
                      {p.is_uplink && <Badge className="ml-2">Uplink</Badge>}
                    </td>
                    <td className="px-4 py-2 text-center">
                      <span className={`inline-block w-2 h-2 rounded-full ${p.up ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                    </td>
                    <td className="px-4 py-2 text-slate-400">
                      {p.up ? (p.speed_label || (p.speed ? `${p.speed}M` : '—')) : '—'}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400">
                      {p.poe_enable ? (p.poe_power ? `${p.poe_power.toFixed(1)}W` : 'On') : '—'}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400 font-mono text-xs">
                      {p.up && p.rx_bytes_r ? formatBytes(p.rx_bytes_r) + '/s' : '—'}
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400 font-mono text-xs">
                      {p.up && p.tx_bytes_r ? formatBytes(p.tx_bytes_r) + '/s' : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      ))}
    </div>
  );
}
