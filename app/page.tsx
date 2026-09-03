"use client";

import { useState } from "react";
import {
  CATEGORIES,
  generatePrediction,
  type PredictionCategory,
} from "./lib/predictions";

export default function Home() {
  const [name, setName] = useState("");
  const [category, setCategory] = useState<PredictionCategory>("random");
  const [prediction, setPrediction] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  function handleGenerate() {
    const next = generatePrediction(name, category);
    setPrediction(next);
    setHistory((prev) => [next, ...prev].slice(0, 5));
  }

  return (
    <div className="font-sans min-h-screen flex items-center justify-center p-8">
      <main className="w-full max-w-md flex flex-col gap-6">
        <div className="text-center">
          <h1 className="text-3xl font-semibold tracking-tight">
            🔮 Prediction Generator
          </h1>
          <p className="mt-2 text-sm text-black/60 dark:text-white/60">
            Enter your name, pick a topic, and see what's coming.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name (optional)"
            maxLength={40}
            className="rounded-full border border-black/[.1] dark:border-white/[.145] bg-transparent px-4 py-2 text-sm outline-none focus:border-black/30 dark:focus:border-white/30"
          />

          <div className="flex flex-wrap gap-2 justify-center">
            {CATEGORIES.map((c) => (
              <button
                key={c.value}
                onClick={() => setCategory(c.value)}
                className={`rounded-full px-3 py-1.5 text-sm border transition-colors ${
                  category === c.value
                    ? "bg-foreground text-background border-transparent"
                    : "border-black/[.1] dark:border-white/[.145] hover:bg-black/[.05] dark:hover:bg-white/[.06]"
                }`}
              >
                {c.emoji} {c.label}
              </button>
            ))}
          </div>

          <button
            onClick={handleGenerate}
            className="rounded-full bg-foreground text-background font-medium text-sm h-11 hover:bg-[#383838] dark:hover:bg-[#ccc] transition-colors"
          >
            Reveal my prediction
          </button>
        </div>

        {prediction && (
          <div className="rounded-2xl border border-black/[.1] dark:border-white/[.145] p-5 text-center">
            <p className="text-lg leading-relaxed">{prediction}</p>
          </div>
        )}

        {history.length > 1 && (
          <div className="flex flex-col gap-2">
            <h2 className="text-xs uppercase tracking-wide text-black/50 dark:text-white/50">
              Earlier predictions
            </h2>
            <ul className="flex flex-col gap-1.5 text-sm text-black/60 dark:text-white/60">
              {history.slice(1).map((p, i) => (
                <li key={i} className="truncate">
                  · {p}
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>
    </div>
  );
}
