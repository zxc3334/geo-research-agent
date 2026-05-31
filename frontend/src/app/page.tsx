"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import Header from "@/components/Header";
import ResearchForm from "@/components/ResearchForm";
import ProgressPanel from "@/components/ProgressPanel";
import ReportViewer from "@/components/ReportViewer";
import {
  submitResearch,
  getTask,
  getReport,
  subscribeTaskStream,
  type TaskDetail,
  type TaskProgress,
  type ReportDetail,
  type SSEEvent,
} from "@/lib/api";
import { Sparkles } from "lucide-react";

type ViewState = "idle" | "submitting" | "streaming" | "done" | "error";

export default function Home() {
  const [viewState, setViewState] = useState<ViewState>("idle");
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [progress, setProgress] = useState<TaskProgress>({
    phase: "",
    completed_subtasks: 0,
    total_subtasks: 0,
    current_task: "",
    evidence_collected: 0,
  });
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const handleSubmit = useCallback(
    async (query: string, domain: string, depth: number) => {
      setViewState("submitting");
      setReport(null);
      setError(null);
      setProgress({
        phase: "",
        completed_subtasks: 0,
        total_subtasks: 0,
        current_task: "",
        evidence_collected: 0,
      });

      try {
        // 1. Submit research task
        const res = await submitResearch({ query, domain, depth });
        const taskId = res.task_id;

        // 2. Get initial task detail
        const detail = await getTask(taskId);
        setTaskDetail(detail);
        setViewState("streaming");

        // 3. Polling fallback — ensures we always get the final state
        let settled = false;
        const finishWithTask = async (finalTask: TaskDetail) => {
          if (settled) return;
          settled = true;
          setTaskDetail(finalTask);
          if (finalTask.status === "completed" && finalTask.report_id) {
            try {
              const rpt = await getReport(finalTask.report_id);
              setReport(rpt);
              setViewState("done");
            } catch (e: any) {
              setError(e.message);
              setViewState("error");
            }
          } else if (finalTask.status === "failed") {
            setError(finalTask.error || "研究任务失败");
            setViewState("error");
          }
        };

        // Start polling every 3s as safety net
        if (pollRef.current) clearInterval(pollRef.current);
        const pollInterval = setInterval(async () => {
          try {
            const t = await getTask(taskId);
            setTaskDetail(t);
            setProgress({
              phase: t.progress?.phase || "",
              completed_subtasks: t.progress?.completed_subtasks || 0,
              total_subtasks: t.progress?.total_subtasks || 0,
              current_task: t.progress?.current_task || "",
              current_task_desc: t.progress?.current_task_desc || "",
              evidence_collected: t.progress?.evidence_collected || 0,
              task_list: t.progress?.task_list || [],
            });
            if (t.status === "completed" || t.status === "failed") {
              clearInterval(pollInterval);
              await finishWithTask(t);
            }
          } catch {
            // ignore poll errors
          }
        }, 3000);

        // 4. Subscribe to SSE for real-time progress (faster updates)
        subscribeTaskStream(
          taskId,
          async (event: SSEEvent) => {
            if ("event" in event) {
              if (event.event === "done") {
                clearInterval(pollInterval);
                try {
                  const finalTask = await getTask(taskId);
                  await finishWithTask(finalTask);
                } catch (e: any) {
                  if (!settled) {
                    setError(e.message);
                    setViewState("error");
                  }
                }
              }
              return;
            }

            // Progress update from SSE
            setProgress({
              phase: event.phase || "",
              completed_subtasks: event.completed_subtasks || 0,
              total_subtasks: event.total_subtasks || 0,
              current_task: event.current_task || "",
              current_task_desc: event.current_task_desc || "",
              evidence_collected: event.evidence_collected || 0,
              task_list: event.task_list || progress.task_list || [],
            });
          },
          (err) => {
            // SSE error — polling will handle it, don't set error state
            console.warn("SSE error (polling fallback active):", err);
          }
        );
      } catch (e: any) {
        setError(e.message || "提交失败");
        setViewState("error");
      }
    },
    []
  );

  const handleReset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setViewState("idle");
    setTaskDetail(null);
    setReport(null);
    setError(null);
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Header />

      <main className="flex-1 max-w-4xl w-full mx-auto px-4 py-8">
        {/* Hero */}
        {viewState === "idle" && (
          <div className="text-center mb-10 animate-fade-in-up">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-brand-400/10 border border-brand-400/20 text-brand-400 text-xs font-medium mb-4">
              <Sparkles className="w-3.5 h-3.5" />
              AI 深度研究 Agent
            </div>
            <h1 className="text-3xl font-bold text-white mb-3 tracking-tight">
              输入问题，获取
              <span className="text-brand-400">带证据分级</span>
              的研究报告
            </h1>
            <p className="text-slate-400 max-w-xl mx-auto text-sm leading-relaxed">
              自动搜索官方文档、学术论文和领域知识库，通过红蓝对抗质量保证，生成可信的研究报告。
            </p>
          </div>
        )}

        {/* Research Form */}
        <div className="mb-8">
          <ResearchForm
            onSubmit={handleSubmit}
            isLoading={viewState === "submitting" || viewState === "streaming"}
          />
        </div>

        {/* Progress */}
        {(viewState === "streaming" || viewState === "done" || viewState === "error") &&
          taskDetail && (
            <div className="mb-6">
              <ProgressPanel
                status={taskDetail.status}
                progress={progress}
                error={error}
              />
            </div>
          )}

        {/* Report */}
        {report && viewState === "done" && (
          <div className="mb-8">
            <ReportViewer report={report} />
          </div>
        )}

        {/* Error State */}
        {viewState === "error" && !report && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-6 text-center animate-fade-in-up">
            <p className="text-red-300 mb-4">{error}</p>
            <button
              onClick={handleReset}
              className="px-4 py-2 bg-surface-2 text-slate-300 rounded-lg text-sm hover:bg-surface-3 transition-colors"
            >
              重试
            </button>
          </div>
        )}

        {/* Reset Button */}
        {viewState === "done" && (
          <div className="text-center mb-8">
            <button
              onClick={handleReset}
              className="px-5 py-2 bg-surface-1 border border-surface-2 text-slate-300 rounded-lg text-sm hover:bg-surface-2 transition-colors"
            >
              开始新研究
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-surface-2 py-4 text-center text-xs text-surface-3">
        GeoResearch Agent — AI Deep Research for GIS & Remote Sensing
      </footer>
    </div>
  );
}
