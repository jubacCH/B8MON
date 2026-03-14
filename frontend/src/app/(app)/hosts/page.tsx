'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { CopyButton } from '@/components/ui/CopyButton';
import { Pagination } from '@/components/ui/Pagination';
import { useHosts } from '@/hooks/queries/useHosts';
import { useConfirm } from '@/hooks/useConfirm';
import { formatLatency, uptimeColor } from '@/lib/utils';
import { post, patch } from '@/lib/api';
import { ExportButton } from '@/components/ui/ExportButton';
import { Plus, Search, X, ArrowUpDown, ArrowUp, ArrowDown, Wrench, Trash2, CheckSquare, Square, Server, Pencil } from 'lucide-react';
import { Modal } from '@/components/ui/Modal';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { HostStatus } from '@/types';

const inputClass = 'ng-input';
const selectClass = 'ng-input [&>option]:text-slate-200';

function UptimeBar({ h24, d7, d30 }: { h24: number | null; d7: number | null; d30: number | null }) {
  const bars = [
    { label: '30d', value: d30 },
    { label: '7d', value: d7 },
    { label: '24h', value: h24 },
  ];
  const allNull = bars.every((b) => b.value === null);
  if (allNull) return <span className="text-xs text-slate-600">—</span>;

  function barColor(v: number | null): string {
    if (v === null) return 'bg-slate-700';
    if (v >= 99.9) return 'bg-emerald-500';
    if (v >= 95) return 'bg-amber-500';
    return 'bg-red-500';
  }

  const worst = bars.reduce((min, b) => (b.value !== null && (min === null || b.value < min)) ? b.value : min, null as number | null);

  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5" title={bars.map((b) => `${b.label}: ${b.value != null ? b.value.toFixed(1) + '%' : '—'}`).join(' | ')}>
        {bars.map((b) => (
          <div key={b.label} className={`w-3 h-3 rounded-sm ${barColor(b.value)}`} />
        ))}
      </div>
      <span className={`text-xs font-mono ${uptimeColor(worst)}`}>
        {worst != null ? `${worst.toFixed(1)}%` : ''}
      </span>
    </div>
  );
}

type SortKey = 'name' | 'status' | 'type' | 'source' | 'latency' | 'uptime';
type SortDir = 'asc' | 'desc';

function SortHeader({ label, sortKey, currentKey, dir, onSort }: {
  label: string; sortKey: SortKey; currentKey: SortKey | null; dir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const active = currentKey === sortKey;
  return (
    <th
      className={`text-left px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300 transition-colors select-none ${active ? 'accent-text' : 'text-slate-400'}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          dir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />
        ) : (
          <ArrowUpDown size={12} className="opacity-30" />
        )}
      </span>
    </th>
  );
}

function statusOrder(h: HostStatus): number {
  if (!h.enabled) return 5;
  if (h.maintenance) return 4;
  if (h.online === false) return 0;
  if (h.online === null) return 3;
  if (h.port_error) return 2;
  return 1;
}

function hostStatusKey(h: HostStatus): 'disabled' | 'maintenance' | 'unknown' | 'offline' | 'error' | 'online' {
  if (!h.enabled) return 'disabled';
  if (h.maintenance) return 'maintenance';
  if (h.online === null) return 'unknown';
  if (h.online === false) return 'offline';
  if (h.port_error) return 'error';
  return 'online';
}

function hostStatusLabel(h: HostStatus): string {
  const key = hostStatusKey(h);
  const labels: Record<string, string> = {
    disabled: 'Disabled', maintenance: 'Maint.', unknown: '—',
    offline: 'Offline', error: 'Port Error', online: 'Online',
  };
  return labels[key];
}

function HostsPageInner() {
  useEffect(() => { document.title = 'Hosts | Nodeglow'; }, []);
  const searchParams = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const qParam = searchParams.get('q') ?? '';
  const [search, setSearch] = useState(qParam);
  const { data: hosts, isLoading } = useHosts();
  const redirected = useRef(false);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: '', hostname: '', check_type: 'icmp', port: '' });
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkLoading, setBulkLoading] = useState(false);
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkForm, setBulkForm] = useState({ check_type: '', enabled: '', latency_threshold_ms: '' });
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 50;
  const { confirm, ConfirmDialogElement } = useConfirm();

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  }, [sortKey]);

  const filteredHosts = useMemo(() => {
    let result = hosts ?? [];

    if (statusFilter !== 'all') {
      result = result.filter((h) => {
        if (statusFilter === 'online') return h.online === true && !h.maintenance && !h.port_error;
        if (statusFilter === 'offline') return h.online === false && !h.maintenance;
        if (statusFilter === 'error') return h.online === true && h.port_error && !h.maintenance;
        if (statusFilter === 'maintenance') return h.maintenance;
        return true;
      });
    }

    if (search) {
      const q = search.toLowerCase();
      result = result.filter((h) =>
        h.name.toLowerCase().includes(q) ||
        h.hostname.toLowerCase().includes(q) ||
        (h.check_type && h.check_type.toLowerCase().includes(q)) ||
        (h.source && h.source.toLowerCase().includes(q))
      );
    }

    if (sortKey) {
      const mult = sortDir === 'asc' ? 1 : -1;
      result = [...result].sort((a, b) => {
        switch (sortKey) {
          case 'name': return mult * a.name.localeCompare(b.name);
          case 'status': return mult * (statusOrder(a) - statusOrder(b));
          case 'type': return mult * (a.check_type ?? '').localeCompare(b.check_type ?? '');
          case 'source': return mult * (a.source ?? '').localeCompare(b.source ?? '');
          case 'latency': return mult * ((a.latency_ms ?? 9999) - (b.latency_ms ?? 9999));
          case 'uptime': return mult * ((a.uptime_h24 ?? 100) - (b.uptime_h24 ?? 100));
          default: return 0;
        }
      });
    }

    return result;
  }, [hosts, search, sortKey, sortDir, statusFilter]);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [search, statusFilter, sortKey, sortDir]);

  const pagedHosts = useMemo(
    () => filteredHosts.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [filteredHosts, page],
  );

  useEffect(() => {
    if (qParam && !isLoading && filteredHosts && filteredHosts.length === 1 && !redirected.current) {
      redirected.current = true;
      router.replace(`/hosts/${filteredHosts[0].id}`);
    }
  }, [qParam, isLoading, filteredHosts, router]);

  async function handleAdd() {
    setSaving(true);
    try {
      await post('/hosts/api/create', {
        name: form.name,
        hostname: form.hostname,
        check_type: form.check_type,
        port: form.port || undefined,
      });
      qc.invalidateQueries({ queryKey: ['hosts'] });
      setShowAdd(false);
      setForm({ name: '', hostname: '', check_type: 'icmp', port: '' });
    } finally {
      setSaving(false);
    }
  }

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === filteredHosts.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredHosts.map((h) => h.id)));
    }
  };

  async function bulkAction(action: 'maintenance' | 'delete') {
    if (selected.size === 0) return;
    const label = action === 'maintenance' ? 'toggle maintenance' : 'delete';
    const ok = await confirm({
      title: action === 'delete' ? 'Delete hosts' : 'Toggle maintenance',
      description: `${label} for ${selected.size} host(s)?`,
      confirmLabel: action === 'delete' ? 'Delete' : 'Confirm',
      variant: action === 'delete' ? 'danger' : 'default',
    });
    if (!ok) return;
    setBulkLoading(true);
    try {
      for (const id of Array.from(selected)) {
        if (action === 'maintenance') {
          await post(`/hosts/api/${id}/maintenance`);
        } else {
          await post(`/hosts/api/${id}/delete`);
        }
      }
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ['hosts'] });
    } finally {
      setBulkLoading(false);
    }
  }

  async function bulkEdit() {
    const updates: Record<string, unknown> = {};
    if (bulkForm.check_type) updates.check_type = bulkForm.check_type;
    if (bulkForm.enabled !== '') updates.enabled = bulkForm.enabled === 'true';
    if (bulkForm.latency_threshold_ms) updates.latency_threshold_ms = Number(bulkForm.latency_threshold_ms);
    if (Object.keys(updates).length === 0) return;
    setBulkLoading(true);
    try {
      await patch('/api/v1/hosts/bulk', { ids: Array.from(selected), updates });
      setSelected(new Set());
      setShowBulkEdit(false);
      setBulkForm({ check_type: '', enabled: '', latency_threshold_ms: '' });
      qc.invalidateQueries({ queryKey: ['hosts'] });
    } finally {
      setBulkLoading(false);
    }
  }

  const onlineCount = hosts?.filter((h) => h.online === true && !h.maintenance && !h.port_error).length ?? 0;
  const offlineCount = hosts?.filter((h) => h.online === false && !h.maintenance).length ?? 0;
  const errorCount = hosts?.filter((h) => h.online === true && h.port_error && !h.maintenance).length ?? 0;
  const maintCount = hosts?.filter((h) => h.maintenance).length ?? 0;

  return (
    <div>
      <PageHeader
        title="Hosts"
        description="Monitor network hosts and services"
        actions={
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                placeholder="Search hosts..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-8 pr-3 py-1.5 text-sm bg-white/[0.06] border border-white/[0.08] rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500/50"
              />
            </div>
            <Button size="sm" variant={selectMode ? 'secondary' : 'ghost'} onClick={() => {
              setSelectMode(!selectMode);
              if (selectMode) setSelected(new Set());
            }}>
              <CheckSquare size={16} />
              {selectMode ? 'Cancel' : 'Select'}
            </Button>
            <ExportButton
              data={(filteredHosts ?? []).map((h) => ({
                name: h.name, hostname: h.hostname, status: hostStatusKey(h),
                check_type: h.check_type, source: h.source, latency_ms: h.latency_ms,
                uptime_24h: h.uptime_h24, uptime_7d: h.uptime_d7, uptime_30d: h.uptime_d30,
              }))}
              filename="hosts"
              columns={[
                { key: 'name', label: 'Name' }, { key: 'hostname', label: 'Hostname' },
                { key: 'status', label: 'Status' }, { key: 'check_type', label: 'Type' },
                { key: 'source', label: 'Source' }, { key: 'latency_ms', label: 'Latency (ms)' },
                { key: 'uptime_24h', label: 'Uptime 24h' }, { key: 'uptime_7d', label: 'Uptime 7d' },
                { key: 'uptime_30d', label: 'Uptime 30d' },
              ]}
            />
            <Button size="sm" onClick={() => setShowAdd(true)}>
              <Plus size={16} />
              Add Host
            </Button>
          </div>
        }
      />

      {/* Status filter pills */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {[
          { key: 'all', label: `All (${hosts?.length ?? 0})` },
          { key: 'online', label: `Online (${onlineCount})`, color: 'text-emerald-400' },
          { key: 'offline', label: `Offline (${offlineCount})`, color: 'text-red-400' },
          ...(errorCount > 0 ? [{ key: 'error', label: `Port Error (${errorCount})`, color: 'text-orange-400' }] : []),
          { key: 'maintenance', label: `Maintenance (${maintCount})`, color: 'text-amber-400' },
        ].map((f) => (
          <button
            key={f.key}
            onClick={() => setStatusFilter(f.key)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              statusFilter === f.key
                ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                : 'bg-white/[0.04] text-slate-400 border border-white/[0.06] hover:bg-white/[0.08]'
            }`}
          >
            {f.label}
          </button>
        ))}

        {selectMode && selected.size > 0 && (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-slate-400">{selected.size} selected</span>
            <Button size="sm" variant="ghost" onClick={() => setShowBulkEdit(true)} disabled={bulkLoading}>
              <Pencil size={14} /> Edit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => bulkAction('maintenance')} disabled={bulkLoading}>
              <Wrench size={14} /> Maintenance
            </Button>
            <Button size="sm" variant="danger" onClick={() => bulkAction('delete')} disabled={bulkLoading}>
              <Trash2 size={14} /> Delete
            </Button>
          </div>
        )}
      </div>

      {showAdd && (
        <GlassCard className="p-6 mb-6 border border-sky-500/20">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-slate-200">Add New Host</h3>
            <button onClick={() => setShowAdd(false)} className="text-slate-400 hover:text-slate-200">
              <X size={16} />
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <label className="ng-label">Name <span className="text-red-400">*</span></label>
              <input type="text" placeholder="My Server" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="ng-label">Hostname / IP <span className="text-red-400">*</span></label>
              <input type="text" placeholder="192.168.1.1 or example.com" value={form.hostname} onChange={(e) => setForm({ ...form, hostname: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="ng-label">Check Type</label>
              <select value={form.check_type} onChange={(e) => setForm({ ...form, check_type: e.target.value })} className={selectClass}>
                <option value="icmp">ICMP (Ping)</option>
                <option value="http">HTTP</option>
                <option value="https">HTTPS</option>
                <option value="tcp">TCP</option>
                <option value="dns">DNS</option>
              </select>
            </div>
            <div>
              <label className="ng-label">Port (optional)</label>
              <input type="text" placeholder="443" value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} className={inputClass} />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="ghost" size="sm" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button size="sm" onClick={handleAdd} disabled={saving || !form.name || !form.hostname}>
              {saving ? 'Adding...' : 'Add Host'}
            </Button>
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                {selectMode && (
                  <th className="px-4 py-3 w-8">
                    <button onClick={toggleAll} className="text-slate-500 hover:text-slate-300">
                      {selected.size === filteredHosts.length && filteredHosts.length > 0 ? <CheckSquare size={16} /> : <Square size={16} />}
                    </button>
                  </th>
                )}
                <SortHeader label="Host" sortKey="name" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Status" sortKey="status" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Type" sortKey="type" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Source" sortKey="source" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Latency" sortKey="latency" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Availability" sortKey="uptime" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    {selectMode && <td className="px-4 py-3"><Skeleton className="h-4 w-4" /></td>}
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                  </tr>
                ))}
              {pagedHosts.map((host) => (
                <tr
                  key={host.id}
                  className={`border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors cursor-pointer ${selected.has(host.id) ? 'bg-sky-500/5' : ''}`}
                >
                  {selectMode && (
                    <td className="px-4 py-3">
                      <button onClick={(e) => { e.stopPropagation(); toggleSelect(host.id); }} className="text-slate-500 hover:text-slate-300">
                        {selected.has(host.id) ? <CheckSquare size={16} className="text-sky-400" /> : <Square size={16} />}
                      </button>
                    </td>
                  )}
                  <td className="px-4 py-3">
                    <Link href={`/hosts/${host.id}`} className="block">
                      <p className="font-medium text-slate-200">{host.name}</p>
                      <div className="flex items-center gap-1">
                        <p className="text-xs text-slate-500 font-mono">{host.hostname}</p>
                        <CopyButton text={host.hostname} size={12} />
                      </div>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <StatusDot
                        status={hostStatusKey(host)}
                        pulse={host.online === false || host.port_error}
                      />
                      <div>
                        <span className="text-xs text-slate-400 block">
                          {hostStatusLabel(host)}
                        </span>
                        {host.port_error && host.check_detail && (
                          <span className="text-[10px] text-red-400/80">
                            {Object.entries(host.check_detail).filter(([, ok]) => !ok).map(([k]) => k.toUpperCase()).join(', ')} failed
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge>{host.check_type}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-slate-400">{host.source ?? 'manual'}</span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {formatLatency(host.latency_ms)}
                  </td>
                  <td className="px-4 py-3">
                    <UptimeBar h24={host.uptime_h24} d7={host.uptime_d7} d30={host.uptime_d30} />
                  </td>
                </tr>
              ))}
              {!isLoading && filteredHosts.length === 0 && (
                <tr>
                  <td colSpan={selectMode ? 7 : 6} className="px-4 py-12 text-center">
                    <Server size={48} className="mx-auto mb-4 text-slate-600" />
                    <p className="text-base font-medium text-slate-300 mb-1">
                      {search ? 'No hosts match your search' : 'No hosts configured yet'}
                    </p>
                    <p className="text-sm text-slate-500 mb-4">
                      {search ? 'Try adjusting your search terms.' : 'Add a host to start monitoring your infrastructure.'}
                    </p>
                    {!search && (
                      <Button size="sm" onClick={() => setShowAdd(true)}>
                        <Plus size={16} /> Add your first host
                      </Button>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} total={filteredHosts.length} onPageChange={setPage} />
      </GlassCard>
      <Modal open={showBulkEdit} onClose={() => setShowBulkEdit(false)} title={`Bulk Edit (${selected.size} hosts)`}>
        <div className="space-y-4">
          <p className="text-xs text-slate-500">Leave fields empty to keep unchanged.</p>
          <div>
            <label className="ng-label">Check Type</label>
            <select value={bulkForm.check_type} onChange={(e) => setBulkForm({ ...bulkForm, check_type: e.target.value })} className={selectClass}>
              <option value="">— No change —</option>
              <option value="icmp">ICMP (Ping)</option>
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="tcp">TCP</option>
              <option value="dns">DNS</option>
            </select>
          </div>
          <div>
            <label className="ng-label">Enabled</label>
            <select value={bulkForm.enabled} onChange={(e) => setBulkForm({ ...bulkForm, enabled: e.target.value })} className={selectClass}>
              <option value="">— No change —</option>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </select>
          </div>
          <div>
            <label className="ng-label">Latency Threshold (ms)</label>
            <input type="number" placeholder="— No change —" value={bulkForm.latency_threshold_ms} onChange={(e) => setBulkForm({ ...bulkForm, latency_threshold_ms: e.target.value })} className={inputClass} />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={() => setShowBulkEdit(false)}>Cancel</Button>
            <Button size="sm" onClick={bulkEdit} disabled={bulkLoading}>
              {bulkLoading ? 'Saving...' : 'Apply Changes'}
            </Button>
          </div>
        </div>
      </Modal>
      {ConfirmDialogElement}
    </div>
  );
}

export default function HostsPage() {
  return (
    <Suspense>
      <HostsPageInner />
    </Suspense>
  );
}
