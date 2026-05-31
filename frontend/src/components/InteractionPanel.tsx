"use client";

import { useState, useRef, useEffect } from "react";
import { cn } from "@/lib/utils";
import { Send, MessageSquare, Loader2 } from "lucide-react";

interface Props {
  taskId: string | null;
  isRunning: boolean;
}

interface Message {
  id: number;
  type: "user" | "system";
  text: string;
  time: string;
}

export default function InteractionPanel({ taskId, isRunning }: Props) {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const listRef = useRef<HTMLDivElement>(null);
  let nextId = useRef(1);

  // Auto-scroll to bottom
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !taskId || sending) return;

    const text = input.trim();
    setInput("");
    setSending(true);

    // Add user message
    const userMsg: Message = {
      id: nextId.current++,
      type: "user",
      text,
      time: new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      }),
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await fetch(`/api/v1/tasks/${taskId}/input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (res.ok) {
        const data = await res.json();
        const sysMsg: Message = {
          id: nextId.current++,
          type: "system",
          text: `✅ 指令已注入：${data.message}`,
          time: new Date().toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          }),
        };
        setMessages((prev) => [...prev, sysMsg]);
      } else {
        const err = await res.json();
        const errMsg: Message = {
          id: nextId.current++,
          type: "system",
          text: `❌ ${err.detail || "提交失败"}`,
          time: new Date().toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          }),
        };
        setMessages((prev) => [...prev, errMsg]);
      }
    } catch (err) {
      const errMsg: Message = {
        id: nextId.current++,
        type: "system",
        text: `❌ 网络错误`,
        time: new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        }),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
    }
  };

  if (!isRunning) return null;

  return (
    <div className="bg-surface-1 border border-surface-2 rounded-xl overflow-hidden animate-fade-in-up">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-surface-2">
        <MessageSquare className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-medium text-white">实时互动</span>
        <span className="text-[11px] text-surface-3 ml-auto">
          研究进行中，可随时调整方向
        </span>
      </div>

      {/* Messages */}
      {messages.length > 0 && (
        <div
          ref={listRef}
          className="max-h-40 overflow-y-auto px-4 py-2 space-y-1.5"
        >
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={cn(
                "flex gap-2 text-xs",
                msg.type === "user" ? "justify-end" : "justify-start"
              )}
            >
              <div
                className={cn(
                  "px-2.5 py-1 rounded-lg max-w-[80%]",
                  msg.type === "user"
                    ? "bg-brand-400/10 text-brand-300"
                    : "bg-surface-0 text-slate-400"
                )}
              >
                <span>{msg.text}</span>
                <span className="ml-2 text-[10px] text-surface-3">
                  {msg.time}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 px-4 py-2.5 border-t border-surface-2"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入补充指令，如：重点关注 Sentinel-2、忽略 MODIS 数据…"
          className="flex-1 bg-surface-0 border border-surface-2 rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder:text-surface-3 focus:outline-none focus:border-brand-400/50"
          disabled={sending}
        />
        <button
          type="submit"
          disabled={!input.trim() || sending}
          className={cn(
            "p-2 rounded-lg transition-colors",
            input.trim() && !sending
              ? "bg-brand-400 text-surface-0 hover:bg-brand-300"
              : "bg-surface-2 text-surface-3 cursor-not-allowed"
          )}
        >
          {sending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Send className="w-4 h-4" />
          )}
        </button>
      </form>
    </div>
  );
}
