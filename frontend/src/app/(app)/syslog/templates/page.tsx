'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  FileText,
  Search,
  Hash,
  Clock,
  TrendingUp,
  Volume2,
  Calendar,
  ChevronDown,
  ChevronUp,
  Activity,
  Loader2,
} from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { get } from '@/lib/api';
import type { LogTemplate } from '@/types';

interface TemplatesResponse {
  templates: (LogTemplate & { avg_rate_per_hour?: number })[];
  total: number;
}

interface FollowUpPattern {
  template: string;
  count: number;
  avg_delay_sec: number;
}

interface RootCauseResponse {
  follow_up_patterns: FollowUpPattern[];
}

type SortMode = 'recent' | 'frequent' | 'noisy' | 'newest';

function noiseColor(score: number): string {
  if (score >= 70) return 'text-red-400';
  if (score >= 30) return 'text-amber-400';
  return 'text-emerald-400';
}

function noiseBg(score: number): string {
  if (score >= 70) return 'bg-red-500/20 text-red-400 border-red-500/30';
  if (score >= 30) return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
  return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function RootCausePanel({ templateHash }: { templateHash: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['root-cause', templateHash],
    queryFn: () => get<RootCauseResponse>(`/api/root-cause/${templateHash}`),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-3 text-xs text-slate-500">
        <Loader2 className="h-3 w-3 animate-spin" />
        Analyzing follow-up patterns...
      </div>
    );
  }

  if (error || !data?.follow_up_patterns?.length) {
    return (
      <p className="py-3 text-xs text-slate-500">
        No follow-up patterns detected for this template.
      </p>
    );
  }

  return (
    <div className="space-y-2 py-2">
      <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">
        Follow-up patterns
      </p>
      {data.follow_up_patterns.map((p, i) => (
        <div
          key={i}
          className="flex items-start gap-3 rounded-md bg-white/[0.03] border border-white/[0.04] px-3 py-2"
        >
          <div className="flex-1 min-w-0">
            <p className="font-mono text-xs text-slate-300 break-all">{p.template}</p>
          </div>
          <div className="flex items-center gap-3 shrink-0 text-xs text-slate-500">
            <span>{p.count}x</span>
            <span>~{p.avg_delay_sec < 60 ? `${Math.round(p.avg_delay_sec)}s` : `${Math.round(p.avg_delay_sec / 60)}m`} delay</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SyslogTemplatesPage() {
  const [search, setSearch] = useState('');
  const [sortMode, setSortMode] = useState<SortMode>('recent');
  const [expandedHash, setExpandedHash] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['syslog-templates'],
    queryFn: () => get<TemplatesResponse>('/syslog/api/templates'),
  });

  const templates = useMemo(() => data?.templates ?? [], [data?.templates]);

  const filtered = useMemo(() => {
    let result = templates;

    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (t) =>
          t.template.toLowerCase().includes(q) ||
          t.example?.toLowerCase().includes(q) ||
          t.tags?.toLowerCase().includes(q),
      );
    }

    const sorted = [...result];
    switch (sortMode) {
      case 'recent':
        sorted.sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime());
        break;
      case 'frequent':
        sorted.sort((a, b) => b.count - a.count);
        break;
      case 'noisy':
        sorted.sort((a, b) => b.noise_score - a.noise_score);
        break;
      case 'newest':
        sorted.sort((a, b) => new Date(b.first_seen).getTime() - new Date(a.first_seen).getTime());
        break;
    }

    return sorted;
  }, [templates, search, sortMode]);

  const avgNoise = templates.length
    ? Math.round(templates.reduce((s, t) => s + t.noise_score, 0) / templates.length)
    : 0;

  const sortButtons: { key: SortMode; label: string }[] = [
    { key: 'recent', label: 'Recent' },
    { key: 'frequent', label: 'Most Frequent' },
    { key: 'noisy', label: 'Noisiest' },
    { key: 'newest', label: 'Newest' },
  ];

  return (
    <div>
      {/* Header */}
      <PageHeader
        title="Syslog"
        description="Log Intelligence - extracted message patterns and statistics"
      />

      {/* Tab bar */}
      <div className="flex items-center gap-1 mb-4">
        <Link href="/syslog" className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-slate-200 hover:bg-white/[0.03] transition-colors">
          <FileText size={15} /> Messages
        </Link>
        <span className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-white/[0.06] text-slate-100">
          <TrendingUp size={15} /> Intelligence
        </span>
      </div>

      {/* Stats summary */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className="rounded-md bg-sky-500/20 p-2">
            <FileText className="h-4 w-4 text-sky-400" />
          </div>
          <div>
            <p className="text-xs text-slate-500">Total Templates</p>
            {isLoading ? (
              <Skeleton className="h-5 w-12 mt-0.5" />
            ) : (
              <p className="text-lg font-semibold text-slate-100">
                {data?.total ?? templates.length}
              </p>
            )}
          </div>
        </GlassCard>
        <GlassCard className="px-4 py-3 flex items-center gap-3">
          <div className={`rounded-md p-2 ${avgNoise >= 70 ? 'bg-red-500/20' : avgNoise >= 30 ? 'bg-amber-500/20' : 'bg-emerald-500/20'}`}>
            <Volume2 className={`h-4 w-4 ${noiseColor(avgNoise)}`} />
          </div>
          <div>
            <p className="text-xs text-slate-500">Avg Noise Score</p>
            {isLoading ? (
              <Skeleton className="h-5 w-12 mt-0.5" />
            ) : (
              <p className={`text-lg font-semibold ${noiseColor(avgNoise)}`}>{avgNoise}</p>
            )}
          </div>
        </GlassCard>
      </div>

      {/* Search bar */}
      <div className="relative mb-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search templates, examples, or tags..."
          className="w-full pl-10 pr-4 py-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/50"
        />
      </div>

      {/* Sort controls */}
      <div className="flex flex-wrap gap-2 mb-6">
        {sortButtons.map((s) => (
          <button
            key={s.key}
            onClick={() => setSortMode(s.key)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              sortMode === s.key
                ? 'bg-sky-500/30 text-sky-300 border border-sky-500/50'
                : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Loading skeleton */}
      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <GlassCard key={i} className="p-4 space-y-3">
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <div className="flex gap-2">
                <Skeleton className="h-5 w-16" />
                <Skeleton className="h-5 w-16" />
                <Skeleton className="h-5 w-20" />
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && filtered.length === 0 && (
        <GlassCard className="p-12">
          <div className="text-center">
            <FileText className="h-10 w-10 text-slate-600 mx-auto mb-3" />
            <p className="text-sm text-slate-400">
              {search ? 'No templates match your search' : 'No log templates extracted yet'}
            </p>
            <p className="text-xs text-slate-600 mt-1">
              {search
                ? 'Try a different search term'
                : 'Templates will appear as syslog messages are processed'}
            </p>
          </div>
        </GlassCard>
      )}

      {/* Template list */}
      {!isLoading && filtered.length > 0 && (
        <div className="space-y-3">
          {filtered.map((t) => {
            const isExpanded = expandedHash === t.template_hash;
            const tags = t.tags
              ? t.tags.split(',').map((s) => s.trim()).filter(Boolean)
              : [];

            return (
              <GlassCard key={t.template_hash} className="p-4">
                {/* Template pattern */}
                <div className="flex items-start gap-3">
                  <Hash className="h-4 w-4 text-slate-600 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="font-mono text-sm text-slate-300 break-all leading-relaxed">
                      {t.template}
                    </p>

                    {/* Example message */}
                    {t.example && (
                      <p className="mt-1.5 text-xs text-slate-500 break-all">
                        Example: {t.example}
                      </p>
                    )}

                    {/* Stats row */}
                    <div className="flex flex-wrap items-center gap-2 mt-3">
                      {/* Count */}
                      <Badge>
                        <TrendingUp className="h-3 w-3" />
                        {t.count.toLocaleString()} hits
                      </Badge>

                      {/* Noise score */}
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border ${noiseBg(t.noise_score)}`}
                      >
                        <Volume2 className="h-3 w-3" />
                        Noise: {t.noise_score}
                      </span>

                      {/* Avg rate */}
                      {t.avg_rate_per_hour != null && (
                        <Badge>
                          <Activity className="h-3 w-3" />
                          {t.avg_rate_per_hour.toFixed(1)}/hr
                        </Badge>
                      )}

                      {/* Tags */}
                      {tags.map((tag) => (
                        <Badge key={tag} className="bg-purple-500/15 text-purple-300 border-purple-500/25">
                          {tag}
                        </Badge>
                      ))}
                    </div>

                    {/* Dates */}
                    <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
                      <span className="flex items-center gap-1">
                        <Calendar className="h-3 w-3" />
                        First: {formatDate(t.first_seen)}
                      </span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        Last: {formatDate(t.last_seen)}
                      </span>
                    </div>

                    {/* Root Cause toggle */}
                    <button
                      onClick={() => setExpandedHash(isExpanded ? null : t.template_hash)}
                      className="mt-3 flex items-center gap-1 text-xs font-medium text-sky-400 hover:text-sky-300 transition-colors"
                    >
                      {isExpanded ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : (
                        <ChevronDown className="h-3 w-3" />
                      )}
                      Root Cause Analysis
                    </button>

                    {/* Expanded root cause panel */}
                    {isExpanded && (
                      <div className="mt-2 pl-1 border-l-2 border-sky-500/30 ml-1">
                        <RootCausePanel templateHash={t.template_hash} />
                      </div>
                    )}
                  </div>
                </div>
              </GlassCard>
            );
          })}
        </div>
      )}
    </div>
  );
}
