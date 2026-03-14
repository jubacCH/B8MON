'use client';

import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { get, post } from '@/lib/api';
import { ShieldCheck, RefreshCw, ChevronDown, ChevronUp, Lock, Key, FileText, Globe } from 'lucide-react';
import { ExportButton } from '@/components/ui/ExportButton';
import Link from 'next/link';
import { useEffect, useState } from 'react';

interface SslCert {
  id: number;
  name: string;
  hostname: string;
  enabled: boolean;
  days: number | null;
}

interface SslData {
  certs: SslCert[];
  expiring_soon: number;
}

interface SslDetail {
  ok: boolean;
  error?: string;
  days?: number;
  expiry_date?: string;
  issued_date?: string;
  issuer?: string;
  issuer_cn?: string;
  issuer_o?: string;
  subject?: string;
  subject_cn?: string;
  subject_o?: string;
  sans?: string[];
  serial?: string;
  fingerprint?: string;
  signature_algorithm?: string;
  key_size?: number;
  port?: number;
}

function expiryBadge(days: number | null): { severity: 'critical' | 'warning' | 'info'; label: string } {
  if (days === null) return { severity: 'warning', label: 'Unknown' };
  if (days <= 7) return { severity: 'critical', label: `${days}d` };
  if (days <= 30) return { severity: 'warning', label: `${days}d` };
  return { severity: 'info', label: `${days}d` };
}

function expiryColor(days: number | null): string {
  if (days === null) return 'text-slate-500';
  if (days <= 7) return 'text-red-400';
  if (days <= 30) return 'text-amber-400';
  return 'text-emerald-400';
}

export default function SslPage() {
  useEffect(() => { document.title = 'SSL | Nodeglow'; }, []);
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const { data, isLoading } = useQuery({
    queryKey: ['ssl-certs'],
    queryFn: () => get<SslData>('/api/ssl/certs'),
  });

  const certs = data?.certs ?? [];
  const expiringSoon = data?.expiring_soon ?? 0;

  async function refreshAll() {
    setRefreshing(true);
    try {
      await post('/api/ssl/refresh-all');
      qc.invalidateQueries({ queryKey: ['ssl-certs'] });
    } finally {
      setRefreshing(false);
    }
  }

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div>
      <PageHeader
        title="SSL Certificates"
        description="Certificate expiry monitoring"
        actions={
          <div className="flex items-center gap-2">
            {certs.length > 0 && (
              <ExportButton
                data={certs.map(c => ({ name: c.name, hostname: c.hostname, days_until_expiry: c.days }))}
                filename="ssl-certificates"
                columns={[
                  { key: 'name', label: 'Name' },
                  { key: 'hostname', label: 'Hostname' },
                  { key: 'days_until_expiry', label: 'Days Until Expiry' },
                ]}
              />
            )}
            <Button variant="ghost" size="sm" onClick={refreshAll} disabled={refreshing}>
              <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
              {refreshing ? 'Refreshing...' : 'Refresh All'}
            </Button>
          </div>
        }
      />

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-slate-100">{certs.length}</p>
          <p className="text-xs text-slate-400 mt-1">HTTPS Hosts</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className={`text-2xl font-semibold ${expiringSoon > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>
            {expiringSoon}
          </p>
          <p className="text-xs text-slate-400 mt-1">Expiring Soon (&le;30d)</p>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <p className="text-2xl font-semibold text-emerald-400">
            {certs.filter(c => c.days !== null && c.days > 30).length}
          </p>
          <p className="text-xs text-slate-400 mt-1">Healthy</p>
        </GlassCard>
      </div>

      <GlassCard>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="w-8 px-2 py-3" />
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Host</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Hostname</th>
                <th className="text-right px-4 py-3 text-xs font-medium text-slate-500 uppercase">Expiry</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
              </tr>
            </thead>
            <tbody>
              {isLoading &&
                Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i} className="border-b border-white/[0.06]">
                    <td className="px-2 py-3" />
                    <td className="px-4 py-3"><Skeleton className="h-5 w-32" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-40" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-16 ml-auto" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-5 w-20 mx-auto" /></td>
                  </tr>
                ))}
              {certs.map((c) => {
                const badge = expiryBadge(c.days);
                const isExpanded = expanded.has(c.id);
                return (
                  <>
                    <tr
                      key={c.id}
                      className="border-b border-white/[0.06] hover:bg-white/[0.06] transition-colors cursor-pointer"
                      onClick={() => toggle(c.id)}
                    >
                      <td className="px-2 py-3 text-slate-500">
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/hosts/${c.id}`}
                          className="flex items-center gap-2 text-slate-200 hover:text-sky-400"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ShieldCheck size={14} className={expiryColor(c.days)} />
                          {c.name}
                        </Link>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-400">{c.hostname}</td>
                      <td className={`px-4 py-3 text-right font-mono ${expiryColor(c.days)}`}>
                        {c.days !== null ? c.days : '—'}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <Badge variant="severity" severity={badge.severity}>
                          {badge.label}
                        </Badge>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`detail-${c.id}`} className="border-b border-white/[0.06]">
                        <td colSpan={5} className="p-0">
                          <CertDetail hostId={c.id} />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
              {!isLoading && certs.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-sm text-slate-500">
                    No HTTPS hosts configured
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}

function CertDetail({ hostId }: { hostId: number }) {
  const { data, isLoading } = useQuery<SslDetail>({
    queryKey: ['ssl-detail', hostId],
    queryFn: () => get(`/api/ssl/detail/${hostId}`),
    staleTime: 5 * 60_000,
  });

  if (isLoading) {
    return (
      <div className="px-6 py-4 bg-white/[0.02] space-y-2">
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-56" />
      </div>
    );
  }

  if (!data || !data.ok) {
    return (
      <div className="px-6 py-4 bg-white/[0.02]">
        <p className="text-sm text-red-400">
          Failed to fetch certificate details{data?.error ? `: ${data.error}` : ''}
        </p>
      </div>
    );
  }

  return (
    <div className="px-6 py-4 bg-white/[0.02]">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
        {/* Subject */}
        <div>
          <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Globe size={12} /> Subject
          </h4>
          <div className="space-y-1">
            <DetailRow label="Common Name" value={data.subject_cn} mono />
            {data.subject_o && <DetailRow label="Organization" value={data.subject_o} />}
            {data.subject && data.subject !== data.subject_cn && (
              <DetailRow label="Full" value={data.subject} small />
            )}
          </div>
        </div>

        {/* Issuer */}
        <div>
          <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Lock size={12} /> Issuer
          </h4>
          <div className="space-y-1">
            <DetailRow label="Common Name" value={data.issuer_cn} mono />
            {data.issuer_o && <DetailRow label="Organization" value={data.issuer_o} />}
            {data.issuer && data.issuer !== data.issuer_cn && (
              <DetailRow label="Full" value={data.issuer} small />
            )}
          </div>
        </div>

        {/* Validity */}
        <div>
          <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <FileText size={12} /> Validity
          </h4>
          <div className="space-y-1">
            <DetailRow label="Not Before" value={data.issued_date} />
            <DetailRow label="Not After" value={data.expiry_date} highlight={data.days != null && data.days <= 30} />
            {data.days != null && (
              <DetailRow
                label="Remaining"
                value={`${data.days} days`}
                highlight={data.days <= 30}
              />
            )}
          </div>
        </div>

        {/* Technical */}
        <div>
          <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Key size={12} /> Technical
          </h4>
          <div className="space-y-1">
            {data.signature_algorithm && <DetailRow label="Signature" value={data.signature_algorithm} />}
            {data.key_size && <DetailRow label="Key Size" value={`${data.key_size} bit`} />}
            {data.port && <DetailRow label="Port" value={String(data.port)} />}
            {data.serial && <DetailRow label="Serial" value={data.serial} mono small />}
            {data.fingerprint && <DetailRow label="Fingerprint" value={data.fingerprint} mono small />}
          </div>
        </div>
      </div>

      {/* SANs */}
      {data.sans && data.sans.length > 0 && (
        <div className="mt-4">
          <h4 className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Globe size={12} /> Subject Alternative Names ({data.sans.length})
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {data.sans.map((san, i) => (
              <span key={i} className="px-2 py-0.5 rounded-md bg-white/[0.04] text-xs font-mono text-slate-300 border border-white/[0.06]">
                {san}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono,
  small,
  highlight,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
  small?: boolean;
  highlight?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="flex items-start gap-2">
      <span className="text-[10px] text-slate-500 uppercase w-24 shrink-0 pt-0.5">{label}</span>
      <span
        className={`text-xs break-all ${
          highlight ? 'text-amber-400' : 'text-slate-300'
        } ${mono ? 'font-mono' : ''} ${small ? 'text-[11px] text-slate-400' : ''}`}
      >
        {value}
      </span>
    </div>
  );
}
