'use client';

import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
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
  if (h.online === false) return '#EF4444';
  if (h.online === null) return '#64748B';
  if (h.host.port_error) return '#F97316';
  const health = hostHealth(h);
  if (health >= 0.5) return '#EF4444';
  if (health >= 0.2) return '#FBBF24';
  return '#10B981';
}

/* ── Orbit radius ── */

function orbitRadius(h: HostStat, index: number): number {
  if (h.online === false) {
    return 3.5 + (index % 5) * 0.15;
  }
  if (h.host.maintenance) {
    return 1.8 + (index % 4) * 0.15;
  }
  const health = hostHealth(h);
  const base = 1.4 + health * 2.5;
  const spread = ((index * 7) % 13) / 13 * 0.8 + ((index * 3) % 7) / 7 * 0.4;
  return base + spread;
}

/* ── Deep space background ── */

function SpaceBackground() {
  const { scene } = useThree();

  useMemo(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 2048;
    canvas.height = 2048;
    const ctx = canvas.getContext('2d')!;

    // Deep space gradient
    const grad = ctx.createRadialGradient(1024, 1024, 0, 1024, 1024, 1200);
    grad.addColorStop(0, '#0a0e1a');
    grad.addColorStop(0.3, '#060a14');
    grad.addColorStop(0.6, '#030510');
    grad.addColorStop(1, '#010208');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 2048, 2048);

    // Subtle nebula patches
    for (let n = 0; n < 4; n++) {
      const nx = 400 + Math.random() * 1200;
      const ny = 400 + Math.random() * 1200;
      const nr = 200 + Math.random() * 300;
      const colors = ['#1e3a5f', '#2d1b4e', '#1a3045', '#261840'];
      const ng = ctx.createRadialGradient(nx, ny, 0, nx, ny, nr);
      ng.addColorStop(0, colors[n] + '10');
      ng.addColorStop(0.5, colors[n] + '04');
      ng.addColorStop(1, 'transparent');
      ctx.fillStyle = ng;
      ctx.fillRect(0, 0, 2048, 2048);
    }

    // Stars — subtle, no bright glows
    for (let i = 0; i < 600; i++) {
      const sx = Math.random() * 2048;
      const sy = Math.random() * 2048;
      const brightness = Math.random();
      const size = brightness > 0.9 ? 1.2 : brightness > 0.7 ? 0.8 : 0.5;

      const r = 180 + Math.random() * 75;
      const g = 190 + Math.random() * 65;
      const b = 220 + Math.random() * 35;
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.2 + brightness * 0.5})`;
      ctx.beginPath();
      ctx.arc(sx, sy, size, 0, Math.PI * 2);
      ctx.fill();
    }

    const texture = new THREE.CanvasTexture(canvas);
    texture.mapping = THREE.EquirectangularReflectionMapping;
    scene.background = texture;

    return () => {
      scene.background = null;
      texture.dispose();
    };
  }, [scene]);

  return null;
}

/* ── Twinkling star particles (foreground depth) ── */

function Stars({ count = 150 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const { geometry } = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    const starSizes = new Float32Array(count);
    const colors = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 8 + Math.random() * 6;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);

      // Varying sizes
      starSizes[i] = 0.015 + Math.random() * 0.03;

      // Blue-white color range
      const warmth = Math.random();
      colors[i * 3] = 0.7 + warmth * 0.3;
      colors[i * 3 + 1] = 0.75 + warmth * 0.2;
      colors[i * 3 + 2] = 0.85 + warmth * 0.15;
    }

    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    return { geometry: geo, sizes: starSizes };
  }, [count]);

  useFrame((_state, delta) => {
    if (ref.current) ref.current.rotation.y += delta * 0.0015;
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        size={0.015}
        vertexColors
        transparent
        opacity={0.35}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

/* ── Earth ── */

function Earth() {
  const groupRef = useRef<THREE.Group>(null);
  const [dayMap, bumpMap] = useTexture([
    '/textures/earth-day.jpg',
    '/textures/earth-topology.png',
  ]);

  useFrame((_state, delta) => {
    if (groupRef.current) groupRef.current.rotation.y += delta * 0.03;
  });

  return (
    <group ref={groupRef}>
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
      {/* Atmosphere layers */}
      <mesh>
        <sphereGeometry args={[0.73, 48, 48]} />
        <meshBasicMaterial color="#60A5FA" transparent opacity={0.12} side={THREE.BackSide} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.82, 32, 32]} />
        <meshBasicMaterial color="#38BDF8" transparent opacity={0.07} side={THREE.BackSide} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.95, 32, 32]} />
        <meshBasicMaterial color="#93C5FD" transparent opacity={0.02} side={THREE.BackSide} />
      </mesh>
      {/* 4th atmosphere — outermost soft bloom */}
      <mesh>
        <sphereGeometry args={[1.05, 24, 24]} />
        <meshBasicMaterial color="#60A5FA" transparent opacity={0.01} side={THREE.BackSide} />
      </mesh>
    </group>
  );
}

/* ── Host node ── */

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
  const health = hostHealth(host);
  const healthPct = Math.round((1 - health) * 100);
  const startAngle = useRef(angle);

  useFrame(({ clock }) => {
    if (groupRef.current) {
      const t = startAngle.current + clock.getElapsedTime() * speed;
      const x = Math.cos(t) * radius;
      const z = Math.sin(t) * radius;
      const y = Math.sin(t + inclination) * radius * 0.03;
      groupRef.current.position.set(x, y, z);
    }
    // Offline pulse
    if (meshRef.current && isOffline) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 2.5) * 0.25;
      meshRef.current.scale.setScalar(s);
    }
  });

  const handleClick = useCallback(() => {
    router.push(`/hosts/${host.host.id}`);
  }, [router, host.host.id]);

  const nodeSize = isOffline ? 0.12 : 0.10;

  return (
    <group ref={groupRef}>
      {/* Core — bright, sharp */}
      <mesh
        ref={meshRef}
        onClick={handleClick}
        onPointerOver={() => { setHovered(true); document.body.style.cursor = 'pointer'; }}
        onPointerOut={() => { setHovered(false); document.body.style.cursor = 'auto'; }}
      >
        <sphereGeometry args={[nodeSize, 20, 20]} />
        <meshStandardMaterial
          color="#ffffff"
          emissive={color}
          emissiveIntensity={hovered ? 4 : 2}
          toneMapped={false}
        />
      </mesh>

      {/* Inner glow — colored halo */}
      <mesh>
        <sphereGeometry args={[nodeSize * 2, 16, 16]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.3 : isOffline ? 0.12 : 0.06}
          depthWrite={false}
        />
      </mesh>

      {/* Outer bloom — subtle */}
      <mesh>
        <sphereGeometry args={[nodeSize * 3, 12, 12]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.1 : 0.02}
          depthWrite={false}
        />
      </mesh>

      {/* Tooltip */}
      {hovered && (
        <Html distanceFactor={8} style={{ pointerEvents: 'none' }}>
          <div className="rounded-xl px-3 py-2.5 text-xs text-slate-100 whitespace-nowrap shadow-xl" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
            <p className="font-medium text-slate-200">{host.host.name}</p>
            <p className="text-[10px] text-slate-500 font-mono">{host.host.hostname}</p>
            <div className="flex items-center gap-2 mt-1">
              <span className={`text-[10px] ${isOffline ? 'text-red-400' : host.host.maintenance ? 'text-amber-400' : host.online === null ? 'text-slate-400' : 'text-emerald-400'}`}>
                {host.online === null ? 'Not observed' : host.online ? 'Online' : 'Offline'}
              </span>
              {host.latency != null && (
                <span className="text-[10px] font-mono text-slate-400">{host.latency.toFixed(0)}ms</span>
              )}
              {host.uptime_stats?.h24 != null && (
                <span className="text-[10px] font-mono text-slate-400">{host.uptime_stats.h24.toFixed(1)}%</span>
              )}
              <span className={`text-[10px] font-mono ${healthPct >= 80 ? 'text-emerald-400' : healthPct >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
                Health {healthPct}%
              </span>
            </div>
          </div>
        </Html>
      )}
    </group>
  );
}

/* ── Scene ── */

function Scene({ hosts }: { hosts: HostStat[] }) {
  const hostOrbits = useMemo(() => {
    const sorted = [...hosts].sort((a, b) => hostHealth(a) - hostHealth(b));
    const golden = Math.PI * (3 - Math.sqrt(5));

    return sorted.map((h, i) => {
      const r = orbitRadius(h, i);
      const a = i * golden;
      const inclination = ((i * 2.39996 + i * 0.7) % (Math.PI * 2));
      const speed = 0.04 + (1 / (r * 0.6)) * 0.06;
      return { host: h, radius: r, angle: a, inclination, speed };
    });
  }, [hosts]);

  return (
    <>
      <SpaceBackground />

      {/* Lighting */}
      <ambientLight intensity={0.25} />
      <directionalLight position={[5, 3, 2]} intensity={1.6} color="#FFF5E6" />
      <pointLight position={[-4, -2, -4]} intensity={0.3} color="#60A5FA" />
      <hemisphereLight args={['#1a2a4a', '#000510', 0.15]} />

      <Stars />
      <Earth />

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
        autoRotateSpeed={0.06}
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
  const unknownCount = hosts.filter((h) => h.online === null && !h.host.maintenance).length;
  const maintCount = hosts.filter((h) => h.host.maintenance).length;

  return (
    <GlassCard className="relative overflow-hidden" style={{ minHeight: isMobile ? 200 : 380 }}>
      {/* HUD overlay — Bento pill badges */}
      <div className="absolute top-4 left-4 z-10 flex items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
          <StatusDot status="online" />
          <span className="text-emerald-400 font-medium">{onlineCount}</span>
        </span>
        <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
          <StatusDot status="offline" />
          <span className="text-red-400 font-medium">{offlineCount}</span>
        </span>
        {maintCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>
            <StatusDot status="maintenance" />
            <span className="text-amber-400 font-medium">{maintCount}</span>
          </span>
        )}
        {unknownCount > 0 && (
          <span className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }} title="Not observed — nobody is currently checking these hosts">
            <StatusDot status="unknown" />
            <span className="text-slate-400 font-medium">{unknownCount}</span>
          </span>
        )}
        <span className="text-xs text-slate-500 px-2">{hosts.length} total</span>
      </div>

      <div className="absolute top-4 right-4 z-10 flex gap-2">
        <span className="text-[10px] text-slate-500 px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>Close orbit = healthy</span>
        <span className="text-[10px] text-slate-500 px-2.5 py-1 rounded-full" style={{ background: 'var(--ng-card-bg)', border: '1.5px solid var(--ng-card-border)' }}>Far orbit = degraded</span>
      </div>

      {isMobile ? (
        <MobileGrid hosts={hosts} />
      ) : (
        <div style={{ height: 380 }}>
          <Canvas
            camera={{ position: [0, 3, 8], fov: 45 }}
            gl={{ antialias: true }}
            dpr={[1, 2]}
          >
            <Scene hosts={hosts} />
          </Canvas>
        </div>
      )}
    </GlassCard>
  );
}
