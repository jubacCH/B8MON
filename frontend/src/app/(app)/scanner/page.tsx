'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Modal } from '@/components/ui/Modal';
import { useToastStore } from '@/stores/toast';
import { get, post, del, patch } from '@/lib/api';
import {
  Search,
  Plus,
  Play,
  Trash2,
  Radar,
  CalendarClock,
} from 'lucide-react';

/* ---------- types ---------- */

interface AliveHost {
  ip: string;
  hostname_ptr: string | null;
  is_monitored: boolean;
  host_id: number | null;
}

interface ScanResult {
  alive: AliveHost[];
  total: number;
}

interface Schedule {
  id: number;
  name: string;
  cidr: string;
  interval_m: number;
  auto_add: boolean;
  enabled: boolean;
  last_run: string | null;
}

/* ---------- page ---------- */

export default function ScannerPage() {
  const toast = useToastStore((s) => s.show);
  const qc = useQueryClient();

  /* --- manual scan state --- */
  const [cidr, setCidr] = useState('');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  /* --- schedule modal state --- */
  const [showAddSchedule, setShowAddSchedule] = useState(false);
  const [schedName, setSchedName] = useState('');
  const [schedCidr, setSchedCidr] = useState('');
  const [schedInterval, setSchedInterval] = useState(60);
  const [schedAutoAdd, setSchedAutoAdd] = useState(false);

  /* --- queries --- */
  const { data: schedules, isLoading: schedulesLoading } = useQuery<Schedule[]>({
    queryKey: ['scanner-schedules'],
    queryFn: () => get<Schedule[]>('/api/subnet-scanner/page-data'),
  });

  /* --- mutations --- */
  const scanMutation = useMutation({
    mutationFn: (subnet: string) =>
      post<ScanResult>('/api/subnet-scanner/scan', { cidr: subnet }),
    onSuccess: (data) => {
      setScanResult(data);
      setSelected(new Set());
      toast(`Found ${data.alive.length} alive hosts out of ${data.total}`, 'success');
    },
    onError: () => toast('Scan failed', 'error'),
  });

  const addHostsMutation = useMutation({
    mutationFn: (ips: string[]) =>
      post('/api/subnet-scanner/add-hosts', { ips }),
    onSuccess: () => {
      toast('Hosts added to monitoring', 'success');
      setSelected(new Set());
      // re-scan to refresh monitored status
      if (cidr) scanMutation.mutate(cidr);
    },
    onError: () => toast('Failed to add hosts', 'error'),
  });

  const createScheduleMutation = useMutation({
    mutationFn: (body: { cidr: string; name: string; interval_m: number; auto_add: boolean }) =>
      post('/api/subnet-scanner/schedules', body),
    onSuccess: () => {
      toast('Schedule created', 'success');
      qc.invalidateQueries({ queryKey: ['scanner-schedules'] });
      setShowAddSchedule(false);
      resetScheduleForm();
    },
    onError: () => toast('Failed to create schedule', 'error'),
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: (id: number) => del(`/api/subnet-scanner/schedules/${id}`),
    onSuccess: () => {
      toast('Schedule deleted', 'success');
      qc.invalidateQueries({ queryKey: ['scanner-schedules'] });
    },
    onError: () => toast('Failed to delete schedule', 'error'),
  });

  const runNowMutation = useMutation({
    mutationFn: (id: number) => post(`/api/subnet-scanner/schedules/${id}/run`),
    onSuccess: () => toast('Scan triggered', 'success'),
    onError: () => toast('Failed to trigger scan', 'error'),
  });

  const toggleScheduleMutation = useMutation({
    mutationFn: ({ id, field, value }: { id: number; field: string; value: boolean }) =>
      patch(`/api/subnet-scanner/schedules/${id}`, { [field]: value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scanner-schedules'] }),
    onError: () => toast('Failed to update schedule', 'error'),
  });

  /* --- helpers --- */

  const resetScheduleForm = useCallback(() => {
    setSchedName('');
    setSchedCidr('');
    setSchedInterval(60);
    setSchedAutoAdd(false);
  }, []);

  const handleScan = () => {
    if (!cidr.trim()) return;
    scanMutation.mutate(cidr.trim());
  };

  const toggleSelect = (ip: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ip)) next.delete(ip);
      else next.add(ip);
      return next;
    });
  };

  const selectAllUnmonitored = () => {
    if (!scanResult) return;
    const unmonitored = scanResult.alive.filter((h) => !h.is_monitored).map((h) => h.ip);
    setSelected((prev) => {
      const allSelected = unmonitored.every((ip) => prev.has(ip));
      if (allSelected) return new Set();
      return new Set(unmonitored);
    });
  };

  const handleAddSelected = () => {
    const ips = Array.from(selected);
    if (ips.length === 0) return;
    addHostsMutation.mutate(ips);
  };

  const handleCreateSchedule = () => {
    if (!schedName.trim() || !schedCidr.trim()) return;
    createScheduleMutation.mutate({
      name: schedName.trim(),
      cidr: schedCidr.trim(),
      interval_m: schedInterval,
      auto_add: schedAutoAdd,
    });
  };

  const unmonitoredCount = scanResult?.alive.filter((h) => !h.is_monitored).length ?? 0;

  return (
    <div>
      <PageHeader
        title="Scanner"
        description="Network discovery and scheduled subnet scanning"
        actions={
          <Button size="sm" onClick={() => setShowAddSchedule(true)}>
            <Plus size={16} />
            Add Schedule
          </Button>
        }
      />

      {/* ── Manual Scan ── */}
      <GlassCard className="mb-6">
        <div className="px-6 py-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <Radar size={18} className="text-sky-400" />
            <h2 className="text-sm font-semibold text-slate-200">Manual Scan</h2>
          </div>
        </div>
        <div className="p-6">
          <div className="flex items-center gap-3 mb-6">
            <input
              type="text"
              value={cidr}
              onChange={(e) => setCidr(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleScan()}
              placeholder="e.g. 10.10.30.0/24"
              className="flex-1 max-w-sm bg-white/[0.04] border border-white/[0.08] rounded-md px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/30"
            />
            <Button
              onClick={handleScan}
              disabled={scanMutation.isPending || !cidr.trim()}
            >
              <Search size={16} />
              {scanMutation.isPending ? 'Scanning...' : 'Scan'}
            </Button>
          </div>

          {/* scan loading skeleton */}
          {scanMutation.isPending && (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-3">
                  <Skeleton className="h-4 w-4" />
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-16" />
                </div>
              ))}
            </div>
          )}

          {/* scan results */}
          {scanResult && !scanMutation.isPending && (
            <>
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs text-slate-400">
                  {scanResult.alive.length} alive / {scanResult.total} scanned
                  {unmonitoredCount > 0 && (
                    <span className="ml-2 text-amber-400">
                      ({unmonitoredCount} unmonitored)
                    </span>
                  )}
                </p>
                <div className="flex items-center gap-2">
                  {unmonitoredCount > 0 && (
                    <Button size="sm" variant="ghost" onClick={selectAllUnmonitored}>
                      {selected.size === unmonitoredCount ? 'Deselect All' : 'Select All Unmonitored'}
                    </Button>
                  )}
                  {selected.size > 0 && (
                    <Button
                      size="sm"
                      onClick={handleAddSelected}
                      disabled={addHostsMutation.isPending}
                    >
                      <Plus size={14} />
                      Add {selected.size} Selected
                    </Button>
                  )}
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/[0.06]">
                      <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider w-10" />
                      <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                        IP Address
                      </th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                        PTR Hostname
                      </th>
                      <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {scanResult.alive.map((host) => (
                      <tr
                        key={host.ip}
                        className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors"
                      >
                        <td className="px-4 py-3">
                          {!host.is_monitored && (
                            <input
                              type="checkbox"
                              checked={selected.has(host.ip)}
                              onChange={() => toggleSelect(host.ip)}
                              className="rounded border-white/20 bg-white/[0.04] text-sky-500 focus:ring-sky-500/50"
                            />
                          )}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-200">
                          {host.ip}
                        </td>
                        <td className="px-4 py-3 text-slate-400">
                          {host.hostname_ptr || '—'}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <StatusDot status={host.is_monitored ? 'online' : 'unknown'} />
                            <span className="text-xs text-slate-400">
                              {host.is_monitored ? 'Monitored' : 'Not monitored'}
                            </span>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {scanResult.alive.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-8 text-center text-sm text-slate-500">
                          No alive hosts found in this subnet
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </GlassCard>

      {/* ── Scheduled Scans ── */}
      <GlassCard>
        <div className="px-6 py-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <CalendarClock size={18} className="text-sky-400" />
            <h2 className="text-sm font-semibold text-slate-200">Scheduled Scans</h2>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Name
                </th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  CIDR
                </th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Interval
                </th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Auto-Add
                </th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Last Run
                </th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {schedulesLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-28" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-12" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-24" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 ml-auto" /></td>
                  </tr>
                ))}
              {schedules?.map((sched) => (
                <tr
                  key={sched.id}
                  className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors"
                >
                  <td className="px-4 py-3 font-medium text-slate-200">
                    {sched.name}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {sched.cidr}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs">
                    {sched.interval_m >= 60
                      ? `${Math.floor(sched.interval_m / 60)}h${sched.interval_m % 60 ? ` ${sched.interval_m % 60}m` : ''}`
                      : `${sched.interval_m}m`}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() =>
                        toggleScheduleMutation.mutate({
                          id: sched.id,
                          field: 'auto_add',
                          value: !sched.auto_add,
                        })
                      }
                      className="cursor-pointer"
                    >
                      <Badge
                        className={
                          sched.auto_add
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                            : 'bg-white/[0.04] text-slate-500 border-white/[0.06]'
                        }
                      >
                        {sched.auto_add ? 'On' : 'Off'}
                      </Badge>
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() =>
                        toggleScheduleMutation.mutate({
                          id: sched.id,
                          field: 'enabled',
                          value: !sched.enabled,
                        })
                      }
                      className="cursor-pointer"
                    >
                      <Badge
                        className={
                          sched.enabled
                            ? 'bg-sky-500/10 text-sky-400 border-sky-500/20'
                            : 'bg-white/[0.04] text-slate-500 border-white/[0.06]'
                        }
                      >
                        {sched.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                    </button>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {sched.last_run
                      ? new Date(sched.last_run).toLocaleString()
                      : 'Never'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => runNowMutation.mutate(sched.id)}
                        disabled={runNowMutation.isPending}
                        title="Run now"
                      >
                        <Play size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => deleteScheduleMutation.mutate(sched.id)}
                        disabled={deleteScheduleMutation.isPending}
                        title="Delete"
                      >
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!schedulesLoading && (!schedules || schedules.length === 0) && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-slate-500">
                    No scheduled scans configured
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>

      {/* ── Add Schedule Modal ── */}
      <Modal
        open={showAddSchedule}
        onClose={() => {
          setShowAddSchedule(false);
          resetScheduleForm();
        }}
        title="Add Scheduled Scan"
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Name
            </label>
            <input
              type="text"
              value={schedName}
              onChange={(e) => setSchedName(e.target.value)}
              placeholder="e.g. Office LAN"
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/30"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              CIDR Subnet
            </label>
            <input
              type="text"
              value={schedCidr}
              onChange={(e) => setSchedCidr(e.target.value)}
              placeholder="e.g. 10.10.30.0/24"
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/30"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Interval (minutes)
            </label>
            <input
              type="number"
              value={schedInterval}
              onChange={(e) => setSchedInterval(Number(e.target.value))}
              min={5}
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500/30"
            />
          </div>
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="sched-auto-add"
              checked={schedAutoAdd}
              onChange={(e) => setSchedAutoAdd(e.target.checked)}
              className="rounded border-white/20 bg-white/[0.04] text-sky-500 focus:ring-sky-500/50"
            />
            <label htmlFor="sched-auto-add" className="text-sm text-slate-300">
              Automatically add discovered hosts to monitoring
            </label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              onClick={() => {
                setShowAddSchedule(false);
                resetScheduleForm();
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreateSchedule}
              disabled={
                createScheduleMutation.isPending ||
                !schedName.trim() ||
                !schedCidr.trim()
              }
            >
              {createScheduleMutation.isPending ? 'Creating...' : 'Create Schedule'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
