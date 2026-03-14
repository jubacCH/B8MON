'use client';

import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2, Pencil, Shield, ShieldCheck, Terminal, MonitorDot } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { get, post, api, del } from '@/lib/api';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';

/* ---------- Types ---------- */

type CredentialType = 'snmp_v2c' | 'snmp_v3' | 'winrm' | 'ssh';

interface Credential {
  id: number;
  name: string;
  type: CredentialType;
  created?: string;
}

interface CredentialForm {
  name: string;
  type: CredentialType;
  data: Record<string, string>;
}

/* ---------- Constants ---------- */

const TYPE_META: Record<CredentialType, { label: string; color: string; icon: typeof Shield }> = {
  snmp_v2c: { label: 'SNMPv2c', color: 'text-sky-400 bg-sky-500/15 border-sky-500/30', icon: Shield },
  snmp_v3:  { label: 'SNMPv3',  color: 'text-violet-400 bg-violet-500/15 border-violet-500/30', icon: ShieldCheck },
  winrm:    { label: 'WinRM',   color: 'text-amber-400 bg-amber-500/15 border-amber-500/30', icon: MonitorDot },
  ssh:      { label: 'SSH',     color: 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30', icon: Terminal },
};

const AUTH_PROTOCOLS = ['SHA', 'SHA256', 'MD5'] as const;
const PRIV_PROTOCOLS = ['AES', 'AES256', 'DES'] as const;
const TRANSPORTS = ['ntlm', 'kerberos', 'basic'] as const;

/* ---------- Field Definitions ---------- */

interface FieldDef {
  key: string;
  label: string;
  type: 'text' | 'password' | 'select' | 'textarea';
  options?: readonly string[];
  placeholder?: string;
}

const FIELDS: Record<CredentialType, FieldDef[]> = {
  snmp_v2c: [
    { key: 'community', label: 'Community String', type: 'password', placeholder: 'e.g. public' },
  ],
  snmp_v3: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'auth_protocol', label: 'Auth Protocol', type: 'select', options: AUTH_PROTOCOLS },
    { key: 'auth_password', label: 'Auth Password', type: 'password' },
    { key: 'priv_protocol', label: 'Privacy Protocol', type: 'select', options: PRIV_PROTOCOLS },
    { key: 'priv_password', label: 'Privacy Password', type: 'password' },
  ],
  winrm: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'transport', label: 'Transport', type: 'select', options: TRANSPORTS },
  ],
  ssh: [
    { key: 'username', label: 'Username', type: 'text' },
    { key: 'password', label: 'Password', type: 'password', placeholder: 'Leave blank if using key' },
    { key: 'private_key', label: 'Private Key', type: 'textarea', placeholder: '-----BEGIN OPENSSH PRIVATE KEY-----' },
  ],
};

/* ---------- Helpers ---------- */

function emptyData(type: CredentialType): Record<string, string> {
  const data: Record<string, string> = {};
  for (const f of FIELDS[type]) {
    data[f.key] = f.type === 'select' && f.options ? f.options[0] : '';
  }
  return data;
}

const inputCls =
  'w-full rounded-md border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/40 transition-colors';

const selectCls =
  'w-full rounded-md border border-white/[0.08] bg-[var(--ng-surface)] px-3 py-2 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/40 transition-colors [&>option]:text-slate-200';

/* ---------- Component ---------- */

export default function CredentialsPage() {
  useEffect(() => { document.title = 'Credentials | Nodeglow'; }, []);
  const toast = useToastStore();
  const qc = useQueryClient();
  const { confirm, ConfirmDialogElement } = useConfirm();

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const [form, setForm] = useState<CredentialForm>({ name: '', type: 'snmp_v2c', data: emptyData('snmp_v2c') });

  /* ----- Queries ----- */

  const { data: credentials, isLoading } = useQuery<Credential[]>({
    queryKey: ['credentials'],
    queryFn: () => get('/api/credentials/list'),
  });

  /* ----- Mutations ----- */

  const createMut = useMutation({
    mutationFn: (body: CredentialForm) => post('/api/credentials', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential created', 'success');
      closeModal();
    },
    onError: () => toast.show('Failed to create credential', 'error'),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: number; body: CredentialForm }) =>
      api(`/api/credentials/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential updated', 'success');
      closeModal();
    },
    onError: () => toast.show('Failed to update credential', 'error'),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => del(`/api/credentials/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['credentials'] });
      toast.show('Credential deleted', 'success');
    },
    onError: () => toast.show('Failed to delete credential', 'error'),
  });

  /* ----- Handlers ----- */

  function openCreate() {
    setEditing(null);
    setForm({ name: '', type: 'snmp_v2c', data: emptyData('snmp_v2c') });
    setModalOpen(true);
  }

  function openEdit(cred: Credential) {
    setEditing(cred);
    // Pre-fill with empty data — passwords are never returned by the API
    setForm({ name: cred.name, type: cred.type, data: emptyData(cred.type) });
    setModalOpen(true);
  }

  function closeModal() {
    setModalOpen(false);
    setEditing(null);
  }

  function handleTypeChange(type: CredentialType) {
    setForm((f) => ({ ...f, type, data: emptyData(type) }));
  }

  function handleDataChange(key: string, value: string) {
    setForm((f) => ({ ...f, data: { ...f.data, [key]: value } }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.show('Name is required', 'warning');
      return;
    }
    if (editing) {
      updateMut.mutate({ id: editing.id, body: form });
    } else {
      createMut.mutate(form);
    }
  }

  async function handleDelete(cred: Credential) {
    const ok = await confirm({ title: 'Delete credential', description: `Delete credential "${cred.name}"? This cannot be undone.`, confirmLabel: 'Delete', variant: 'danger' });
    if (!ok) return;
    deleteMut.mutate(cred.id);
  }

  const isSaving = createMut.isPending || updateMut.isPending;

  /* ----- Render ----- */

  return (
    <div>
      <PageHeader
        title="Credentials"
        description="Stored credentials for integrations"
        actions={
          <Button onClick={openCreate} size="sm">
            <Plus size={15} />
            Add Credential
          </Button>
        }
      />

      <GlassCard className="p-0 overflow-hidden">
        {isLoading ? (
          <div className="p-6 space-y-3">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !credentials?.length ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-16 text-slate-500">
            <KeyRound size={40} className="mb-3 text-slate-600" />
            <p className="text-sm font-medium">No credentials stored</p>
            <p className="text-xs mt-1">Add your first credential to get started.</p>
            <Button onClick={openCreate} size="sm" className="mt-4">
              <Plus size={14} />
              Add Credential
            </Button>
          </div>
        ) : (
          /* Table */
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-left text-xs text-slate-400 uppercase tracking-wider">
                <th className="px-6 py-3 font-medium">Name</th>
                <th className="px-6 py-3 font-medium">Type</th>
                <th className="px-6 py-3 font-medium hidden sm:table-cell">Created</th>
                <th className="px-6 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {credentials.map((cred) => {
                const meta = TYPE_META[cred.type];
                const Icon = meta.icon;
                return (
                  <tr key={cred.id} className="hover:bg-white/[0.06] transition-colors">
                    <td className="px-6 py-3 text-slate-200 font-medium">
                      <div className="flex items-center gap-2">
                        <KeyRound size={14} className="text-slate-500" />
                        {cred.name}
                      </div>
                    </td>
                    <td className="px-6 py-3">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border ${meta.color}`}>
                        <Icon size={12} />
                        {meta.label}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-slate-400 hidden sm:table-cell">
                      {cred.created
                        ? new Date(cred.created).toLocaleDateString('en-US', {
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric',
                          })
                        : '\u2014'}
                    </td>
                    <td className="px-6 py-3 text-right">
                      <div className="inline-flex items-center gap-1">
                        <button
                          onClick={() => openEdit(cred)}
                          className="p-1.5 rounded-md text-slate-400 hover:text-sky-400 hover:bg-sky-500/10 transition-colors"
                          title="Edit"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(cred)}
                          className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </GlassCard>

      {/* ----- Add / Edit Modal ----- */}
      <Modal open={modalOpen} onClose={closeModal} title={editing ? 'Edit Credential' : 'Add Credential'}>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Name</label>
            <input
              className={inputCls}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Core Switch SNMP"
              autoFocus
            />
          </div>

          {/* Type */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Type</label>
            <select
              className={selectCls}
              value={form.type}
              onChange={(e) => handleTypeChange(e.target.value as CredentialType)}
            >
              {(Object.keys(TYPE_META) as CredentialType[]).map((t) => (
                <option key={t} value={t}>
                  {TYPE_META[t].label}
                </option>
              ))}
            </select>
          </div>

          {/* Divider */}
          <div className="border-t border-white/[0.06]" />

          {/* Dynamic fields */}
          {FIELDS[form.type].map((field) => (
            <div key={field.key}>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">{field.label}</label>

              {field.type === 'select' && field.options ? (
                <select
                  className={selectCls}
                  value={form.data[field.key] ?? ''}
                  onChange={(e) => handleDataChange(field.key, e.target.value)}
                >
                  {field.options.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : field.type === 'textarea' ? (
                <textarea
                  className={`${inputCls} min-h-[100px] font-mono text-xs`}
                  value={form.data[field.key] ?? ''}
                  onChange={(e) => handleDataChange(field.key, e.target.value)}
                  placeholder={field.placeholder}
                  rows={4}
                />
              ) : (
                <input
                  className={inputCls}
                  type={field.type}
                  value={form.data[field.key] ?? ''}
                  onChange={(e) => handleDataChange(field.key, e.target.value)}
                  placeholder={editing && field.type === 'password' ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : field.placeholder}
                />
              )}
            </div>
          ))}

          {editing && (
            <p className="text-xs text-slate-500">
              Leave password fields blank to keep existing values.
            </p>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={closeModal}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={isSaving}>
              {isSaving ? 'Saving\u2026' : editing ? 'Update' : 'Create'}
            </Button>
          </div>
        </form>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
