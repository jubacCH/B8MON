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
import { ArrowLeft, CheckCircle, Eye, Zap, Search } from 'lucide-react';
import { useState } from 'react';
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
  template_hash?: string;
}

interface PatternInfo {
  count: number;
  template: string;
  example: string;
  hosts: { name: string; count: number }[];
  apps: { name: string; count: number }[];
  severity_breakdown: Record<string, number>;
  first_seen: string;
  last_seen: string;
  is_known: boolean;
  noise_score: number | null;
  tags: string[];
  avg_rate_per_hour: number | null;
}

interface LogAnalysis {
  summary: string;
  total_messages: number;
  unique_patterns: number;
  affected_hosts: number;
  single_source: boolean;
  worst_severity: string;
  patterns: PatternInfo[];
  top_hosts: { name: string; count: number }[];
  precursor_hints: {
    template: string;
    precedes: string;
    confidence: number;
    lead_time_min: number | null;
  }[];
}

interface IncidentDetail extends Incident {
  events: IncidentEvent[];
  related_logs?: RelatedLog[];
  log_analysis?: LogAnalysis | null;
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

          {/* Log Analysis */}
          {data.log_analysis && (
            <LogAnalysisSection analysis={data.log_analysis} />
          )}

          {/* Related Syslog Messages (collapsible) */}
          {data.related_logs && data.related_logs.length > 0 && (
            <RawLogsSection logs={data.related_logs} />
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

/* ---------- Log Analysis Section ---------- */

function LogAnalysisSection({ analysis }: { analysis: LogAnalysis }) {
  return (
    <GlassCard className="p-4 mt-6">
      <div className="flex items-center gap-2 mb-4">
        <Zap size={16} className="text-amber-400" />
        <h3 className="text-sm font-medium text-slate-300">Error Analysis</h3>
        <div className="flex gap-2 ml-auto">
          <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.05] text-slate-400">
            {analysis.total_messages} messages
          </span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.05] text-slate-400">
            {analysis.unique_patterns} pattern{analysis.unique_patterns !== 1 ? 's' : ''}
          </span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.05] text-slate-400">
            {analysis.affected_hosts} host{analysis.affected_hosts !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* Summary */}
      <div className="bg-white/[0.03] rounded-lg p-3 mb-4 border border-white/[0.06]">
        <p className="text-sm text-slate-200 leading-relaxed">{analysis.summary}</p>
      </div>

      {/* Precursor warnings */}
      {analysis.precursor_hints.length > 0 && (
        <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 mb-4">
          <p className="text-xs font-medium text-amber-400 mb-2">Precursor Pattern Detected</p>
          {analysis.precursor_hints.map((hint, i) => (
            <div key={i} className="flex items-start gap-2 text-xs mb-1">
              <span className="text-amber-400 shrink-0">{hint.confidence}%</span>
              <span className="text-slate-300">
                This pattern has preceded <span className="text-amber-300 font-medium">{hint.precedes}</span> events
                {hint.lead_time_min != null && (
                  <span className="text-slate-500"> (~{hint.lead_time_min}min lead time)</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Pattern groups */}
      <div className="space-y-3">
        {analysis.patterns.map((pattern, i) => (
          <div key={i} className="border border-white/[0.06] rounded-lg p-3">
            <div className="flex items-start gap-3 mb-2">
              <span className="text-lg font-bold text-sky-400 shrink-0 leading-none mt-0.5">
                {pattern.count}x
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-300 font-mono break-all leading-relaxed">
                  {pattern.template}
                </p>
                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                  {!pattern.is_known && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium">
                      NEW PATTERN
                    </span>
                  )}
                  {pattern.noise_score != null && pattern.noise_score >= 70 && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.05] text-slate-500">
                      noise: {pattern.noise_score}%
                    </span>
                  )}
                  {pattern.tags.filter(Boolean).map((tag) => (
                    <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400">
                      {tag}
                    </span>
                  ))}
                  {Object.entries(pattern.severity_breakdown).map(([sev, count]) => (
                    <span key={sev} className="text-[10px] text-slate-500">
                      {sev}: {count}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            {/* Affected hosts */}
            {pattern.hosts.length > 0 && (
              <div className="flex items-center gap-2 ml-9 flex-wrap">
                <span className="text-[10px] text-slate-500">Hosts:</span>
                {pattern.hosts.map((h) => (
                  <span key={h.name} className="text-[10px] font-mono text-slate-400">
                    {h.name}{h.count > 1 ? ` (${h.count})` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </GlassCard>
  );
}

/* ---------- Raw Logs Section (collapsible) ---------- */

function RawLogsSection({ logs }: { logs: RelatedLog[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <GlassCard className="p-4 mt-6">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        <Search size={14} className="text-slate-400" />
        <h3 className="text-sm font-medium text-slate-300">
          Raw Syslog Messages
        </h3>
        <span className="text-xs text-slate-500 font-normal">
          ({logs.length} entries)
        </span>
        <span className="text-xs text-slate-500 ml-auto">
          {expanded ? '▾ collapse' : '▸ expand'}
        </span>
      </button>

      {expanded && (
        <div className="overflow-x-auto mt-4">
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
              {logs.map((log, i) => (
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
      )}
    </GlassCard>
  );
}
