"use client";

import { Component, type ComponentType, type ReactNode } from "react";
import { validateBlock, type UiBlock } from "@/lib/ui-contract";
import PlanCard from "./blocks/PlanCard";
import ClarifyQuestion from "./blocks/ClarifyQuestion";
import EvidenceCard from "./blocks/EvidenceCard";
import EvidenceTable from "./blocks/EvidenceTable";
import ConclusionCard from "./blocks/ConclusionCard";
import ApprovalCard from "./blocks/ApprovalCard";
import DegradationNotice from "./blocks/DegradationNotice";
import FallbackJsonCard from "./blocks/FallbackJsonCard";

/** 已实现组件注册表（严格对应 IMPLEMENTED_COMPONENTS 的 7 个）。 */
const REGISTRY: Record<string, ComponentType<Record<string, unknown>>> = {
  plan_card: PlanCard,
  clarify_question: ClarifyQuestion,
  evidence_card: EvidenceCard,
  evidence_table: EvidenceTable,
  conclusion_card: ConclusionCard,
  approval_card: ApprovalCard,
  degradation_notice: DegradationNotice,
};

interface BoundaryProps {
  fallback: ReactNode;
  children: ReactNode;
}

/** 渲染期异常兜底：任何子组件抛错都退化为 FallbackJsonCard，绝不白屏。 */
class BlockErrorBoundary extends Component<BoundaryProps, { hasError: boolean }> {
  constructor(props: BoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(): void {
    /* 静默：降级卡片已足够告知用户 */
  }

  render(): ReactNode {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export interface DynamicRendererProps {
  block: UiBlock;
  strict?: boolean;
}

export default function DynamicRenderer({ block, strict = true }: DynamicRendererProps) {
  const result = validateBlock(block, { strict });
  const Comp = result.ok ? REGISTRY[result.component] : undefined;

  if (!result.ok || !Comp) {
    return <FallbackJsonCard block={block} reason={result.reason} />;
  }

  const props = isRecord(block.props) ? block.props : {};
  const merged: Record<string, unknown> =
    block.refs !== undefined ? { ...props, refs: block.refs } : { ...props };

  return (
    <BlockErrorBoundary fallback={<FallbackJsonCard block={block} reason="组件渲染期异常，已降级" />}>
      <Comp {...merged} />
    </BlockErrorBoundary>
  );
}
