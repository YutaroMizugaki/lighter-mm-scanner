import { fmtSampleQuality } from "@/lib/format";

type Props = {
  quality: string | null | undefined;
  className?: string;
};

/** Display sample quality label (Insufficient / Preliminary / Reliable). */
export function SampleQualityValue({ quality, className }: Props) {
  return <span className={className}>{fmtSampleQuality(quality)}</span>;
}
