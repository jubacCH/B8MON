'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useIntegrations } from '@/hooks/queries/useIntegrations';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post, del } from '@/lib/api';
import { Plus, Trash2, X } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

interface ConfigField {
  key: string;
  label: string;
  field_type: string;
  placeholder: string;
  required: boolean;
  default: string | number | boolean;
  options: { value: string; label: string }[] | null;
}

interface FieldsResponse {
  type: string;
  display_name: string;
  description: string;
  fields: ConfigField[];
}

const selectClass = 'w-full px-3 py-2 text-sm bg-[#111621] border border-white/[0.08] rounded-lg text-slate-200 focus:outline-none focus:border-sky-500/50 [&>option]:bg-[#111621] [&>option]:text-slate-200';
const inputClass = 'w-full px-3 py-2 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50';

export default function IntegrationListPage() {
  const params = useParams();
  const type = params.type as string;
  const qc = useQueryClient();
  const { data: integrations, isLoading } = useIntegrations(type);
  const [showAdd, setShowAdd] = useState(false);
  const [formData, setFormData] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);

  const { data: fieldsData } = useQuery({
    queryKey: ['integration-fields', type],
    queryFn: () => get<FieldsResponse>(`/api/integration/${type}/fields`),
    enabled: showAdd,
  });

  function resetForm() {
    const defaults: Record<string, string | boolean> = {};
    if (fieldsData?.fields) {
      for (const f of fieldsData.fields) {
        defaults[f.key] = f.field_type === 'checkbox' ? !!f.default : String(f.default ?? '');
      }
    }
    setFormData(defaults);
  }

  async function handleAdd() {
    setSaving(true);
    try {
      await post(`/api/integration/${type}/create`, {
        name: formData.name || '',
        ...formData,
      });
      qc.invalidateQueries({ queryKey: ['integrations', type] });
      setShowAdd(false);
      setFormData({});
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm('Delete this integration instance?')) return;
    await del(`/api/integration/${type}/${id}`);
    qc.invalidateQueries({ queryKey: ['integrations', type] });
  }

  return (
    <div>
      <PageHeader
        title={fieldsData?.display_name ?? (type.charAt(0).toUpperCase() + type.slice(1))}
        description={fieldsData?.description ?? `Integration instances for ${type}`}
        actions={
          <Button size="sm" onClick={() => { setShowAdd(true); resetForm(); }}>
            <Plus size={16} />
            Add Instance
          </Button>
        }
      />

      {/* Add form modal */}
      {showAdd && (
        <GlassCard className="p-6 mb-6 border border-sky-500/20">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-slate-200">
              New {fieldsData?.display_name ?? type} Instance
            </h3>
            <button onClick={() => setShowAdd(false)} className="text-slate-400 hover:text-slate-200">
              <X size={16} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Name field */}
            <div>
              <label className="block text-xs text-slate-400 mb-1">Name</label>
              <input
                type="text"
                placeholder={`My ${fieldsData?.display_name ?? type}`}
                value={(formData.name as string) ?? ''}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className={inputClass}
              />
            </div>

            {/* Dynamic config fields */}
            {fieldsData?.fields.map((field) => (
              <div key={field.key}>
                <label className="block text-xs text-slate-400 mb-1">
                  {field.label}
                  {field.required && <span className="text-red-400 ml-0.5">*</span>}
                </label>
                {field.field_type === 'checkbox' ? (
                  <label className="flex items-center gap-2 mt-1">
                    <input
                      type="checkbox"
                      checked={!!formData[field.key]}
                      onChange={(e) => setFormData({ ...formData, [field.key]: e.target.checked })}
                      className="rounded border-white/20 bg-white/[0.06]"
                    />
                    <span className="text-sm text-slate-300">{field.label}</span>
                  </label>
                ) : field.field_type === 'select' && field.options ? (
                  <select
                    value={(formData[field.key] as string) ?? ''}
                    onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                    className={selectClass}
                  >
                    <option value="">Select...</option>
                    {field.options.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={field.field_type === 'password' ? 'password' : field.field_type === 'url' ? 'url' : 'text'}
                    placeholder={field.placeholder}
                    value={(formData[field.key] as string) ?? ''}
                    onChange={(e) => setFormData({ ...formData, [field.key]: e.target.value })}
                    className={inputClass}
                  />
                )}
              </div>
            ))}
          </div>

          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" size="sm" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button size="sm" onClick={handleAdd} disabled={saving}>
              {saving ? 'Saving...' : 'Create'}
            </Button>
          </div>
        </GlassCard>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading &&
          Array.from({ length: 3 }).map((_, i) => (
            <GlassCard key={i} className="p-4">
              <Skeleton className="h-5 w-40 mb-3" />
              <Skeleton className="h-4 w-24 mb-2" />
              <Skeleton className="h-4 w-32" />
            </GlassCard>
          ))}
        {integrations?.map((int) => (
          <Link key={int.id} href={`/integration/${type}/${int.id}`}>
            <GlassCard className="p-4 hover:bg-white/[0.06] transition-colors cursor-pointer">
              <div className="flex items-center gap-3 mb-3">
                <StatusDot status={int.enabled ? 'online' : 'disabled'} />
                <p className="text-sm font-medium text-slate-200 flex-1">{int.name}</p>
                <button
                  onClick={(e) => handleDelete(int.id, e)}
                  className="text-slate-500 hover:text-red-400 transition-colors"
                  title="Delete"
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="flex items-center gap-2">
                <Badge>{int.type}</Badge>
                {!int.enabled && <Badge variant="severity" severity="warning">Disabled</Badge>}
              </div>
              <p className="text-xs text-slate-500 mt-2">
                Created: {new Date(int.created_at).toLocaleDateString()}
              </p>
            </GlassCard>
          </Link>
        ))}
        {!isLoading && (!integrations || integrations.length === 0) && !showAdd && (
          <GlassCard className="p-8 col-span-full">
            <p className="text-center text-sm text-slate-500">No {type} integrations configured</p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}
