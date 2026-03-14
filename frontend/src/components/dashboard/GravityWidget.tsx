'use client';

import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import { useRouter } from 'next/navigation';
import * as THREE from 'three';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatusDot } from '@/components/ui/StatusDot';
import Link from 'next/link';
import type { HostStat } from '@/hooks/queries/useDashboard';

/* ── Health scoring ── */

function hostHealth(h: HostStat): number {
  if (h.host.maintenance) return 0.5;
  if (h.online === false) return 1.0;
  if (h.online === null) return 0.8;
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
  const health = hostHealth(h);
  if (health >= 0.5) return '#F87171';
  if (health >= 0.2) return '#FBBF24';
  return '#34D399';
}

/* ── Orbit radius from health: healthy=close, offline=far ── */

function orbitRadius(h: HostStat): number {
  const health = hostHealth(h);
  // Earth radius is 1.0
  // Healthy (0) → 1.6, degraded → further, offline (1.0) → 4.5
  if (h.online === false) return 4.0 + Math.random() * 1.0;
  return 1.6 + health * 2.8;
}

/* ── Earth component ── */

function Earth() {
  const meshRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Mesh>(null);

  useFrame((_state, delta) => {
    if (meshRef.current) meshRef.current.rotation.y += delta * 0.05;
  });

  // Procedural earth-like material
  const earthMaterial = useMemo(() => {
    return new THREE.MeshStandardMaterial({
      color: new THREE.Color('#1a4a7a'),
      emissive: new THREE.Color('#0a2a4a'),
      emissiveIntensity: 0.3,
      roughness: 0.8,
      metalness: 0.1,
    });
  }, []);

  return (
    <group>
      {/* Earth sphere */}
      <mesh ref={meshRef} material={earthMaterial}>
        <sphereGeometry args={[1, 48, 48]} />
      </mesh>
      {/* Atmosphere glow */}
      <mesh ref={glowRef}>
        <sphereGeometry args={[1.08, 48, 48]} />
        <meshBasicMaterial
          color="#38BDF8"
          transparent
          opacity={0.08}
          side={THREE.BackSide}
        />
      </mesh>
      {/* Outer atmosphere halo */}
      <mesh>
        <sphereGeometry args={[1.2, 32, 32]} />
        <meshBasicMaterial
          color="#0ea5e9"
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
          <ringGeometry args={[r - 0.005, r + 0.005, 128]} />
          <meshBasicMaterial
            color="#ffffff"
            transparent
            opacity={0.04}
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
  tilt: number;
  speed: number;
}

function HostNode({ host, radius, angle, tilt, speed }: HostNodeProps) {
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
      const y = Math.sin(t * 0.7) * tilt;
      groupRef.current.position.set(x, y, z);
    }
    if (meshRef.current && isOffline) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 3) * 0.25;
      meshRef.current.scale.setScalar(s);
    }
  });

  const handleClick = useCallback(() => {
    router.push(`/hosts/${host.host.id}`);
  }, [router, host.host.id]);

  const nodeSize = isOffline ? 0.12 : 0.1;

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
          emissiveIntensity={hovered ? 2.0 : 0.8}
          transparent
          opacity={0.95}
        />
      </mesh>
      {/* Glow shell */}
      <mesh>
        <sphereGeometry args={[nodeSize * 2.5, 12, 12]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.2 : isOffline ? 0.12 : 0.06}
          depthWrite={false}
        />
      </mesh>
      {/* Tooltip on hover */}
      {hovered && (
        <Html distanceFactor={6} style={{ pointerEvents: 'none' }}>
          <div className="rounded-md bg-[#0B0E14]/95 border border-white/[0.08] px-3 py-2 text-xs text-white whitespace-nowrap backdrop-blur-sm shadow-xl">
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

function Stars({ count = 300 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 8 + Math.random() * 4;
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    return geo;
  }, [count]);

  useFrame((_state, delta) => {
    if (ref.current) ref.current.rotation.y += delta * 0.008;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial size={0.03} color="#94A3B8" transparent opacity={0.5} sizeAttenuation depthWrite={false} />
    </points>
  );
}

/* ── Scene ── */

interface SceneProps {
  hosts: HostStat[];
}

function Scene({ hosts }: SceneProps) {
  // Compute orbital params per host
  const hostOrbits = useMemo(() => {
    const sorted = [...hosts].sort((a, b) => hostHealth(a) - hostHealth(b));
    const golden = Math.PI * (3 - Math.sqrt(5));
    return sorted.map((h, i) => {
      const r = orbitRadius(h);
      const angle = i * golden;
      const tilt = 0.15 + Math.random() * 0.4;
      // Slower orbit for far-away hosts
      const speed = 0.15 + (1 - hostHealth(h)) * 0.2;
      return { host: h, radius: r, angle, tilt, speed };
    });
  }, [hosts]);

  // Unique orbit ring radii (rounded to avoid too many rings)
  const ringRadii = useMemo(() => {
    const set = new Set<number>();
    set.add(1.6);
    set.add(2.5);
    set.add(3.5);
    set.add(4.5);
    return Array.from(set);
  }, []);

  return (
    <>
      <ambientLight intensity={0.25} />
      <pointLight position={[5, 3, 5]} intensity={0.9} color="#ffffff" />
      <pointLight position={[-3, -2, -4]} intensity={0.2} color="#38BDF8" />

      <Stars />
      <Earth />
      <OrbitRings radii={ringRadii} />

      {hostOrbits.map((o) => (
        <HostNode
          key={o.host.host.id}
          host={o.host}
          radius={o.radius}
          angle={o.angle}
          tilt={o.tilt}
          speed={o.speed}
        />
      ))}

      <OrbitControls
        enablePan={false}
        minDistance={3}
        maxDistance={12}
        autoRotate
        autoRotateSpeed={0.15}
        enableDamping
        dampingFactor={0.05}
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
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 420 }}>
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
        <div style={{ height: 420 }}>
          <Canvas
            camera={{ position: [0, 2.5, 6], fov: 50 }}
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
