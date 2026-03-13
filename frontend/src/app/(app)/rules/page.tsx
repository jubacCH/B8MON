'use client';

import { useState } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import type { AlertRule } from '@/types';
import { Plus, Trash2, Power } from 'lucide-react';

export default function RulesPage() {
  const [showAdd, setShowAdd] = useState(false);
  const toast = useToastStore((s) => s.show);

  const { data: rules, isLoading, refetch } = useQuery({
    queryKey: ['rules'],
    queryFn: () => get<AlertRule[]>('/api/v1/rules'),
  });

  async function toggleRule(id: number) {
    try {
      await post(`/api/v1/rules/${id}/toggle`);
      refetch();
      toast('Rule toggled', 'success');
    } catch {
      toast('Failed to toggle rule', 'error');
    }
  }

  async function deleteRule(id: number) {
    if (!confirm('Delete this rule?')) return;
    try {
      await post(`/api/v1/rules/${id}/delete`);
      refetch();
      toast('Rule deleted', 'success');
    } catch {
      toast('Failed to delete rule', 'error');
    }
  }

  return (
    <div>
      <PageHeader
        title="Alert Rules"
        description="Configure alert and correlation rules"
        actions={
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus size={16} /> New Rule
          </Button>
        }
      />

      <GlassCard>
        {isLoading ? (
          <div className="p-4 space-y-3">
            {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        ) : !rules?.length ? (
          <div className="p-8 text-center">
            <p className="text-sm text-slate-500">No alert rules configured</p>
            <Button size="sm" className="mt-4" onClick={() => setShowAdd(true)}>
              <Plus size={16} /> Create First Rule
            </Button>
          </div>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {rules.map((rule) => (
              <div key={rule.id} className="px-4 py-3 flex items-center gap-4 group hover:bg-white/[0.02] transition-colors">
                <StatusDot status={rule.enabled ? 'online' : 'disabled'} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-200">{rule.name}</span>
                    <Badge variant="severity" severity={rule.severity}>{rule.severity}</Badge>
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5 font-mono truncate">
                    {rule.source_type} &rarr; {rule.field_path} {rule.operator} {rule.threshold}
                  </p>
                  {rule.last_triggered_at && (
                    <p className="text-[10px] text-slate-600 mt-0.5">
                      Last triggered: {new Date(rule.last_triggered_at).toLocaleString()}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => toggleRule(rule.id)}
                    className="p-1.5 rounded-md hover:bg-white/[0.06] text-slate-400 hover:text-slate-200"
                    title={rule.enabled ? 'Disable' : 'Enable'}
                  >
                    <Power size={14} />
                  </button>
                  <button
                    onClick={() => deleteRule(rule.id)}
                    className="p-1.5 rounded-md hover:bg-red-500/10 text-slate-400 hover:text-red-400"
                    title="Delete"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="New Alert Rule">
        <p className="text-sm text-slate-400">Rule editor coming soon.</p>
      </Modal>
    </div>
  );
}
