'use client';

import { useState, useCallback, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { Modal } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToastStore } from '@/stores/toast';
import { get, post, del, patch, api } from '@/lib/api';
import {
  Upload,
  Trash2,
  Search,
  Download,
  Plus,
  Play,
  Database,
  Server,
  BookOpen,
  RefreshCw,
} from 'lucide-react';

/* ---------- types ---------- */

interface Mib {
  id: number;
  name: string;
  oid_count: number;
  uploaded_at?: string;
}

interface HostConfig {
  id: number;
  host_id: number;
  hostname: string;
  credential_id: number;
  credential_name?: string;
  port: number;
  poll_interval: number;
  enabled: boolean;
  preset?: string;
  last_poll?: string;
}

interface AvailableHost {
  id: number;
  name: string;
  hostname: string;
}

interface OidEntry {
  oid: string;
  name: string;
  mib: string;
  syntax?: string;
}

interface LibraryMib {
  name: string;
  description?: string;
}

interface PageData {
  mibs: Mib[];
  host_configs: HostConfig[];
  available_hosts: AvailableHost[];
}

/* ---------- tabs ---------- */

type Tab = 'mibs' | 'hosts' | 'oids';

const tabs: { key: Tab; label: string; icon: typeof Database }[] = [
  { key: 'mibs', label: 'MIB Library', icon: Database },
  { key: 'hosts', label: 'Host Configs', icon: Server },
  { key: 'oids', label: 'OID Browser', icon: BookOpen },
];

/* ---------- page ---------- */

export default function SnmpPage() {
  const [activeTab, setActiveTab] = useState<Tab>('mibs');

  return (
    <div>
      <PageHeader title="SNMP" description="SNMP monitoring and MIB management" />

      {/* tab bar */}
      <div className="flex items-center gap-1 mb-4">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                activeTab === t.key
                  ? 'bg-white/[0.06] text-slate-100'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.03]'
              }`}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
      </div>

      {activeTab === 'mibs' && <MibLibraryTab />}
      {activeTab === 'hosts' && <HostConfigsTab />}
      {activeTab === 'oids' && <OidBrowserTab />}
    </div>
  );
}

/* ================================================================
   MIB Library Tab
   ================================================================ */

function MibLibraryTab() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const fileRef = useRef<HTMLInputElement>(null);
  const [libraryQuery, setLibraryQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [libraryResults, setLibraryResults] = useState<LibraryMib[]>([]);

  const { data, isLoading } = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  const mibs = data?.mibs ?? [];

  /* upload */
  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return api('/api/snmp/mibs/upload', { method: 'POST', body: form });
    },
    onSuccess: () => {
      toast.show('MIB uploaded', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Upload failed', 'error'),
  });

  const handleUpload = useCallback(() => fileRef.current?.click(), []);
  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadMut.mutate(file);
      e.target.value = '';
    },
    [uploadMut],
  );

  /* delete */
  const deleteMut = useMutation({
    mutationFn: (id: number) => del(`/api/snmp/mibs/${id}`),
    onSuccess: () => {
      toast.show('MIB deleted', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Delete failed', 'error'),
  });

  /* seed defaults */
  const seedMut = useMutation({
    mutationFn: () => post('/api/snmp/mibs/seed-defaults'),
    onSuccess: () => {
      toast.show('Default MIBs seeded', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Seed failed', 'error'),
  });

  /* library search */
  const searchLibrary = useCallback(async () => {
    if (!libraryQuery.trim()) return;
    setSearching(true);
    try {
      const res = await get<LibraryMib[]>(`/api/snmp/mibs/library/search?q=${encodeURIComponent(libraryQuery)}`);
      setLibraryResults(res);
    } catch {
      toast.show('Search failed', 'error');
    } finally {
      setSearching(false);
    }
  }, [libraryQuery, toast]);

  /* library import */
  const importMut = useMutation({
    mutationFn: (mib_name: string) => post('/api/snmp/mibs/library/import', { mib_name }),
    onSuccess: () => {
      toast.show('MIB imported', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Import failed', 'error'),
  });

  return (
    <div className="space-y-4">
      {/* uploaded MIBs */}
      <GlassCard>
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
          <h3 className="text-sm font-semibold text-slate-200">Uploaded MIBs</h3>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => seedMut.mutate()} disabled={seedMut.isPending}>
              <RefreshCw size={14} className={seedMut.isPending ? 'animate-spin' : ''} />
              Seed Defaults
            </Button>
            <Button size="sm" onClick={handleUpload} disabled={uploadMut.isPending}>
              <Upload size={14} />
              Upload MIB
            </Button>
            <input ref={fileRef} type="file" className="hidden" accept=".mib,.txt,.my" onChange={onFileChange} />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Name</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">OIDs</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Uploaded</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-8 ml-auto" /></td>
                  </tr>
                ))}
              {!isLoading && mibs.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                    No MIBs uploaded yet. Upload a file or seed defaults.
                  </td>
                </tr>
              )}
              {mibs.map((mib) => (
                <tr key={mib.id} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                  <td className="px-4 py-3 font-medium text-slate-200 font-mono text-xs">{mib.name}</td>
                  <td className="px-4 py-3">
                    <Badge>{mib.oid_count}</Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {mib.uploaded_at ? new Date(mib.uploaded_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => deleteMut.mutate(mib.id)}
                      disabled={deleteMut.isPending}
                    >
                      <Trash2 size={13} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </GlassCard>

      {/* online library search */}
      <GlassCard>
        <div className="px-4 py-3 border-b border-white/[0.06]">
          <h3 className="text-sm font-semibold text-slate-200">Online MIB Library</h3>
          <p className="text-xs text-slate-500 mt-0.5">Search and import MIBs from the online library</p>
        </div>
        <div className="p-4 space-y-3">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                type="text"
                placeholder="Search MIBs (e.g. IF-MIB, CISCO, SYNOLOGY)..."
                value={libraryQuery}
                onChange={(e) => setLibraryQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && searchLibrary()}
                className="w-full pl-9 pr-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500/50"
              />
            </div>
            <Button size="sm" onClick={searchLibrary} disabled={searching || !libraryQuery.trim()}>
              <Search size={14} />
              Search
            </Button>
          </div>

          {searching && (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          )}

          {!searching && libraryResults.length > 0 && (
            <div className="space-y-1">
              {libraryResults.map((m) => (
                <div
                  key={m.name}
                  className="flex items-center justify-between px-3 py-2 rounded-md hover:bg-white/[0.06] transition-colors"
                >
                  <div>
                    <span className="text-sm font-mono text-slate-200">{m.name}</span>
                    {m.description && (
                      <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{m.description}</p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => importMut.mutate(m.name)}
                    disabled={importMut.isPending}
                  >
                    <Download size={13} />
                    Import
                  </Button>
                </div>
              ))}
            </div>
          )}

          {!searching && libraryResults.length === 0 && libraryQuery && (
            <p className="text-xs text-slate-500 text-center py-4">No results. Try a different search term.</p>
          )}
        </div>
      </GlassCard>
    </div>
  );
}

/* ================================================================
   Host Configs Tab
   ================================================================ */

function HostConfigsTab() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading } = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  const configs = data?.host_configs ?? [];
  const availableHosts = data?.available_hosts ?? [];

  /* poll now */
  const pollMut = useMutation({
    mutationFn: (configId: number) => post(`/api/snmp/hosts/${configId}/poll`),
    onSuccess: () => toast.show('Poll triggered', 'success'),
    onError: () => toast.show('Poll failed', 'error'),
  });

  /* delete config */
  const deleteMut = useMutation({
    mutationFn: (configId: number) => del(`/api/snmp/hosts/${configId}`),
    onSuccess: () => {
      toast.show('Host config deleted', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
    },
    onError: () => toast.show('Delete failed', 'error'),
  });

  /* toggle enabled */
  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      patch(`/api/snmp/hosts/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['snmp-page'] }),
    onError: () => toast.show('Update failed', 'error'),
  });

  return (
    <>
      <GlassCard>
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
          <h3 className="text-sm font-semibold text-slate-200">SNMP-Monitored Hosts</h3>
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus size={14} />
            Add Host
          </Button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Credential</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Port</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Interval</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Last Poll</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                  </tr>
                ))}
              {!isLoading && configs.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-slate-500">
                    No SNMP host configs yet. Click &quot;Add Host&quot; to get started.
                  </td>
                </tr>
              )}
              {configs.map((cfg) => (
                <tr key={cfg.id} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-slate-200">{cfg.hostname}</p>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {cfg.credential_name ?? `#${cfg.credential_id}`}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">{cfg.port}</td>
                  <td className="px-4 py-3 text-xs text-slate-300">{cfg.poll_interval}s</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => toggleMut.mutate({ id: cfg.id, enabled: !cfg.enabled })}
                      className="flex items-center gap-2"
                    >
                      <StatusDot status={cfg.enabled ? 'online' : 'disabled'} />
                      <span className="text-xs text-slate-400">{cfg.enabled ? 'Enabled' : 'Disabled'}</span>
                    </button>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {cfg.last_poll ? new Date(cfg.last_poll).toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => pollMut.mutate(cfg.id)}
                        disabled={pollMut.isPending}
                      >
                        <Play size={13} />
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => deleteMut.mutate(cfg.id)}
                        disabled={deleteMut.isPending}
                      >
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </GlassCard>

      <AddHostModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        availableHosts={availableHosts}
      />
    </>
  );
}

/* ---------- Add Host Modal ---------- */

function AddHostModal({
  open,
  onClose,
  availableHosts,
}: {
  open: boolean;
  onClose: () => void;
  availableHosts: AvailableHost[];
}) {
  const qc = useQueryClient();
  const toast = useToastStore();

  const [hostId, setHostId] = useState('');
  const [credentialId, setCredentialId] = useState('');
  const [port, setPort] = useState('161');
  const [interval, setInterval] = useState('300');
  const [preset, setPreset] = useState('standard');

  const createMut = useMutation({
    mutationFn: () =>
      post('/api/snmp/hosts', {
        host_id: Number(hostId),
        credential_id: Number(credentialId),
        port: Number(port),
        poll_interval: Number(interval),
        preset,
      }),
    onSuccess: () => {
      toast.show('Host config created', 'success');
      qc.invalidateQueries({ queryKey: ['snmp-page'] });
      onClose();
      setHostId('');
      setCredentialId('');
      setPort('161');
      setInterval('300');
      setPreset('standard');
    },
    onError: () => toast.show('Failed to create config', 'error'),
  });

  const inputCls =
    'w-full px-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500/50';

  return (
    <Modal open={open} onClose={onClose} title="Add SNMP Host">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          createMut.mutate();
        }}
        className="space-y-4"
      >
        {/* host select */}
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">Host</label>
          <select value={hostId} onChange={(e) => setHostId(e.target.value)} className={`${inputCls} !bg-[#111621]`} required>
            <option value="" className="bg-[#111621] text-slate-200">Select a host...</option>
            {availableHosts.map((h) => (
              <option key={h.id} value={h.id} className="bg-[#111621] text-slate-200">
                {h.name} ({h.hostname})
              </option>
            ))}
          </select>
        </div>

        {/* credential id */}
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">Credential ID</label>
          <input
            type="number"
            min={1}
            placeholder="Credential ID"
            value={credentialId}
            onChange={(e) => setCredentialId(e.target.value)}
            className={inputCls}
            required
          />
        </div>

        {/* port + interval row */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Port</label>
            <input
              type="number"
              min={1}
              max={65535}
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className={inputCls}
              required
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Poll Interval (s)</label>
            <input
              type="number"
              min={10}
              value={interval}
              onChange={(e) => setInterval(e.target.value)}
              className={inputCls}
              required
            />
          </div>
        </div>

        {/* preset */}
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">Preset</label>
          <select value={preset} onChange={(e) => setPreset(e.target.value)} className={`${inputCls} !bg-[#111621]`}>
            <option value="standard" className="bg-[#111621] text-slate-200">Standard (system + interfaces)</option>
            <option value="minimal" className="bg-[#111621] text-slate-200">Minimal (sysDescr only)</option>
            <option value="full" className="bg-[#111621] text-slate-200">Full (all common OIDs)</option>
            <option value="custom" className="bg-[#111621] text-slate-200">Custom OIDs</option>
          </select>
        </div>

        {/* submit */}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={createMut.isPending || !hostId || !credentialId}>
            {createMut.isPending ? 'Creating...' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/* ================================================================
   OID Browser Tab
   ================================================================ */

function OidBrowserTab() {
  const [mibFilter, setMibFilter] = useState('');
  const [keyword, setKeyword] = useState('');
  const [searchTriggered, setSearchTriggered] = useState(false);

  const queryParams = new URLSearchParams();
  if (mibFilter) queryParams.set('mib', mibFilter);
  if (keyword) queryParams.set('q', keyword);
  const qs = queryParams.toString();

  const { data: oids, isLoading, isFetching } = useQuery<OidEntry[]>({
    queryKey: ['snmp-oids', qs],
    queryFn: () => get(`/api/snmp/oids?${qs}`),
    enabled: searchTriggered,
  });

  const { data: pageData } = useQuery<PageData>({
    queryKey: ['snmp-page'],
    queryFn: () => get('/api/snmp/page-data'),
  });

  const mibNames = (pageData?.mibs ?? []).map((m) => m.name);

  const doSearch = () => setSearchTriggered(true);

  const inputCls =
    'w-full px-3 py-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-sky-500/50';

  return (
    <GlassCard>
      <div className="px-4 py-3 border-b border-white/[0.06]">
        <h3 className="text-sm font-semibold text-slate-200">OID Browser</h3>
        <p className="text-xs text-slate-500 mt-0.5">Search and explore OIDs from loaded MIBs</p>
      </div>

      {/* filters */}
      <div className="px-4 py-3 border-b border-white/[0.06]">
        <div className="flex gap-2">
          <select
            value={mibFilter}
            onChange={(e) => {
              setMibFilter(e.target.value);
              setSearchTriggered(false);
            }}
            className={`${inputCls} !bg-[#111621] max-w-[200px]`}
          >
            <option value="" className="bg-[#111621] text-slate-200">All MIBs</option>
            {mibNames.map((n) => (
              <option key={n} value={n} className="bg-[#111621] text-slate-200">
                {n}
              </option>
            ))}
          </select>
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Search OIDs by name or OID string..."
              value={keyword}
              onChange={(e) => {
                setKeyword(e.target.value);
                setSearchTriggered(false);
              }}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
              className={`${inputCls} pl-9`}
            />
          </div>
          <Button size="sm" onClick={doSearch}>
            <Search size={14} />
            Search
          </Button>
        </div>
      </div>

      {/* results */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">OID</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Name</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">MIB</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">Syntax</th>
            </tr>
          </thead>
          <tbody>
            {(isLoading || isFetching) &&
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} className="border-b border-white/[0.06]">
                  <td className="px-4 py-3"><Skeleton className="h-5 w-48" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                  <td className="px-4 py-3"><Skeleton className="h-5 w-20" /></td>
                </tr>
              ))}
            {!isLoading && !isFetching && searchTriggered && (oids?.length ?? 0) === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                  No OIDs found. Try a different filter or search term.
                </td>
              </tr>
            )}
            {!isLoading && !isFetching && !searchTriggered && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                  Select a MIB or enter a keyword and click Search to browse OIDs.
                </td>
              </tr>
            )}
            {!isFetching &&
              oids?.map((oid, i) => (
                <tr key={`${oid.oid}-${i}`} className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors">
                  <td className="px-4 py-3 font-mono text-xs text-slate-300 select-all">{oid.oid}</td>
                  <td className="px-4 py-3 text-sm text-slate-200">{oid.name}</td>
                  <td className="px-4 py-3">
                    <Badge>{oid.mib}</Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">{oid.syntax ?? '—'}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}
