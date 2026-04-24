"use client";

import { SegmentedControl, type SegmentedOption } from "./SegmentedControl";

export type Intensity = "auto" | "chill" | "balanced" | "hype";

const OPTIONS_WITH_AUTO: readonly SegmentedOption<Intensity>[] = [
  { value: "auto", label: "Auto", sublabel: "Match music" },
  { value: "chill", label: "Chill", sublabel: "Long cuts" },
  { value: "balanced", label: "Balanced", sublabel: "Default" },
  { value: "hype", label: "Hype", sublabel: "Beat-rapid" },
];

const OPTIONS_NO_AUTO: readonly SegmentedOption<Intensity>[] = [
  { value: "chill", label: "Chill", sublabel: "Long cuts" },
  { value: "balanced", label: "Balanced", sublabel: "Default" },
  { value: "hype", label: "Hype", sublabel: "Beat-rapid" },
];

export function IntensityPicker({
  value,
  onChange,
  /** Hide Auto when AI isn't available — it can't do anything without Gemini. */
  showAuto = true,
}: {
  value: Intensity;
  onChange: (v: Intensity) => void;
  showAuto?: boolean;
}) {
  return (
    <SegmentedControl
      options={showAuto ? OPTIONS_WITH_AUTO : OPTIONS_NO_AUTO}
      value={value}
      onChange={onChange}
      ariaLabel="Edit intensity"
    />
  );
}
