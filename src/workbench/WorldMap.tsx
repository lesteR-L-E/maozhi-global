import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import land from './land.json';
import { Market, score } from './data';
import { CENTROIDS } from './countries';

// Map, pins and labels share one equirectangular projection (viewBox 900x460).
const project = (lon: number, lat: number): [number, number] => [(lon + 180) * 2.5, (85 - lat) * 2.5];

// Hand-tuned label spots keep the dense Southeast Asian cluster readable;
// every other market's label is placed from its centroid and de-collided below.
const LABEL_OVERRIDES: Record<string, [number, number]> = {
  VNM: [808, 148], THA: [656, 150], PHL: [847, 203], MYS: [655, 223], IDN: [778, 276],
};
const LABEL_MIN_DISTANCE = 42; // pill diameter (36) plus breathing room
const LABEL_PADDING = 26;
const FOCUSED_PAN = { x: 0, y: 34 };
const PAN_LIMITS = { x: 270, y: 180 };
const FOCUSED_LABEL_WIDTH = 126;
const FOCUSED_LABEL_HEIGHT = 36;
const FOCUSED_LABEL_GAP_X = 12;
const FOCUSED_LABEL_GAP_Y = 10;

// Preferred label spot is northeast of the pin; relaxation pushes overlapping
// pills apart and springs them back, so clusters fan out instead of stacking.
// Fully deterministic — the same markets always produce the same layout.
function placeLabels(points: { id: string; gx: number; gy: number }[]) {
  const items = points.map(p => {
    const fixed = LABEL_OVERRIDES[p.id];
    const [lx, ly] = fixed ?? [p.gx + 30, p.gy - 22];
    return { id: p.id, lx, ly, px: lx, py: ly, fixed: !!fixed };
  });
  for (let iter = 0; iter < 80; iter++) {
    let moved = false;
    for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
      const a = items[i], b = items[j];
      if (a.fixed && b.fixed) continue;
      let dx = b.lx - a.lx, dy = b.ly - a.ly, dist = Math.hypot(dx, dy);
      if (dist >= LABEL_MIN_DISTANCE) continue;
      if (dist < 0.01) { dx = 0; dy = -1; dist = 1; }
      const push = (LABEL_MIN_DISTANCE - dist) / 2, ux = dx / dist, uy = dy / dist;
      if (!a.fixed) { a.lx -= ux * push; a.ly -= uy * push; }
      if (!b.fixed) { b.lx += ux * push; b.ly += uy * push; }
      moved = true;
    }
    for (const it of items) {
      if (!it.fixed) { it.lx += (it.px - it.lx) * 0.04; it.ly += (it.py - it.ly) * 0.04; }
      it.lx = Math.max(LABEL_PADDING, Math.min(900 - LABEL_PADDING, it.lx));
      it.ly = Math.max(LABEL_PADDING, Math.min(460 - LABEL_PADDING, it.ly));
    }
    if (!moved) break;
  }
  return new Map(items.map(it => [it.id, [it.lx, it.ly] as [number, number]]));
}

// Focused labels are full-width pills, so point-distance collision is not
// sufficient. Relax their actual rectangles and keep every pill in view.
function placeFocusedLabels(
  points: { id: string; gx: number; gy: number }[],
  baseLabels: Map<string, [number, number]>,
) {
  const minX = 20;
  const maxX = 900 - FOCUSED_LABEL_WIDTH - 2;
  const minY = FOCUSED_LABEL_HEIGHT / 2 + 4;
  const maxY = 460 - FOCUSED_LABEL_HEIGHT / 2 - 4;
  const items = points.map((point, index) => {
    const [lx, ly] = baseLabels.get(point.id)!;
    const preferredX = (lx - 650) * 1.7 + 140;
    const preferredY = (ly - 140) * 1.9 + 70;
    return {
      id: point.id,
      index,
      x: Math.max(minX, Math.min(maxX, preferredX)),
      y: Math.max(minY, Math.min(maxY, preferredY)),
      preferredX,
      preferredY,
    };
  });

  for (let iteration = 0; iteration < 220; iteration++) {
    for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
      const a = items[i], b = items[j];
      const centerAX = a.x + FOCUSED_LABEL_WIDTH / 2;
      const centerBX = b.x + FOCUSED_LABEL_WIDTH / 2;
      const dx = centerBX - centerAX;
      const dy = b.y - a.y;
      const overlapX = FOCUSED_LABEL_WIDTH + FOCUSED_LABEL_GAP_X - Math.abs(dx);
      const overlapY = FOCUSED_LABEL_HEIGHT + FOCUSED_LABEL_GAP_Y - Math.abs(dy);
      if (overlapX <= 0 || overlapY <= 0) continue;

      if (overlapY <= overlapX) {
        const direction = Math.abs(dy) > .01 ? Math.sign(dy) : ((a.index + b.index) % 2 ? 1 : -1);
        const push = overlapY / 2 + .25;
        a.y -= direction * push;
        b.y += direction * push;
      } else {
        const direction = Math.abs(dx) > .01 ? Math.sign(dx) : ((a.index + b.index) % 2 ? 1 : -1);
        const push = overlapX / 2 + .25;
        a.x -= direction * push;
        b.x += direction * push;
      }
    }

    for (const item of items) {
      if (iteration < 130) {
        item.x += (item.preferredX - item.x) * .012;
        item.y += (item.preferredY - item.y) * .012;
      }
      item.x = Math.max(minX, Math.min(maxX, item.x));
      item.y = Math.max(minY, Math.min(maxY, item.y));
    }
  }

  return new Map(items.map(item => [item.id, [item.x, item.y] as [number, number]]));
}

export function WorldMap({ markets, selected, focused, onSelect }: { markets: Market[]; selected: string; focused: boolean; onSelect: (id: string, anchor: { x: number; y: number }) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; panX: number; panY: number } | null>(null);
  const draggedRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  const [pan, setPan] = useState(FOCUSED_PAN);

  useEffect(() => {
    setPan(focused ? FOCUSED_PAN : { x: 0, y: 0 });
    dragRef.current = null;
    draggedRef.current = false;
    setDragging(false);
  }, [focused, markets]);

  const pins = useMemo(() => {
    const points = markets.flatMap(m => {
      const centroid = CENTROIDS[m.market];
      if (!centroid) return [];
      const [gx, gy] = project(centroid[0], centroid[1]);
      return [{ market: m, id: m.market, gx, gy }];
    });
    const labels = placeLabels(points);
    const focusedLabels = placeFocusedLabels(points, labels);
    return points.map(p => ({ ...p, label: labels.get(p.id)!, focusedLabel: focusedLabels.get(p.id)! }));
  }, [markets]);

  const startDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!focused || event.button !== 0) return;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
    draggedRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };
  const moveDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    const svg = svgRef.current;
    if (!focused || !drag || drag.pointerId !== event.pointerId || !svg) return;
    const bounds = svg.getBoundingClientRect();
    const dx = (event.clientX - drag.x) * 900 / Math.max(bounds.width, 1);
    const dy = (event.clientY - drag.y) * 460 / Math.max(bounds.height, 1);
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 4) draggedRef.current = true;
    setPan({
      x: Math.max(-PAN_LIMITS.x, Math.min(PAN_LIMITS.x, drag.panX + dx)),
      y: Math.max(-PAN_LIMITS.y, Math.min(PAN_LIMITS.y, drag.panY + dy)),
    });
  };
  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    setDragging(false);
    window.setTimeout(() => { draggedRef.current = false; }, 0);
  };
  const selectMarket = (id: string, anchor: { x: number; y: number }) => {
    if (draggedRef.current) return;
    onSelect(id, anchor);
  };

  return <>
  <svg ref={svgRef} className={`wb-world ${focused ? 'is-focused' : ''} ${dragging ? 'is-dragging' : ''}`} viewBox="0 0 900 460" role="group" aria-label="世界市场示意地图，使用排名标记选择市场" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
    <defs><linearGradient id="land-fill" x1="0" y1="0" x2=".4" y2="1"><stop stopColor="#d9e7ee"/><stop offset="1" stopColor="#b9d1df"/></linearGradient><pattern id="sea-grid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M50 0H0V50" fill="none" stroke="#9fbad0" strokeWidth=".5" opacity=".2"/></pattern><filter id="land-shadow" x="-10%" y="-10%" width="120%" height="125%"><feDropShadow dx="0" dy="3" stdDeviation="2" floodColor="#8eafc7" floodOpacity=".18"/></filter></defs>
    <rect width="900" height="460" fill="url(#sea-grid)"/>
    <g className="wb-map-pan" transform={`translate(${focused ? pan.x : 0} ${focused ? pan.y : 0})`}>
    <g className="wb-geography" style={{transform:focused ? 'translate(-1315px, -190px) scale(2.4)' : 'translate(0px, 0px) scale(1)'}}>
      <g fill="url(#land-fill)" stroke="#f2f7fa" strokeWidth=".75" strokeLinejoin="round" filter="url(#land-shadow)">{land.map((d,i)=><path key={i} d={d}/>)}</g>
      <g className="wb-ocean-labels"><text x="110" y="265">太 平 洋</text><text x="367" y="235">大 西 洋</text><text x="607" y="303">印 度 洋</text><text x="800" y="270">太 平 洋</text></g>
    </g>
    {!focused && <g className="wb-continent-labels"><text x="213" y="123">北美洲</text><text x="301" y="302">南美洲</text><text x="485" y="218">非洲</text><text x="496" y="81">欧洲</text><text x="682" y="110">亚洲</text><text x="790" y="307">大洋洲</text></g>}
    {pins.map(({market:m,gx,gy,label,focusedLabel})=>{const x=focused?gx*2.4-1315:gx,y=focused?gy*2.4-190:gy;const [lx,ly]=label;const [labelX,labelY]=focused?focusedLabel:[lx,ly];return <g key={m.market} data-market={m.market} className={`wb-pin ${selected===m.market?'selected':''}`} role="button" tabIndex={0} aria-label={`${m.name_cn}，排名第${m.rank}，${score(m.total_score)}分`} onClick={()=>selectMarket(m.market,{x:labelX,y:labelY})} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(m.market,{x:labelX,y:labelY});}}}>
      <title>{m.name_cn} · 第 {m.rank} 名 · {score(m.total_score)} 分</title><line x1={x} y1={y} x2={labelX} y2={labelY} stroke="#7ca5c1" strokeWidth="1"/><circle cx={x} cy={y} r="4" fill="#5c96bc" stroke="white" strokeWidth="2"/>
      <g transform={`translate(${labelX},${labelY})`}><rect x="-18" y="-18" width={focused?126:35} height="36" rx="18" className="wb-pin-bg"/><circle r="14" className={`wb-medal medal-${m.rank <= 3 ? m.rank : 'other'}`}/><text y="5" textAnchor="middle" className="wb-pin-rank">{m.rank}</text>{focused&&<text x="23" y="5" className="wb-pin-name">{m.name_cn}</text>}</g>
    </g>;})}
    </g>
    {!focused && <g className="wb-compass" transform="translate(60 359)"><path d="M0 -20L5 0L0 13L-5 0Z" fill="#a6bdcd"/><text y="-28" textAnchor="middle">N</text></g>}
  </svg>
  {focused&&<button type="button" className="wb-map-reset" aria-label="重置地图位置" title="重置地图位置" onClick={()=>setPan(FOCUSED_PAN)}>↺</button>}
  </>;
}
