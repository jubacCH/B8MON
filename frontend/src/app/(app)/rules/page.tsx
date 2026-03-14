'use client';

import { useState, useEffect, useCallback } from 'react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery } from '@tanstack/react-query';
import { api, get, post } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import type { AlertRule } from '@/types';
import { Plus, Trash2, Power, Pencil } from 'lucide-react';

interface SourceInstance {
  id: number;
  name: string;
}

interface SourceOption {
  type: string;
  label: string;
  instances: SourceInstance[];
}

interface FieldOption {
  path: string;
  value: unknown;
  type: string;
}

const OPERATORS = [
  { key: 'gt', label: 'Greater than' },
  { key: 'lt', label: 'Less than' },
  { key: 'gte', label: 'Greater or equal' },
  { key: 'lte', label: 'Less or equal' },
  { key: 'eq', label: 'Equals' },
  { key: 'ne', label: 'Not equals' },
  { key: 'contains', label: 'Contains' },
  { key: 'not_contains', label: 'Not contains' },
  { key: 'regex', label: 'Matches regex' },
  { key: 'is_true', label: 'Is true' },
  { key: 'is_false', label: 'Is false' },
];

const SEVERITIES = ['critical', 'warning', 'info'] as const;

const inputClass =
  'w-full rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-blue-500/50 focus:outline-none focus:ring-1 focus:ring-blue-500/30';
const labelClass = 'block text-xs font-medium text-slate-400 mb-1';
const selectClass =
  'w-full rounded-md border border-white/10 bg-[#111621] px-3 py-2 text-sm text-slate-200 focus:border-blue-500/50 focus:outline-none focus:ring-1 focus:ring-blue-500/30 [&>option]:bg-[#111621] [&>option]:text-slate-200';

interface RuleFormState {
  name: string;
  source_type: string;
  source_id: string;
  field_path: string;
  operator: string;
  threshold: string;
  severity: string;
  cooldown_minutes: string;
  notify_channels: string;
  message_template: string;
}

const DEFAULT_FORM: RuleFormState = {
  name: '',
  source_type: '',
  source_id: '',
  field_path: '',
  operator: 'gt',
  threshold: '',
  severity: 'warning',
  cooldown_minutes: '5',
  notify_channels: '',
  message_template: '',
};

function ruleToForm(rule: AlertRule): RuleFormState {
  return {
    name: rule.name,
    source_type: rule.source_type,
    source_id: rule.source_id != null ? String(rule.source_id) : '',
    field_path: rule.field_path,
    operator: rule.operator,
    threshold: rule.threshold ?? '',
    severity: rule.severity,
    cooldown_minutes: String(rule.cooldown_minutes),
    notify_channels: rule.notify_channels ?? '',
    message_template: rule.message_template ?? '',
  };
}

export default function RulesPage() {
  const [showModal, setShowModal] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [form, setForm] = useState<RuleFormState>(DEFAULT_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [sources, setSources] = useState<SourceOption[]>([]);
  const [fields, setFields] = useState<FieldOption[]>([]);
  const [loadingFields, setLoadingFields] = useState(false);
  const toast = useToastStore((s) => s.show);

  const { data: rules, isLoading, refetch } = useQuery({
    queryKey: ['rules'],
    queryFn: () => get<AlertRule[]>('/api/v1/rules'),
  });

  // Fetch sources when modal opens
  useEffect(() => {
    if (!showModal) return;
    get<SourceOption[]>('/api/rules/sources').then(setSources).catch(() => {});
  }, [showModal]);

  // Fetch fields when source changes
  const fetchFields = useCallback(async (sourceType: string, sourceId: string) => {
    if (!sourceType) {
      setFields([]);
      return;
    }
    setLoadingFields(true);
    try {
      const params = new URLSearchParams({ source_type: sourceType });
      if (sourceId) params.set('source_id', sourceId);
      const result = await get<FieldOption[]>(`/api/rules/fields?${params}`);
      setFields(result);
    } catch {
      setFields([]);
    } finally {
      setLoadingFields(false);
    }
  }, []);

  // Fetch fields when source_type or source_id changes
  useEffect(() => {
    if (!showModal) return;
    fetchFields(form.source_type, form.source_id);
  }, [showModal, form.source_type, form.source_id, fetchFields]);

  function openAdd() {
    setEditingRule(null);
    setForm(DEFAULT_FORM);
    setFields([]);
    setShowModal(true);
  }

  function openEdit(rule: AlertRule) {
    setEditingRule(rule);
    setForm(ruleToForm(rule));
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    setEditingRule(null);
  }

  function updateForm(key: keyof RuleFormState, value: string) {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      // Reset dependent fields when source_type changes
      if (key === 'source_type') {
        next.source_id = '';
        next.field_path = '';
      }
      if (key === 'source_id') {
        next.field_path = '';
      }
      return next;
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.source_type || !form.field_path) {
      toast('Please fill in name, source type, and field', 'error');
      return;
    }

    setSubmitting(true);
    try {
      const body = new FormData();
      body.set('name', form.name.trim());
      body.set('source_type', form.source_type);
      if (form.source_id) body.set('source_id', form.source_id);
      body.set('field_path', form.field_path);
      body.set('operator', form.operator);
      body.set('threshold', form.threshold);
      body.set('severity', form.severity);
      body.set('cooldown_minutes', form.cooldown_minutes || '5');
      if (form.notify_channels.trim()) body.set('notify_channels', form.notify_channels.trim());
      if (form.message_template.trim()) body.set('message_template', form.message_template.trim());

      const url = editingRule ? `/rules/${editingRule.id}/edit` : '/rules/add';
      await api(url, { method: 'POST', body });
      refetch();
      closeModal();
      toast(editingRule ? 'Rule updated' : 'Rule created', 'success');
    } catch {
      toast('Failed to save rule', 'error');
    } finally {
      setSubmitting(false);
    }
  }

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

  const selectedSource = sources.find((s) => s.type === form.source_type);
  const hideThreshold = form.operator === 'is_true' || form.operator === 'is_false';

  return (
    <div>
      <PageHeader
        title="Alert Rules"
        description="Configure alert and correlation rules"
        actions={
          <Button size="sm" onClick={openAdd}>
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
            <Button size="sm" className="mt-4" onClick={openAdd}>
              <Plus size={16} /> Create First Rule
            </Button>
          </div>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {rules.map((rule) => (
              <div key={rule.id} className="px-4 py-3 flex items-center gap-4 group hover:bg-white/[0.06] transition-colors">
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
                    onClick={() => openEdit(rule)}
                    className="p-1.5 rounded-md hover:bg-white/[0.06] text-slate-400 hover:text-slate-200"
                    title="Edit"
                  >
                    <Pencil size={14} />
                  </button>
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

      <Modal open={showModal} onClose={closeModal} title={editingRule ? 'Edit Alert Rule' : 'New Alert Rule'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className={labelClass}>Rule Name</label>
            <input
              type="text"
              className={inputClass}
              placeholder="e.g. High CPU on Proxmox"
              value={form.name}
              onChange={(e) => updateForm('name', e.target.value)}
              required
            />
          </div>

          {/* Source Type + Instance */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Source Type</label>
              <select
                className={selectClass}
                value={form.source_type}
                onChange={(e) => updateForm('source_type', e.target.value)}
                required
              >
                <option value="">Select source...</option>
                {sources.map((s) => (
                  <option key={s.type} value={s.type}>{s.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Instance</label>
              <select
                className={selectClass}
                value={form.source_id}
                onChange={(e) => updateForm('source_id', e.target.value)}
                disabled={!selectedSource?.instances.length}
              >
                <option value="">{selectedSource?.instances.length ? 'Any / All' : 'N/A'}</option>
                {selectedSource?.instances.map((inst) => (
                  <option key={inst.id} value={String(inst.id)}>{inst.name}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Field Path */}
          <div>
            <label className={labelClass}>Field</label>
            {loadingFields ? (
              <Skeleton className="h-9 w-full" />
            ) : fields.length > 0 ? (
              <>
                <select
                  className={selectClass}
                  value={form.field_path}
                  onChange={(e) => updateForm('field_path', e.target.value)}
                  required
                >
                  <option value="">Select field...</option>
                  {fields.map((f) => (
                    <option key={f.path} value={f.path}>
                      {f.path} ({f.type})
                    </option>
                  ))}
                </select>
                {form.field_path && (() => {
                  const selected = fields.find((f) => f.path === form.field_path);
                  if (!selected || selected.value == null) return null;
                  const displayVal = typeof selected.value === 'object'
                    ? JSON.stringify(selected.value)
                    : String(selected.value);
                  return (
                    <div className="mt-1.5 px-3 py-1.5 rounded-md bg-white/[0.04] border border-white/[0.06]">
                      <span className="text-[10px] text-slate-500 uppercase tracking-wider">Current value: </span>
                      <span className="text-xs font-mono text-slate-300">{displayVal}</span>
                    </div>
                  );
                })()}
              </>
            ) : (
              <input
                type="text"
                className={inputClass}
                placeholder="e.g. cpu_pct or data.temperature"
                value={form.field_path}
                onChange={(e) => updateForm('field_path', e.target.value)}
                required
              />
            )}
          </div>

          {/* Operator + Threshold */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Operator</label>
              <select
                className={selectClass}
                value={form.operator}
                onChange={(e) => updateForm('operator', e.target.value)}
              >
                {OPERATORS.map((op) => (
                  <option key={op.key} value={op.key}>{op.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Threshold</label>
              <input
                type="text"
                className={`${inputClass} ${hideThreshold ? 'opacity-30 pointer-events-none' : ''}`}
                placeholder={hideThreshold ? 'N/A' : 'e.g. 90'}
                value={hideThreshold ? '' : form.threshold}
                onChange={(e) => updateForm('threshold', e.target.value)}
                disabled={hideThreshold}
              />
            </div>
          </div>

          {/* Severity + Cooldown */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Severity</label>
              <select
                className={selectClass}
                value={form.severity}
                onChange={(e) => updateForm('severity', e.target.value)}
              >
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Cooldown (minutes)</label>
              <input
                type="number"
                className={inputClass}
                min={1}
                value={form.cooldown_minutes}
                onChange={(e) => updateForm('cooldown_minutes', e.target.value)}
              />
            </div>
          </div>

          {/* Notify Channels */}
          <div>
            <label className={labelClass}>Notify Channels <span className="text-slate-600">(optional, comma-separated)</span></label>
            <input
              type="text"
              className={inputClass}
              placeholder="e.g. email,slack"
              value={form.notify_channels}
              onChange={(e) => updateForm('notify_channels', e.target.value)}
            />
          </div>

          {/* Message Template */}
          <div>
            <label className={labelClass}>Message Template <span className="text-slate-600">(optional)</span></label>
            <textarea
              className={`${inputClass} resize-none`}
              rows={2}
              placeholder="e.g. {name}: {field} is {value} (threshold: {threshold})"
              value={form.message_template}
              onChange={(e) => updateForm('message_template', e.target.value)}
            />
          </div>

          {/* Submit */}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={closeModal}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting ? 'Saving...' : editingRule ? 'Update Rule' : 'Create Rule'}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
