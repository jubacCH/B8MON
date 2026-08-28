'use client';

import { GlassCard } from '@/components/ui/GlassCard';

interface SpeedtestData {
  download_mbps: number;
  upload_mbps: number;
  ping_ms: number | null;
  server_name: string;
  server_location: string;
  isp: string;
  timestamp: string;
}

function SpeedCard({ label, value, unit, color }: { label: string; value: number | null; unit: string; color: string }) {
  return (
    <GlassCard className="p-6 text-center">
      <p className="text-xs text-slate-500 mb-2">{label}</p>
      {/* null means the tool did not produce a usable reading. Showing "—"
          is honest; showing a number that was never measured is not. */}
      <p className={`text-4xl font-bold ${value === null ? 'text-slate-500' : color}`}>
        {value === null ? '—' : value.toFixed(1)}
      </p>
      <p className="text-sm text-slate-400 mt-1">{unit}</p>
    </GlassCard>
  );
}

export function SpeedtestDetail({ data }: { data: SpeedtestData }) {
  return (
    <div className="space-y-6">
      {/* Speed cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <SpeedCard label="Download" value={data.download_mbps} unit="Mbps" color="text-emerald-400" />
        <SpeedCard label="Upload" value={data.upload_mbps} unit="Mbps" color="text-blue-400" />
        <SpeedCard label="Ping" value={data.ping_ms} unit="ms" color="text-amber-400" />
      </div>

      {/* Server info */}
      <GlassCard className="p-5">
        <h3 className="text-sm font-medium text-slate-300 mb-4">Connection Details</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-3">
          <div>
            <p className="text-xs text-slate-500">Server</p>
            <p className="text-sm text-slate-200">{data.server_name}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Location</p>
            <p className="text-sm text-slate-200">{data.server_location}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">ISP</p>
            <p className="text-sm text-slate-200">{data.isp}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Tested</p>
            <p className="text-sm text-slate-200">{new Date(data.timestamp).toLocaleString()}</p>
          </div>
        </div>
      </GlassCard>
    </div>
  );
}
