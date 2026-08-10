import { rankLabel, rankTooltip } from "@/lib/public";

type Props = {
  letter: string | null | undefined;
  showLabel?: boolean;
};

export default function RankBadge({ letter, showLabel = false }: Props) {
  const L = letter || "?";
  const label = rankLabel(letter);
  return (
    <span className={`badge rank-badge ${L}`} title={rankTooltip(letter)}>
      {L}
      {showLabel && label ? <span className="rank-badge-label"> {label}</span> : null}
    </span>
  );
}
