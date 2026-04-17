interface Props {
  color?: string
  size?: number
}

export function NatoSymbol({ color = '#4A90D9', size = 40 }: Props) {
  const w = size
  const h = size * 0.75
  const pad = 2

  return (
    <svg width={w} height={h + 8} viewBox={`0 0 ${w} ${h + 8}`} className="nato-symbol">
      {/* Size indicator dots above */}
      <circle cx={w/2 - 5} cy={4} r={2} fill={color} />
      <circle cx={w/2 + 5} cy={4} r={2} fill={color} />

      {/* Main rectangle */}
      <rect x={pad} y={8} width={w - pad*2} height={h - pad}
            fill="none" stroke={color} strokeWidth={2} />

      {/* Infantry X pattern */}
      <line x1={pad + 2} y1={10} x2={w - pad - 2} y2={h + 6}
            stroke={color} strokeWidth={1.5} />
      <line x1={w - pad - 2} y1={10} x2={pad + 2} y2={h + 6}
            stroke={color} strokeWidth={1.5} />
    </svg>
  )
}
