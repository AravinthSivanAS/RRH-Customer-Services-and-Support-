interface LogoProps {
  compact?: boolean;
}

export default function Logo({ compact = false }: LogoProps) {
  return (
    <div className={`brand${compact ? " brand--compact" : ""}`} aria-label="Yacy, fresh every week">
      <svg className="brand__mark" viewBox="0 0 44 44" role="img" aria-label="Yacy">
        <rect width="44" height="44" rx="13" fill="currentColor" />
        <text
          x="10"
          y="33"
          fill="white"
          fontFamily="Arial, sans-serif"
          fontSize="34"
          fontWeight="700"
        >
          y
        </text>
        <circle cx="34.5" cy="32.5" r="3" fill="#f5c84b" />
      </svg>
      <span className="brand__wording">
        <strong>yacy</strong>
        {!compact && <small>fresh, every week</small>}
      </span>
    </div>
  );
}
