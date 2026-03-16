'use client';

import { useEffect, useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAgents } from '@/hooks/queries/useAgents';
import { get, del } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, X, Copy, Check, Terminal, Monitor, Trash2, Tag } from 'lucide-react';
import { timeAgo } from '@/lib/utils';
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

function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text: string) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        copyText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="p-1.5 rounded hover:bg-white/10 transition-colors text-slate-400 hover:text-slate-200"
      title="Copy"
    >
      {copied ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
    </button>
  );
}

interface EnrollmentInfo {
  enrollment_key: string;
  server_url: string;
  install_linux: string;
  install_windows: string;
}

function AddAgentDialog({ onClose }: { onClose: () => void }) {
  const [info, setInfo] = useState<EnrollmentInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'linux' | 'windows'>('linux');

  useEffect(() => {
    get<EnrollmentInfo>('/api/agent/enrollment-info')
      .then(setInfo)
      .finally(() => setLoading(false));
  }, []);

  return (
    <GlassCard className="p-6 mb-6 border border-sky-500/20">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-slate-200">Add New Agent</h3>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
          <X size={16} />
        </button>
      </div>

      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : info ? (
        <div className="space-y-4">
          <p className="text-sm text-slate-400">
            Run one of the following commands on the target machine to install and register the agent automatically.
          </p>

          <div className="flex gap-1 border-b border-white/[0.06]">
            <button
              onClick={() => setTab('linux')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === 'linux' ? 'accent-text border-b-2 border-current' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Terminal size={12} /> Linux
            </button>
            <button
              onClick={() => setTab('windows')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === 'windows' ? 'accent-text border-b-2 border-current' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Monitor size={12} /> Windows
            </button>
          </div>

          <div className="relative">
            <pre className="bg-black/40 border border-white/[0.06] rounded-lg p-4 pr-10 text-sm font-mono text-emerald-400 overflow-x-auto whitespace-pre-wrap break-all">
              {tab === 'linux' ? info.install_linux : info.install_windows}
            </pre>
            <div className="absolute top-2 right-2">
              <CopyButton text={tab === 'linux' ? info.install_linux : info.install_windows} />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Server URL</label>
              <code className="text-xs text-slate-300 font-mono bg-white/[0.04] px-2 py-1 rounded block truncate">
                {info.server_url}
              </code>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Enrollment Key</label>
              <div className="flex items-center gap-1">
                <code className="text-xs text-slate-300 font-mono bg-white/[0.04] px-2 py-1 rounded flex-1 truncate">
                  {info.enrollment_key}
                </code>
                <CopyButton text={info.enrollment_key} />
              </div>
            </div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-red-400">Failed to load enrollment info</p>
      )}
    </GlassCard>
  );
}

export default function AgentsPage() {
  useEffect(() => { document.title = 'Agents | Nodeglow'; }, []);
  const { data: agents, isLoading } = useAgents();
  const [showAdd, setShowAdd] = useState(false);
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();

  async function handleDelete(agentId: number, name: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const ok = await confirm({ title: 'Decommission agent', description: `Decommission agent "${name}"? This will also remove its associated host and all snapshots.`, confirmLabel: 'Decommission', variant: 'danger' });
    if (!ok) return;
    try {
      await del(`/api/v1/agents/${agentId}`);
      qc.invalidateQueries({ queryKey: ['agents'] });
      toast('Agent decommissioned', 'success');
    } catch {
      toast('Failed to delete agent', 'error');
    }
  }

  return (
    <div>
      <PageHeader
        title="Agents"
        description="Deployed monitoring agents"
        actions={
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus size={16} />
            Add Agent
          </Button>
        }
      />

      {showAdd && <AddAgentDialog onClose={() => setShowAdd(false)} />}

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
                  {agent.agent_version && (
                    <Badge className="bg-white/[0.04] text-slate-400 border-white/[0.06]">
                      <Tag size={10} /> v{agent.agent_version}
                    </Badge>
                  )}
                  <button
                    onClick={(e) => handleDelete(agent.id, agent.name, e)}
                    className="p-1 rounded text-slate-500 hover:text-red-400 hover:bg-white/[0.06] transition-colors"
                    title="Decommission"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="space-y-2">
                  <MetricBar label="CPU" value={agent.cpu_pct} />
                  <MetricBar label="Memory" value={agent.mem_pct} />
                  <MetricBar label="Disk" value={agent.disk_pct} />
                </div>
                {agent.last_seen && (
                  <p className="text-xs text-slate-500 mt-3" title={new Date(agent.last_seen).toLocaleString()}>
                    Last seen: {timeAgo(agent.last_seen)}
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
      {ConfirmDialogElement}
    </div>
  );
}
