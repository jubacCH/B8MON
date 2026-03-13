'use client';

import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';

interface HassAutomation {
  entity_id: string;
  name: string;
  state: string;
  last_triggered: string | null;
}

interface HassPerson {
  name: string;
  state: string;
}

interface HassData {
  version: string;
  location_name: string;
  timezone: string;
  components: number;
  entities: {
    total: number;
    by_domain: Record<string, number>;
  };
  automations: HassAutomation[];
  persons: HassPerson[];
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <GlassCard className="p-4 text-center">
      <p className="text-2xl font-semibold text-slate-100">{value}</p>
      <p className="text-xs text-slate-400 mt-1">{label}</p>
    </GlassCard>
  );
}

export function HassDetail({ data }: { data: HassData }) {
  const domains = Object.entries(data.entities?.by_domain ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Entities" value={data.entities?.total ?? 0} />
        <StatCard label="Components" value={data.components ?? 0} />
        <StatCard label="Automations" value={data.automations?.length ?? 0} />
        <StatCard label="Persons" value={data.persons?.length ?? 0} />
      </div>

      {/* System info */}
      <GlassCard className="p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-3">Instance</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
          <div><span className="text-slate-500">Location</span><p className="text-slate-200 mt-1">{data.location_name}</p></div>
          <div><span className="text-slate-500">Version</span><p className="text-slate-300 mt-1 font-mono">{data.version}</p></div>
          <div><span className="text-slate-500">Timezone</span><p className="text-slate-300 mt-1">{data.timezone}</p></div>
          <div><span className="text-slate-500">Components</span><p className="text-slate-300 mt-1">{data.components}</p></div>
        </div>
      </GlassCard>

      {/* Entity domains */}
      {domains.length > 0 && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Entities by Domain</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {domains.map(([domain, count]) => (
              <div key={domain} className="flex items-center justify-between bg-white/[0.03] rounded-lg px-3 py-2">
                <span className="text-xs text-slate-300">{domain}</span>
                <span className="text-xs text-slate-500 font-mono ml-2">{count}</span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}

      {/* Automations */}
      {data.automations && data.automations.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.06]">
            <h3 className="text-sm font-medium text-slate-300">Automations</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-white/[0.06]">
                  <th className="px-4 py-2 text-left">Name</th>
                  <th className="px-4 py-2 text-center">State</th>
                  <th className="px-4 py-2 text-right">Last Triggered</th>
                </tr>
              </thead>
              <tbody>
                {data.automations.map((a) => (
                  <tr key={a.entity_id} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-slate-300">{a.name}</td>
                    <td className="px-4 py-2 text-center">
                      <Badge variant="severity" severity={a.state === 'on' ? 'info' : 'warning'}>
                        {a.state}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-right text-slate-400 text-xs">
                      {a.last_triggered ? new Date(a.last_triggered).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}

      {/* Persons */}
      {data.persons && data.persons.length > 0 && (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Persons</h3>
          <div className="flex flex-wrap gap-3">
            {data.persons.map((p) => (
              <div key={p.name} className="flex items-center gap-2 bg-white/[0.03] rounded-lg px-3 py-2">
                <span className={`inline-block w-2 h-2 rounded-full ${p.state === 'home' ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                <span className="text-sm text-slate-300">{p.name}</span>
                <span className="text-xs text-slate-500">{p.state}</span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
    </div>
  );
}
