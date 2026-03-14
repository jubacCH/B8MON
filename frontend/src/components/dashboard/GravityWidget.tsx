'use client';

import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html, useTexture } from '@react-three/drei';
import { useRouter } from 'next/navigation';
import * as THREE from 'three';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import Link from 'next/link';
import type { HostStat } from '@/hooks/queries/useDashboard';

/* ── Health scoring ── */

function hostHealth(h: HostStat): number {
  if (h.host.maintenance) return 0.4;
  if (h.online === false) return 1.0;
  if (h.online === null) return 0.7;
  let score = 0;
  if (h.latency != null) {
    if (h.latency > 200) score += 0.3;
    else if (h.latency > 100) score += 0.15;
    else if (h.latency > 50) score += 0.05;
  }
  const uptime = h.uptime_stats?.h24;
  if (uptime != null && uptime < 100) {
    score += (1 - uptime / 100) * 0.4;
  }
  return Math.min(score, 1);
}

function hostColor(h: HostStat): string {
  if (h.host.maintenance) return '#FBBF24';
  if (h.online === false) return '#F87171';
  if (h.online === null) return '#64748B';
  if (h.host.port_error) return '#FB923C'; // orange for port errors
  const health = hostHealth(h);
  if (health >= 0.5) return '#F87171';
  if (health >= 0.2) return '#FBBF24';
  return '#34D399';
}

/* ── Orbit radius: healthy=close, offline=far ── */
// Earth visual radius = 0.7
// Inner orbit starts at 1.2 (just outside earth)
// Healthy online: 1.2-2.0
// Maintenance: 2.5-3.0
// Offline: 4.0-5.5

function orbitRadius(h: HostStat, index: number): number {
  if (h.online === false) {
    // Offline: far ring with some spread
    return 4.5 + (index % 5) * 0.25;
  }
  if (h.host.maintenance) {
    // Maintenance: closer mid-range ring
    return 2.2 + (index % 4) * 0.2;
  }
  // Online hosts: spread across inner rings based on health
  const health = hostHealth(h);
  // health 0 → 1.4-1.9, health 0.3 → 2.3-2.8
  const base = 1.4 + health * 3.0;
  // More spread so healthy hosts don't cluster at same radius
  const spread = ((index * 7) % 13) / 13 * 0.5;
  return base + spread;
}

/* ── Earth component — photorealistic with textures ── */

function Earth() {
  const groupRef = useRef<THREE.Group>(null);
  const [dayMap, bumpMap] = useTexture([
    '/textures/earth-day.jpg',
    '/textures/earth-topology.png',
  ]);

  useFrame((_state, delta) => {
    if (groupRef.current) groupRef.current.rotation.y += delta * 0.06;
  });

  return (
    <group ref={groupRef}>
      {/* Main planet with day texture + bump map */}
      <mesh>
        <sphereGeometry args={[0.7, 64, 64]} />
        <meshStandardMaterial
          map={dayMap}
          bumpMap={bumpMap}
          bumpScale={0.03}
          roughness={0.7}
          metalness={0.05}
        />
      </mesh>
      {/* Atmosphere inner glow — Fresnel-like rim */}
      <mesh>
        <sphereGeometry args={[0.73, 48, 48]} />
        <meshBasicMaterial
          color="#60A5FA"
          transparent
          opacity={0.12}
          side={THREE.BackSide}
        />
      </mesh>
      {/* Atmosphere outer halo */}
      <mesh>
        <sphereGeometry args={[0.82, 32, 32]} />
        <meshBasicMaterial
          color="#38BDF8"
          transparent
          opacity={0.07}
          side={THREE.BackSide}
        />
      </mesh>
      {/* Soft blue glow */}
      <mesh>
        <sphereGeometry args={[0.95, 32, 32]} />
        <meshBasicMaterial
          color="#93C5FD"
          transparent
          opacity={0.03}
          side={THREE.BackSide}
        />
      </mesh>
    </group>
  );
}

/* ── Orbit ring guides ── */

function OrbitRings({ radii }: { radii: number[] }) {
  return (
    <>
      {radii.map((r, i) => (
        <mesh key={i} rotation={[Math.PI / 2, 0, 0]}>
          <ringGeometry args={[r - 0.004, r + 0.004, 160]} />
          <meshBasicMaterial
            color="#ffffff"
            transparent
            opacity={0.06}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
    </>
  );
}

/* ── Host node in 3D ── */

interface HostNodeProps {
  host: HostStat;
  radius: number;
  angle: number;
  inclination: number;
  speed: number;
}

function HostNode({ host, radius, angle, inclination, speed }: HostNodeProps) {
  const groupRef = useRef<THREE.Group>(null);
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const router = useRouter();
  const isOffline = host.online === false && !host.host.maintenance;
  const color = hostColor(host);
  const startAngle = useRef(angle);

  useFrame(({ clock }) => {
    if (groupRef.current) {
      const t = startAngle.current + clock.getElapsedTime() * speed;
      const x = Math.cos(t) * radius;
      const z = Math.sin(t) * radius;
      // Flat orbital plane — hosts hover on same level
      const y = Math.sin(t + inclination) * radius * 0.03;
      groupRef.current.position.set(x, y, z);
    }
    if (meshRef.current && isOffline) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 2.5) * 0.3;
      meshRef.current.scale.setScalar(s);
    }
  });

  const handleClick = useCallback(() => {
    router.push(`/hosts/${host.host.id}`);
  }, [router, host.host.id]);

  // Node sizes — visible at distance
  const nodeSize = isOffline ? 0.09 : 0.07;

  return (
    <group ref={groupRef}>
      {/* Core sphere */}
      <mesh
        ref={meshRef}
        onClick={handleClick}
        onPointerOver={() => { setHovered(true); document.body.style.cursor = 'pointer'; }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = 'auto'; }}
      >
        <sphereGeometry args={[nodeSize, 16, 16]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={hovered ? 2.5 : 1.0}
          transparent
          opacity={0.95}
        />
      </mesh>
      {/* Glow shell — smaller, more subtle */}
      <mesh>
        <sphereGeometry args={[nodeSize * 2, 12, 12]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.25 : isOffline ? 0.1 : 0.04}
          depthWrite={false}
        />
      </mesh>
      {/* Tooltip on hover */}
      {hovered && (
        <Html distanceFactor={8} style={{ pointerEvents: 'none' }}>
          <div className="rounded-md border px-3 py-2 text-xs text-white whitespace-nowrap backdrop-blur-sm shadow-xl" style={{ background: 'var(--ng-surface)', borderColor: 'var(--ng-glass-border)' }}>
            <p className="font-medium text-slate-200">{host.host.name}</p>
            <p className="text-[10px] text-slate-500 font-mono">{host.host.hostname}</p>
            <div className="flex items-center gap-2 mt-1">
              <span className={`text-[10px] ${isOffline ? 'text-red-400' : host.host.maintenance ? 'text-amber-400' : 'text-emerald-400'}`}>
                {host.online === null ? 'Unknown' : host.online ? 'Online' : 'Offline'}
              </span>
              {host.latency != null && (
                <span className="text-[10px] font-mono text-slate-400">{host.latency.toFixed(0)}ms</span>
              )}
              {host.uptime_stats?.h24 != null && (
                <span className="text-[10px] font-mono text-slate-400">{host.uptime_stats.h24.toFixed(1)}%</span>
              )}
            </div>
          </div>
        </Html>
      )}
    </group>
  );
}

/* ── Star particles ── */

function Stars({ count = 400 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 10 + Math.random() * 5;
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    return geo;
  }, [count]);

  useFrame((_state, delta) => {
    if (ref.current) ref.current.rotation.y += delta * 0.005;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial size={0.025} color="#CBD5E1" transparent opacity={0.4} sizeAttenuation depthWrite={false} />
    </points>
  );
}

/* ── Scene ── */

function Scene({ hosts }: { hosts: HostStat[] }) {
  const hostOrbits = useMemo(() => {
    // Sort: healthy first, then maintenance, then offline
    const sorted = [...hosts].sort((a, b) => hostHealth(a) - hostHealth(b));
    const golden = Math.PI * (3 - Math.sqrt(5));

    return sorted.map((h, i) => {
      const r = orbitRadius(h, i);
      const angle = i * golden; // Golden angle for even distribution
      // Each host gets a unique orbit inclination
      // Stronger, more varied inclinations for 3D depth
      const inclination = ((i * 2.39996 + i * 0.7) % (Math.PI * 2));
      // Speed: outer orbits move slower (Kepler-like)
      const speed = 0.08 + (1 / (r * 0.6)) * 0.12;
      return { host: h, radius: r, angle, inclination, speed };
    });
  }, [hosts]);

  // Guide rings at key orbit distances
  const ringRadii = useMemo(() => [1.5, 2.2, 3.0, 4.0, 5.0], []);

  return (
    <>
      {/* Lighting — sunlight from right, subtle blue fill */}
      <ambientLight intensity={0.35} />
      <directionalLight position={[5, 3, 2]} intensity={1.4} color="#FFF5E6" />
      <pointLight position={[-4, -2, -4]} intensity={0.4} color="#60A5FA" />
      <hemisphereLight args={['#87CEEB', '#000814', 0.2]} />

      <Stars />
      <Earth />
      <OrbitRings radii={ringRadii} />

      {hostOrbits.map((o) => (
        <HostNode
          key={o.host.host.id}
          host={o.host}
          radius={o.radius}
          angle={o.angle}
          inclination={o.inclination}
          speed={o.speed}
        />
      ))}

      <OrbitControls
        enablePan={false}
        minDistance={4}
        maxDistance={14}
        autoRotate
        autoRotateSpeed={0.12}
        enableDamping
        dampingFactor={0.05}
        maxPolarAngle={Math.PI * 0.75}
        minPolarAngle={Math.PI * 0.2}
      />
    </>
  );
}

/* ── Mobile fallback ── */

function MobileGrid({ hosts }: { hosts: HostStat[] }) {
  return (
    <div className="grid grid-cols-4 gap-2 p-4">
      {hosts.map((h) => {
        const status = h.host.maintenance
          ? 'maintenance' as const
          : h.online === false
            ? 'offline' as const
            : h.online === true
              ? 'online' as const
              : 'unknown' as const;
        return (
          <Link
            key={h.host.id}
            href={`/hosts/${h.host.id}`}
            className="flex flex-col items-center gap-1 p-2 rounded-md hover:bg-white/5 transition-colors"
          >
            <StatusDot status={status} pulse={status === 'offline'} />
            <span className="text-[10px] text-slate-400 truncate max-w-full">
              {h.host.name}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

/* ── Main Widget ── */

export interface GravityWidgetProps {
  hosts: HostStat[];
}

export function GravityWidget({ hosts }: GravityWidgetProps) {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const onlineCount = hosts.filter((h) => h.online === true && !h.host.maintenance).length;
  const offlineCount = hosts.filter((h) => h.online === false && !h.host.maintenance).length;
  const maintCount = hosts.filter((h) => h.host.maintenance).length;

  return (
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 350 }}>
      {/* HUD overlay */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-3">
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="online" />
          <span className="text-emerald-400 font-medium">{onlineCount}</span>
        </span>
        <span className="flex items-center gap-1.5 text-xs">
          <StatusDot status="offline" />
          <span className="text-red-400 font-medium">{offlineCount}</span>
        </span>
        {maintCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs">
            <StatusDot status="maintenance" />
            <span className="text-amber-400 font-medium">{maintCount}</span>
          </span>
        )}
        <span className="text-xs text-slate-500">{hosts.length} total</span>
      </div>

      <div className="absolute top-3 right-3 z-10 flex gap-3 text-[10px] text-slate-500">
        <span>Close orbit = healthy</span>
        <span>Far orbit = degraded</span>
      </div>

      {isMobile ? (
        <MobileGrid hosts={hosts} />
      ) : (
        <div style={{ height: 350 }}>
          <Canvas
            camera={{ position: [0, 3, 8], fov: 45 }}
            style={{ background: 'transparent' }}
            gl={{ alpha: true, antialias: true }}
            dpr={[1, 2]}
          >
            <Scene hosts={hosts} />
          </Canvas>
        </div>
      )}
    </GlassCard>
  );
}
