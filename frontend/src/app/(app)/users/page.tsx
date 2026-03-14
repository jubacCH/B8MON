'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post, patch, del } from '@/lib/api';
import { useIsAdmin } from '@/stores/auth';
import { Plus, Trash2, Key } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useToastStore } from '@/stores/toast';
import { useConfirm } from '@/hooks/useConfirm';

interface UserInfo {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

const ROLES = ['admin', 'editor', 'readonly'] as const;

export default function UsersPage() {
  useEffect(() => { document.title = 'Users | Nodeglow'; }, []);
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [resetPwUser, setResetPwUser] = useState<UserInfo | null>(null);
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'readonly' });
  const [newPw, setNewPw] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const toast = useToastStore((s) => s.show);
  const { confirm, ConfirmDialogElement } = useConfirm();

  const { data: users, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => get<UserInfo[]>('/api/users'),
    enabled: isAdmin,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] });

  async function handleCreate() {
    setError('');
    if (!newUser.username.trim() || !newUser.password) {
      setError('Username and password required');
      return;
    }
    setSaving(true);
    try {
      await post('/api/users', newUser);
      setShowAdd(false);
      setNewUser({ username: '', password: '', role: 'readonly' });
      refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create user');
    } finally {
      setSaving(false);
    }
  }

  async function handleRoleChange(userId: number, role: string) {
    try {
      await patch(`/api/users/${userId}`, { role });
      refresh();
      toast('Role updated', 'success');
    } catch {
      toast('Failed to update role', 'error');
    }
  }

  async function handleDelete(userId: number) {
    const ok = await confirm({ title: 'Delete user', description: 'Delete this user? This action cannot be undone.', confirmLabel: 'Delete', variant: 'danger' });
    if (!ok) return;
    try {
      await del(`/api/users/${userId}`);
      refresh();
      toast('User deleted', 'success');
    } catch {
      toast('Failed to delete user', 'error');
    }
  }

  async function handleResetPassword() {
    if (!resetPwUser || !newPw) return;
    setSaving(true);
    try {
      await patch(`/api/users/${resetPwUser.id}`, { password: newPw });
      setResetPwUser(null);
      setNewPw('');
      toast('Password reset', 'success');
    } catch {
      toast('Failed to reset password', 'error');
    } finally {
      setSaving(false);
    }
  }

  if (!isAdmin) {
    return (
      <div>
        <PageHeader title="Users" description="User management" />
        <GlassCard className="p-8 text-center">
          <p className="text-sm text-slate-500">Admin access required</p>
        </GlassCard>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Users"
        description="User management"
        actions={
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus size={16} /> Add User
          </Button>
        }
      />

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Username</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Role</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Created</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                  </tr>
                ))}
              {users?.map((u) => {
                return (
                  <tr key={u.id} className="border-b border-white/[0.06] hover:bg-white/[0.06]">
                    <td className="px-4 py-3 text-slate-200 font-medium">{u.username}</td>
                    <td className="px-4 py-3">
                      <select
                        value={u.role}
                        onChange={(e) => handleRoleChange(u.id, e.target.value)}
                        className="bg-[var(--ng-surface)] border border-white/[0.08] rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r} className="bg-[var(--ng-surface)] text-slate-200">{r}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => { setResetPwUser(u); setNewPw(''); }}
                          className="p-1.5 rounded-md text-slate-400 hover:text-sky-400 hover:bg-white/[0.06] transition-colors"
                          title="Reset password"
                        >
                          <Key size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(u.id)}
                          className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-white/[0.06] transition-colors"
                          title="Delete user"
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
        </div>
      </GlassCard>

      {/* Add User Modal */}
      <Modal open={showAdd} onClose={() => { setShowAdd(false); setError(''); }} title="Add User">
        <div className="space-y-4">
          {error && (
            <p className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-md px-3 py-2">{error}</p>
          )}
          <div>
            <label className="block text-xs text-slate-400 mb-1">Username</label>
            <input
              type="text"
              value={newUser.username}
              onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
              className="w-full px-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.08] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Password</label>
            <input
              type="password"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              className="w-full px-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.08] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Role</label>
            <select
              value={newUser.role}
              onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
              className="w-full px-3 py-2 rounded-md bg-[var(--ng-surface)] border border-white/[0.08] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
            >
              {ROLES.map((r) => (
                <option key={r} value={r} className="bg-[var(--ng-surface)] text-slate-200">{r}</option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => { setShowAdd(false); setError(''); }}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleCreate} disabled={saving}>
              {saving ? 'Creating...' : 'Create User'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Reset Password Modal */}
      <Modal open={!!resetPwUser} onClose={() => setResetPwUser(null)} title={`Reset Password — ${resetPwUser?.username}`}>
        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1">New Password</label>
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.08] text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-sky-500/50"
              autoFocus
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setResetPwUser(null)}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleResetPassword} disabled={saving || !newPw}>
              {saving ? 'Saving...' : 'Reset Password'}
            </Button>
          </div>
        </div>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}
