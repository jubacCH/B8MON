'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import type { Incident, IncidentEvent } from '@/types';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs';
import { ArrowLeft, CheckCircle, Eye } from 'lucide-react';
import Link from 'next/link';

const SEVERITY_LABELS: Record<number, { label: string; color: string }> = {
  0: { label: 'EMERG', color: 'text-red-300 bg-red-500/20' },
  1: { label: 'ALERT', color: 'text-red-300 bg-red-500/20' },
  2: { label: 'CRIT', color: 'text-red-400 bg-red-500/15' },
  3: { label: 'ERROR', color: 'text-red-400 bg-red-500/10' },
  4: { label: 'WARN', color: 'text-amber-400 bg-amber-500/10' },
};

function SeverityBadge({ severity }: { severity: number }) {
  const info = SEVERITY_LABELS[severity] ?? { label: `SEV${severity}`, color: 'text-slate-400 bg-white/[0.05]' };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-medium ${info.color}`}>
      {info.label}
    </span>
  );
}

interface RelatedLog {
  timestamp: string;
  hostname: string;
  severity: number;
  app_name: string;
  message: string;
}

interface IncidentDetail extends Incident {
  events: IncidentEvent[];
  related_logs?: RelatedLog[];
}

export default function IncidentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const incidentId = Number(id);
  const toast = useToastStore((s) => s.show);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => get<IncidentDetail>(`/api/v1/incidents/${incidentId}`),
    enabled: incidentId > 0,
  });

  async function acknowledge() {
    try {
      await post(`/api/v1/incidents/${incidentId}/acknowledge`);
      refetch();
      toast('Incident acknowledged', 'success');
    } catch {
      toast('Failed to acknowledge', 'error');
    }
  }

  async function resolve() {
    try {
      await post(`/api/v1/incidents/${incidentId}/resolve`);
      refetch();
      toast('Incident resolved', 'success');
    } catch {
      toast('Failed to resolve', 'error');
    }
  }

  return (
    <div>
      <Breadcrumbs items={[{ label: 'Alerts', href: '/alerts' }, { label: data?.title ?? `Incident #${incidentId}` }]} />
      <PageHeader
        title={data?.title ?? `Incident #${incidentId}`}
        actions={
          <div className="flex items-center gap-2">
            {data?.status === 'open' && (
              <Button size="sm" variant="ghost" onClick={acknowledge}>
                <Eye size={16} /> Acknowledge
              </Button>
            )}
            {data?.status !== 'resolved' && (
              <Button size="sm" onClick={resolve}>
                <CheckCircle size={16} /> Resolve
              </Button>
            )}
            <Link href="/alerts?tab=incidents">
              <Button variant="ghost" size="sm"><ArrowLeft size={16} /> Back</Button>
            </Link>
          </div>
        }
      />

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : data ? (
        <>
          <GlassCard className="p-4 mb-6">
            <div className="flex items-center gap-3 flex-wrap">
              <Badge variant="severity" severity={data.severity}>{data.severity}</Badge>
              <Badge>{data.status}</Badge>
              <span className="text-xs text-slate-500 font-mono">{data.rule}</span>
              <span className="text-xs text-slate-500">
                Created: {new Date(data.created_at).toLocaleString()}
              </span>
              {data.resolved_at && (
                <span className="text-xs text-emerald-400">
                  Resolved: {new Date(data.resolved_at).toLocaleString()}
                </span>
              )}
            </div>
          </GlassCard>

          <GlassCard className="p-4">
            <h3 className="text-sm font-medium text-slate-300 mb-4">Event Timeline</h3>
            {data.events?.length ? (
              <div className="space-y-0">
                {data.events.map((evt, i) => (
                  <div key={evt.id} className="flex gap-3 pb-4 relative">
                    {i < data.events.length - 1 && (
                      <div className="absolute left-[7px] top-5 bottom-0 w-px bg-white/[0.06]" />
                    )}
                    <div className="w-4 h-4 rounded-full bg-white/[0.08] border-2 border-white/[0.15] mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge>{evt.event_type}</Badge>
                        <span className="text-[10px] text-slate-500">
                          {new Date(evt.timestamp).toLocaleString()}
                        </span>
                      </div>
                      <p className="text-sm text-slate-300 mt-1">{evt.summary}</p>
                      {evt.detail && (
                        <pre className="text-xs text-slate-500 mt-1 bg-white/[0.02] rounded p-2 overflow-x-auto">
                          {evt.detail}
                        </pre>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">No events</p>
            )}
          </GlassCard>

          {/* Related Syslog Messages */}
          {data.related_logs && data.related_logs.length > 0 && (
            <GlassCard className="p-4 mt-6">
              <h3 className="text-sm font-medium text-slate-300 mb-4">
                Related Syslog Messages
                <span className="text-xs text-slate-500 font-normal ml-2">
                  ({data.related_logs.length} entries)
                </span>
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-slate-500 border-b border-white/[0.06]">
                      <th className="pb-2 pr-3 font-medium">Time</th>
                      <th className="pb-2 pr-3 font-medium">Sev</th>
                      <th className="pb-2 pr-3 font-medium">Host</th>
                      <th className="pb-2 pr-3 font-medium">App</th>
                      <th className="pb-2 font-medium">Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.related_logs.map((log, i) => (
                      <tr key={i} className="border-b border-white/[0.03] hover:bg-white/[0.02]">
                        <td className="py-1.5 pr-3 text-slate-400 font-mono whitespace-nowrap">
                          {new Date(log.timestamp).toLocaleTimeString()}
                        </td>
                        <td className="py-1.5 pr-3">
                          <SeverityBadge severity={log.severity} />
                        </td>
                        <td className="py-1.5 pr-3 text-slate-300 font-mono whitespace-nowrap">
                          {log.hostname}
                        </td>
                        <td className="py-1.5 pr-3 text-slate-400 whitespace-nowrap">
                          {log.app_name || '—'}
                        </td>
                        <td className="py-1.5 text-slate-300 font-mono break-all">
                          {log.message}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </GlassCard>
          )}
        </>
      ) : (
        <GlassCard className="p-8 text-center">
          <p className="text-sm text-slate-500">Incident not found</p>
        </GlassCard>
      )}
    </div>
  );
}
