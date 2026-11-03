import {
  Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { GraphNode, MoneyGraph } from "../lib/api.ts";
import { inr, inrCompact, shortId } from "../lib/format.ts";

const COL = 220;
const ROW = 64;

type AccountData = { label: string; kind: "root" | "case" | "system" | "plain" };
type AccountNodeT = Node<AccountData, "account">;

const KIND_CLASS: Record<AccountData["kind"], string> = {
  root: "border-indigo-600 bg-indigo-600 text-white",
  case: "border-indigo-400 bg-indigo-50 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-100",
  system: "border-dashed border-zinc-400 bg-transparent text-zinc-500",
  plain: "border-zinc-300 bg-white text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100",
};

/**
 * Account node with two sets of handles: left/right for forward flow, bottom for edges that point
 * back to the left (cycles), so a round trip's return leg routes underneath instead of overlapping.
 */
function AccountNode({ data }: NodeProps<AccountNodeT>) {
  const hidden = "!h-1.5 !w-1.5 !min-w-0 !border-0 !bg-transparent";
  return (
    <div className={`min-w-24 rounded-lg border px-2 py-1 text-center font-mono text-xs ${KIND_CLASS[data.kind]}`}>
      <Handle type="target" position={Position.Left} id="in" className={hidden} />
      <Handle type="source" position={Position.Right} id="out" className={hidden} />
      <Handle type="target" position={Position.Bottom} id="back-in" className={hidden} style={{ left: "30%" }} />
      <Handle type="source" position={Position.Bottom} id="back-out" className={hidden} style={{ left: "70%" }} />
      {data.label}
    </div>
  );
}

const nodeTypes = { account: AccountNode };

function kind(n: GraphNode): AccountData["kind"] {
  if (n.is_root) return "root";
  if (n.is_system) return "system";
  return n.has_open_case ? "case" : "plain";
}

/**
 * Layout: the flagged account in the middle; accounts that mostly receive from closer hops go right,
 * those that mostly send toward them go left; columns by hop, staggered vertically. Deterministic.
 */
function layout(g: MoneyGraph): { nodes: AccountNodeT[]; edges: Edge[] } {
  const hop = new Map(g.nodes.map((n) => [n.id, n.hop]));
  const net = new Map<string, number>();
  for (const e of g.edges) {
    const hs = hop.get(e.source) ?? 0;
    const ht = hop.get(e.target) ?? 0;
    if (ht > hs) net.set(e.target, (net.get(e.target) ?? 0) + 1);
    if (hs > ht) net.set(e.source, (net.get(e.source) ?? 0) - 1);
  }
  const columns = new Map<number, string[]>();
  for (const n of g.nodes) {
    const side = n.is_root ? 0 : (net.get(n.id) ?? 0) >= 0 ? 1 : -1;
    const col = side * n.hop;
    columns.set(col, [...(columns.get(col) ?? []), n.id]);
  }
  const pos = new Map<string, { x: number; y: number }>();
  for (const [col, ids] of columns) {
    ids.sort();
    const stagger = col === 0 ? 0 : (Math.abs(col) % 2 === 1 ? 1 : -1) * ROW * 0.9;
    ids.forEach((id, i) => pos.set(id, { x: col * COL, y: stagger + (i - (ids.length - 1) / 2) * ROW }));
  }

  const nodes: AccountNodeT[] = g.nodes.map((n) => ({
    id: n.id,
    type: "account",
    position: pos.get(n.id) ?? { x: 0, y: 0 },
    data: { label: n.is_system ? "Funding" : shortId(n.id), kind: kind(n) },
    style: { cursor: n.is_system ? "default" : "pointer" },
  }));

  const max = Math.max(1, ...g.edges.map((e) => e.amount_minor));
  const edges: Edge[] = g.edges.map((e) => {
    const w = 1 + 5 * (Math.log10(e.amount_minor + 1) / Math.log10(max + 1));
    const color = e.type === "DEPOSIT" ? "#a1a1aa" : "#6366f1";
    const back = (pos.get(e.source)?.x ?? 0) > (pos.get(e.target)?.x ?? 0);
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: back ? "back-out" : "out",
      targetHandle: back ? "back-in" : "in",
      type: "smoothstep",
      pathOptions: { borderRadius: 12, offset: back ? 28 : 20 },
      label: inrCompact(e.amount_minor),
      ariaLabel: `${inr(e.amount_minor)} from ${shortId(e.source)} to ${shortId(e.target)}`,
      style: { strokeWidth: w, stroke: color, opacity: 0.85 },
      labelStyle: { fontSize: 10, fill: "var(--lg-label-fg)", fontWeight: 500 },
      labelBgStyle: { fill: "var(--lg-label-bg)" },
      labelShowBg: true,
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      // Marker size is in stroke-width units: keep every arrowhead clearly wider than its own line.
      markerEnd: { type: MarkerType.ArrowClosed, width: Math.max(3, 16 / w), height: Math.max(3, 16 / w), color },
    };
  });
  return { nodes, edges };
}

export function MoneyFlowGraph({ graph }: { graph: MoneyGraph }) {
  const navigate = useNavigate();
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);
  return (
    <div className="h-[420px] w-full overflow-hidden rounded-lg border border-zinc-200 lg:h-[560px] dark:border-zinc-800">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode="system"
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable={false}
        nodesConnectable={false}
        minZoom={0.2}
        onNodeClick={(_, n) => graph.nodes.find((x) => x.id === n.id && !x.is_system) && navigate(`/accounts/${n.id}`)}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
