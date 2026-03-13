'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { EChart } from '@/components/charts/EChart';
import type { EChartsOption } from 'echarts';

interface AdguardEntry {
  domain: string;
  count: number;
}

interface AdguardData {
  status: string;
  version: string;
  queries_today: number;
  blocked_today: number;
  blocked_pct: number;
  avg_processing_time_ms: number;
  top_queries: AdguardEntry[];
  top_blocked: AdguardEntry[];
  clients_today: number;
  filtering_enabled: boolean;
  safebrowsing_enabled: boolean;
  parental_enabled: boolean;
  num_replaced_safebrowsing: number;
  num_replaced_parental: number;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function AdguardDetail({ data }: { data: AdguardData }) {
  const pieOption: EChartsOption = {
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: 'transparent', borderWidth: 2 },
        label: { show: false },
        data: [
          { value: data.blocked_today, name: 'Blocked', itemStyle: { color: '#ef4444' } },
          { value: data.queries_today - data.blocked_today, name: 'Allowed', itemStyle: { color: '#22c55e' } },
        ],
      },
    ],
  };

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Queries" value={formatNumber(data.queries_today)} />
        <StatCard label="Blocked" value={formatNumber(data.blocked_today)} />
        <StatCard label="Block Rate" value={`${data.blocked_pct.toFixed(1)}%`} />
        <StatCard label="Clients Today" value={data.clients_today} />
      </div>

      {/* Features + pie */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Blocked vs Allowed</h3>
          <EChart option={pieOption} height={220} />
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Blocked Domains</h3>
          <div className="space-y-2">
            {(data.top_blocked ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.domain ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.domain}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!data.top_blocked || data.top_blocked.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>

        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Top Queries</h3>
          <div className="space-y-2">
            {(data.top_queries ?? []).slice(0, 10).map((entry, i) => (
              <div key={entry.domain ?? i} className="flex items-center justify-between text-xs">
                <span className="text-slate-300 truncate mr-2 font-mono">{entry.domain}</span>
                <span className="text-slate-500 tabular-nums shrink-0">{formatNumber(entry.count)}</span>
              </div>
            ))}
            {(!data.top_queries || data.top_queries.length === 0) && (
              <p className="text-xs text-slate-500">No data</p>
            )}
          </div>
        </GlassCard>
      </div>

      {/* Info row */}
      <GlassCard className="p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Service Info</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
          <div>
            <span className="text-slate-500">Status</span>
            <div className="mt-1">
              <Badge variant="severity" severity={data.status === 'running' ? 'info' : 'warning'}>
                {data.status}
              </Badge>
            </div>
          </div>
          <div>
            <span className="text-slate-500">Version</span>
            <p className="text-slate-300 mt-1 font-mono">{data.version}</p>
          </div>
          <div>
            <span className="text-slate-500">Avg Processing</span>
            <p className="text-slate-300 mt-1">{data.avg_processing_time_ms?.toFixed(1)} ms</p>
          </div>
          <div>
            <span className="text-slate-500">Features</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {data.filtering_enabled && <Badge>Filtering</Badge>}
              {data.safebrowsing_enabled && <Badge>Safe Browsing</Badge>}
              {data.parental_enabled && <Badge>Parental</Badge>}
            </div>
          </div>
        </div>
      </GlassCard>
    </div>
  );
}
