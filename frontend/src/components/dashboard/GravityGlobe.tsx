'use client';

import { useRef, useMemo, useState, useCallback } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html } from '@react-three/drei';
import { useRouter } from 'next/navigation';
import * as THREE from 'three';
import type { HostStat } from '@/hooks/queries/useDashboard';

/* ── Layout helpers ── */

function spiralPosition(index: number, total: number): [number, number, number] {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const angle = index * goldenAngle;
  const radius = 0.6 + Math.sqrt(index / Math.max(total, 1)) * 2.8;
  const y = ((index / Math.max(total - 1, 1)) - 0.5) * 2.5;
  return [Math.cos(angle) * radius, y, Math.sin(angle) * radius];
}

function hostColor(host: HostStat): string {
  if (host.host.maintenance) return '#FBBF24';
  if (host.online === false) return '#F87171';
  if (host.host.port_error) return '#FB923C';
  return '#34D399';
}

function hostRadius(host: HostStat): number {
  return host.host.maintenance ? 0.12 : 0.15;
}

/* ── Particle system ── */

function Particles({ count = 200 }: { count?: number }) {
  const ref = useRef<THREE.Points>(null);
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = 3.5 + Math.random() * 1.5;
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
    return geo;
  }, [count]);

  useFrame((_state, delta) => {
    if (ref.current) {
      ref.current.rotation.y += delta * 0.04;
      ref.current.rotation.x += delta * 0.01;
    }
  });

  return (
    <points ref={ref} geometry={geometry}>
      <pointsMaterial
        size={0.03}
        color="#38BDF8"
        transparent
        opacity={0.3}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

/* ── Connection lines ── */

function ConnectionLines({ positions }: { positions: [number, number, number][] }) {
  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const verts: number[] = [];
    const maxDist = 2.5;
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const dx = positions[i][0] - positions[j][0];
        const dy = positions[i][1] - positions[j][1];
        const dz = positions[i][2] - positions[j][2];
        if (Math.sqrt(dx * dx + dy * dy + dz * dz) < maxDist) {
          verts.push(...positions[i], ...positions[j]);
        }
      }
    }
    geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
    return geo;
  }, [positions]);

  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial color="#ffffff" transparent opacity={0.1} />
    </lineSegments>
  );
}

/* ── Host node ── */

interface HostNodeProps {
  host: HostStat;
  position: [number, number, number];
}

function HostNode({ host, position }: HostNodeProps) {
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const router = useRouter();
  const isOffline = host.online === false && !host.host.maintenance;
  const color = hostColor(host);
  const radius = hostRadius(host);

  useFrame(({ clock }) => {
    if (meshRef.current && isOffline) {
      const s = 1 + Math.sin(clock.getElapsedTime() * 3) * 0.15;
      meshRef.current.scale.setScalar(s);
    }
  });

  const handleClick = useCallback(() => {
    router.push(`/hosts/${host.host.id}`);
  }, [router, host.host.id]);

  return (
    <group position={position}>
      <mesh
        ref={meshRef}
        onClick={handleClick}
        onPointerOver={() => setHovered(true)}
        onPointerOut={() => setHovered(false)}
      >
        <sphereGeometry args={[radius, 24, 24]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={hovered ? 1.5 : 0.6}
          transparent
          opacity={0.9}
        />
      </mesh>
      {/* outer glow */}
      <mesh>
        <sphereGeometry args={[radius * 1.6, 16, 16]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={hovered ? 0.2 : 0.08}
          depthWrite={false}
        />
      </mesh>
      {hovered && (
        <Html distanceFactor={6} style={{ pointerEvents: 'none' }}>
          <div className="rounded-md bg-slate-900/90 border border-white/10 px-3 py-1.5 text-xs text-white whitespace-nowrap backdrop-blur-sm shadow-lg">
            <span className="font-medium">{host.host.name}</span>
            {host.latency !== null && (
              <span className="ml-2 text-slate-400">{Math.round(host.latency)}ms</span>
            )}
            {host.online === false && (
              <span className="ml-2 text-red-400">offline</span>
            )}
            {host.online !== false && host.host.port_error && (
              <span className="ml-2 text-orange-400">port error</span>
            )}
            {host.host.maintenance && (
              <span className="ml-2 text-amber-400">maintenance</span>
            )}
          </div>
        </Html>
      )}
    </group>
  );
}

/* ── Scene ── */

interface SceneProps {
  hosts: HostStat[];
}

function Scene({ hosts }: SceneProps) {
  const positions = useMemo(
    () => hosts.map((_, i) => spiralPosition(i, hosts.length)),
    [hosts],
  );

  return (
    <>
      <ambientLight intensity={0.3} />
      <pointLight position={[5, 5, 5]} intensity={0.8} />
      <pointLight position={[-5, -3, -5]} intensity={0.3} color="#38BDF8" />
      <Particles />
      <ConnectionLines positions={positions} />
      {hosts.map((host, i) => (
        <HostNode key={host.host.id} host={host} position={positions[i]} />
      ))}
      <OrbitControls
        enablePan={false}
        minDistance={3}
        maxDistance={12}
        autoRotate
        autoRotateSpeed={0.3}
      />
    </>
  );
}

/* ── Public component ── */

export interface GravityGlobeProps {
  hosts: HostStat[];
  cameraPosition?: [number, number, number];
}

export function GravityGlobe({ hosts, cameraPosition = [0, 2, 7] }: GravityGlobeProps) {
  return (
    <Canvas
      camera={{ position: cameraPosition, fov: 50 }}
      style={{ background: 'transparent' }}
      gl={{ alpha: true, antialias: true }}
      dpr={[1, 2]}
    >
      <Scene hosts={hosts} />
    </Canvas>
  );
}
