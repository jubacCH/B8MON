'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAgents } from '@/hooks/queries/useAgents';
import Link from 'next/link';

function MetricBar({ label, value }: { label: string; value: number | null }) {
  const pct = value ?? 0;
  const color = pct >= 90 ? 'bg-red-400' : pct >= 75 ? 'bg-amber-400' : 'bg-emerald-400';
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-500">{label}</span>
        <span className="text-slate-300 font-mono">{value != null ? `${Math.round(value)}%` : '--'}</span>
      </div>
      <div className="h-1.5 bg-white/[0.06] rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const { data: agents, isLoading } = useAgents();

  return (
    <div>
      <PageHeader
        title="Agents"
        description="Deployed monitoring agents"
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading &&
          Array.from({ length: 6 }).map((_, i) => (
            <GlassCard key={i} className="p-4">
              <div className="flex items-center gap-3 mb-4">
                <Skeleton className="h-5 w-5 rounded-full" />
                <Skeleton className="h-5 w-32" />
              </div>
              <div className="space-y-3">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-full" />
              </div>
            </GlassCard>
          ))}
        {agents?.map((agent) => {
          const detailHref = agent.host_id ? `/hosts/${agent.host_id}` : `/agents/${agent.id}`;
          return (
            <Link key={agent.id} href={detailHref}>
              <GlassCard className="p-4 hover:bg-white/[0.06] transition-colors cursor-pointer">
                <div className="flex items-center gap-3 mb-4">
                  <StatusDot status={agent.online ? 'online' : 'offline'} pulse={!agent.online} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-200 truncate">{agent.name}</p>
                    <p className="text-xs text-slate-500 font-mono truncate">{agent.hostname ?? '--'}</p>
                  </div>
                  <Badge>{agent.platform ?? '?'}</Badge>
                </div>
                <div className="space-y-2">
                  <MetricBar label="CPU" value={agent.cpu_pct} />
                  <MetricBar label="Memory" value={agent.mem_pct} />
                  <MetricBar label="Disk" value={agent.disk_pct} />
                </div>
                {agent.last_seen && (
                  <p className="text-xs text-slate-500 mt-3">
                    Last seen: {new Date(agent.last_seen).toLocaleString()}
                  </p>
                )}
              </GlassCard>
            </Link>
          );
        })}
        {!isLoading && (!agents || agents.length === 0) && (
          <GlassCard className="p-8 col-span-full">
            <p className="text-center text-sm text-slate-500">No agents registered</p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}
