'use client';

import { cn } from '@/lib/utils';
import type { HeatmapHost } from '@/hooks/queries/useDashboard';

interface HeatmapGridProps {
  data: HeatmapHost[];
  days: string[];
}

function cellColor(pct: number | null): string {
  if (pct === null) return 'bg-slate-500/20';
  if (pct >= 99.9) return 'bg-emerald-500/70';
  if (pct >= 95) return 'bg-amber-500/70';
  return 'bg-red-500/70';
}

export function HeatmapGrid({ data, days }: HeatmapGridProps) {
  if (!data.length) {
    return <p className="text-sm text-slate-500 text-center py-4">No heatmap data</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left px-2 py-1 text-slate-500 font-normal w-32">Host</th>
            {days.map((d, i) => (
              <th key={i} className="px-0 py-1" title={d}>
                <span className="sr-only">{d}</span>
              </th>
            ))}
            <th className="text-right px-2 py-1 text-slate-500 font-normal w-16">Avg</th>
          </tr>
        </thead>
        <tbody>
          {data.map((host) => {
            const validDays = host.days.filter((d): d is number => d !== null);
            const avg = validDays.length > 0
              ? (validDays.reduce((a, b) => a + b, 0) / validDays.length).toFixed(1)
              : '—';
            return (
              <tr key={host.host_id} className="group">
                <td className="px-2 py-1 text-slate-300 truncate max-w-[120px]" title={host.name}>
                  {host.name}
                </td>
                {host.days.map((pct, i) => (
                  <td key={i} className="px-[1px] py-1">
                    <div
                      className={cn(
                        'w-2 h-4 rounded-sm transition-transform hover:scale-150',
                        cellColor(pct),
                      )}
                      title={`${days[i]}: ${pct !== null ? pct + '%' : 'No data'}`}
                    />
                  </td>
                ))}
                <td className={cn('text-right px-2 py-1 font-mono', {
                  'text-emerald-400': Number(avg) >= 99.9,
                  'text-amber-400': Number(avg) >= 95 && Number(avg) < 99.9,
                  'text-red-400': Number(avg) < 95 && avg !== '—',
                  'text-slate-500': avg === '—',
                })}>
                  {avg}{avg !== '—' ? '%' : ''}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
