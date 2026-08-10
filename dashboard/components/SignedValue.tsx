import { fmt } from "@/lib/format";

type Props = {
  value: number | null | undefined;
  digits?: number;
  suffix?: string;
};

/** Signed numeric display with positive/negative text styling (not color-only). */
export default function SignedValue({ value, digits = 2, suffix = "bp" }: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="tabular">—</span>;
  }
  const tone = value > 0 ? "signed-pos" : value < 0 ? "signed-neg" : "signed-neu";
  const signWord = value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
  return (
    <span className={`tabular ${tone}`} title={`${signWord} ${suffix}`}>
      {fmt(value, digits, true)}
      {suffix ? <span className="unit"> {suffix}</span> : null}
    </span>
  );
}
