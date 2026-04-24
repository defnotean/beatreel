"use client";

import { SegmentedControl, type SegmentedOption } from "./SegmentedControl";

export type Aspect = "landscape" | "portrait" | "square";

const OPTIONS: readonly SegmentedOption<Aspect>[] = [
  {
    value: "landscape",
    label: "16:9",
    sublabel: "YouTube",
    leading: <div className="border-2 border-current" style={{ width: 20, height: 12 }} />,
  },
  {
    value: "portrait",
    label: "9:16",
    sublabel: "Shorts",
    leading: <div className="border-2 border-current" style={{ width: 12, height: 20 }} />,
  },
  {
    value: "square",
    label: "1:1",
    sublabel: "Instagram",
    leading: <div className="border-2 border-current" style={{ width: 16, height: 16 }} />,
  },
];

export function AspectPicker({
  value,
  onChange,
}: {
  value: Aspect;
  onChange: (v: Aspect) => void;
}) {
  return (
    <SegmentedControl
      options={OPTIONS}
      value={value}
      onChange={onChange}
      ariaLabel="Aspect ratio"
    />
  );
}
