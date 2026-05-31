"use client";

import { cn } from "@/lib/utils";
import type { TaskProgress } from "@/lib/api";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Search,
  FileText,
  BarChart3,
  Shield,
  RefreshCw,
} from "lucide-react";

interface Props {
  status: "queued" | "running" | "completed" | "failed";
  progress: TaskProgress;
  error?: string | null;
}

const PHASE_LABELS: Record<string, string> = {
  initializing: "初始化",
  loading_modules: "加载模块",
  planning: "规划研究方向",
  dispatching: "分派子任务",
  researching: "搜索与收集",
  collecting: "收集证据",
  synthesizing: "生成报告",
  adversarial: "对抗验证",
  validating: "验证与审核",
  replanning: "重新规划",
  completed: "研究完成",
};

const PHASE_ICONS: Record<string, typeof Search> = {
  initializing: Loader2,
  loading_modules: Loader2,
  planning: FileText,
  dispatching: Search,
  researching: Search,
  collecting: BarChart3,
  synthesizing: FileText,
  adversarial: Shield,
  validating: Shield,
  replanning: RefreshCw,
};

export default function ProgressPanel({ status, progress, error }: Props) {
  const progressPercent =
    progress.total_subtasks > 0
      ? Math.round(
          (progress.completed_subtasks / progress.total_subtasks) * 100
        )
      : status === "completed"
      ? 100
      : 0;

  const PhaseIcon = PHASE_ICONS[progress.phase] || Search;

  return (
    <div className="bg-surface-1 border border-surface-2 rounded-xl p-5 animate-fade-in-up">
      {/* Status Header */}
      <div className="flex items-center gap-3 mb-4">
        {status === "running" && (
          <Loader2 className="w-5 h-5 text-brand-400 animate-spin" />
        )}
        {status === "completed" && (
          <CheckCircle2 className="w-5 h-5 text-emerald-400" />
        )}
        {status === "failed" && (
          <XCircle className="w-5 h-5 text-red-400" />
        )}
        {status === "queued" && (
          <span className="w-5 h-5 rounded-full border-2 border-brand-400 border-t-transparent animate-spin" />
        )}
        <div>
          <h3 className="text-sm font-semibold text-white">
            {status === "completed"
              ? "研究完成"
              : status === "failed"
              ? "研究失败"
              : PHASE_LABELS[progress.phase] || progress.phase || "准备中…"}
          </h3>
          {progress.current_task_desc && (
            <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">
              {progress.current_task_desc}
            </p>
          )}
          {!progress.current_task_desc && progress.current_task && (
            <p className="text-xs text-slate-400 mt-0.5">
              {progress.current_task}
            </p>
          )}
        </div>
      </div>

      {/* Task List (planning result) */}
      {progress.task_list && progress.task_list.length > 0 && (
        <div className="mb-3 space-y-1">
          {progress.task_list.map((t, i) => {
            const isDone = i < progress.completed_subtasks;
            const isCurrent = i === progress.completed_subtasks;
            return (
              <div
                key={t.id}
                className={cn(
                  "flex items-start gap-2 px-2 py-1 rounded text-xs",
                  isDone
                    ? "text-emerald-400/70 line-through"
                    : isCurrent
                    ? "text-brand-400 bg-brand-400/5"
                    : "text-surface-3"
                )}
              >
                {isDone ? (
                  <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                ) : isCurrent ? (
                  <Loader2 className="w-3.5 h-3.5 mt-0.5 shrink-0 animate-spin" />
                ) : (
                  <span className="w-3.5 h-3.5 mt-0.5 shrink-0 rounded-full border border-surface-3" />
                )}
                <span className="line-clamp-1">{t.description}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Progress Bar */}
      <div className="mb-3">
        <div className="flex justify-between text-xs text-slate-400 mb-1.5">
          <span>
            {progress.completed_subtasks} / {progress.total_subtasks} 子任务
          </span>
          <span>{progressPercent}%</span>
        </div>
        <div className="h-1.5 bg-surface-0 rounded-full overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500",
              status === "completed"
                ? "bg-emerald-400"
                : status === "failed"
                ? "bg-red-400"
                : "bg-brand-400"
            )}
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Phase Steps */}
      <div className="flex items-center gap-1.5 mb-3 flex-wrap">
        {(["planning", "researching", "collecting", "synthesizing", "adversarial"] as const).map(
          (key) => {
            const label = PHASE_LABELS[key];
            const Icon = PHASE_ICONS[key] || Search;
            const isActive = progress.phase === key;
            const phaseOrder = ["initializing", "loading_modules", "planning", "dispatching", "researching", "collecting", "synthesizing", "adversarial", "completed"];
            const currentIdx = phaseOrder.indexOf(progress.phase);
            const keyIdx = phaseOrder.indexOf(key);
            const isPast = currentIdx > keyIdx;

            return (
              <div
                key={key}
                className={cn(
                  "flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors",
                  isActive
                    ? "bg-brand-400/10 text-brand-400 font-medium"
                    : isPast
                    ? "text-emerald-400/60"
                    : "text-surface-3"
                )}
              >
                <Icon className="w-3 h-3" />
                <span className="hidden sm:inline">{label}</span>
              </div>
            );
          }
        )}
      </div>

      {/* Evidence Counter */}
      {progress.evidence_collected > 0 && (
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <BarChart3 className="w-3.5 h-3.5" />
          已收集 {progress.evidence_collected} 条证据
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-3 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
