import { fmtSampleQuality } from "@/lib/format";
import { TOOLTIPS } from "@/lib/public";

type Props = {
  quality: string | null | undefined;
};

export default function QualityChip({ quality }: Props) {
  const label = fmtSampleQuality(quality);
  const tone =
    quality === "reliable"
      ? "quality-reliable"
      : quality === "preliminary"
        ? "quality-preliminary"
        : quality === "insufficient"
          ? "quality-insufficient"
          : "quality-unknown";
  return (
    <span className={`quality-chip ${tone}`} title={TOOLTIPS.sampleQuality}>
      {label}
    </span>
  );
}
