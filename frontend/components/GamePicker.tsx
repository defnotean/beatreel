"use client";

import { SegmentedControl, type SegmentedOption } from "./SegmentedControl";

export type Game = "valorant_ai" | "valorant" | "generic";

const OPTIONS: readonly SegmentedOption<Game>[] = [
  { value: "valorant_ai", label: "Valorant AI", sublabel: "Gemini detects kills" },
  { value: "valorant", label: "Valorant", sublabel: "Audio template" },
  { value: "generic", label: "Generic", sublabel: "Audio peaks" },
];

export function GamePicker({
  value,
  onChange,
  aiDisabled,
}: {
  value: Game;
  onChange: (v: Game) => void;
  /** AI option is greyed out in the UI when no Gemini key is set. */
  aiDisabled?: boolean;
}) {
  const options = aiDisabled
    ? OPTIONS.map((o) =>
        o.value === "valorant_ai"
          ? { ...o, sublabel: "Set key in Settings" }
          : o,
      )
    : OPTIONS;
  return (
    <SegmentedControl
      options={options}
      value={value}
      onChange={(v) => {
        if (aiDisabled && v === "valorant_ai") return;
        onChange(v);
      }}
      ariaLabel="Detector profile"
    />
  );
}
