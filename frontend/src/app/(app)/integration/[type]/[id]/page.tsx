'use client';

import { useParams } from 'next/navigation';
import { PageHeader } from '@/components/layout/PageHeader';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { useIntegration } from '@/hooks/queries/useIntegrations';
import { ProxmoxDetail } from '@/components/integrations/ProxmoxDetail';
import { UnifiDetail } from '@/components/integrations/UnifiDetail';
import { PiholeDetail } from '@/components/integrations/PiholeDetail';
import { PortainerDetail } from '@/components/integrations/PortainerDetail';
import { SynologyDetail } from '@/components/integrations/SynologyDetail';
import { UnasDetail } from '@/components/integrations/UnasDetail';
import { SpeedtestDetail } from '@/components/integrations/SpeedtestDetail';
import { PhpipamDetail } from '@/components/integrations/PhpipamDetail';
import { AdguardDetail } from '@/components/integrations/AdguardDetail';
import { TruenasDetail } from '@/components/integrations/TruenasDetail';
import { FirewallDetail } from '@/components/integrations/FirewallDetail';
import { HassDetail } from '@/components/integrations/HassDetail';
import { GiteaDetail } from '@/components/integrations/GiteaDetail';
import { UpsDetail } from '@/components/integrations/UpsDetail';
import { RedfishDetail } from '@/components/integrations/RedfishDetail';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';

/* eslint-disable @typescript-eslint/no-explicit-any */
const detailComponents: Record<string, React.ComponentType<{ data: any }>> = {
  proxmox: ProxmoxDetail,
  unifi: UnifiDetail,
  pihole: PiholeDetail,
  portainer: PortainerDetail,
  synology: SynologyDetail,
  unas: UnasDetail,
  speedtest: SpeedtestDetail,
  phpipam: PhpipamDetail,
  adguard: AdguardDetail,
  truenas: TruenasDetail,
  firewall: FirewallDetail,
  hass: HassDetail,
  gitea: GiteaDetail,
  ups: UpsDetail,
  redfish: RedfishDetail,
};
/* eslint-enable @typescript-eslint/no-explicit-any */

export default function IntegrationDetailPage() {
  const params = useParams();
  const type = params.type as string;
  const id = Number(params.id);
  const { data: snapshot, isLoading } = useIntegration(id);

  const DetailComponent = detailComponents[type];

  return (
    <div>
      <PageHeader
        title={isLoading ? 'Loading...' : `${type.charAt(0).toUpperCase() + type.slice(1)} #${id}`}
        description={snapshot?.entity_type ?? type}
        actions={
          <Link href={`/integration/${type}`}>
            <Button variant="ghost" size="sm">
              <ArrowLeft size={16} />
              Back
            </Button>
          </Link>
        }
      />

      {/* Status header */}
      <GlassCard className="p-4 mb-6">
        {isLoading ? (
          <div className="flex items-center gap-4">
            <Skeleton className="h-5 w-5 rounded-full" />
            <Skeleton className="h-5 w-48" />
          </div>
        ) : snapshot ? (
          <div className="flex items-center gap-4">
            <StatusDot status={snapshot.ok ? 'online' : 'offline'} pulse={!snapshot.ok} />
            <div className="flex-1">
              <p className="text-sm font-medium text-slate-200">
                {snapshot.ok ? 'Healthy' : 'Error'}
              </p>
              <p className="text-xs text-slate-500">
                Last updated: {new Date(snapshot.timestamp).toLocaleString()}
              </p>
            </div>
            <Badge>{snapshot.entity_type}</Badge>
          </div>
        ) : (
          <p className="text-sm text-slate-400">Snapshot not found</p>
        )}
      </GlassCard>

      {/* Error display */}
      {snapshot?.error && (
        <GlassCard className="p-4 mb-6 border-red-500/30 bg-red-500/5">
          <h3 className="text-sm font-medium text-red-400 mb-2">Error</h3>
          <p className="text-xs text-red-300 font-mono">{snapshot.error}</p>
        </GlassCard>
      )}

      {/* Type-specific or generic detail */}
      {isLoading ? (
        <GlassCard className="p-4">
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        </GlassCard>
      ) : snapshot?.data_json && DetailComponent ? (
        <DetailComponent data={snapshot.data_json} />
      ) : snapshot?.data_json ? (
        <GlassCard className="p-4">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Snapshot Data</h3>
          <pre className="text-xs text-slate-300 font-mono bg-black/20 rounded-md p-4 overflow-auto max-h-[500px]">
            {JSON.stringify(snapshot.data_json, null, 2)}
          </pre>
        </GlassCard>
      ) : (
        <GlassCard className="p-4">
          <p className="text-sm text-slate-500">No data available</p>
        </GlassCard>
      )}
    </div>
  );
}
