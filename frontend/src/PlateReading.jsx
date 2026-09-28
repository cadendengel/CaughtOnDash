// A plate reading with its doubtful characters marked, so nobody copies
// "S39 SCA" without seeing that the fourth character may be a 9.
const PlateReading = ({ plate }) => {
  const doubts = new Map((plate.uncertain || []).map((u) => [u.index, u]))
  let index = -1
  return (
    <span className="font-mono text-[1.15rem] font-bold tracking-[0.12em] text-ink" data-testid="plate-reading">
      {[...plate.text].map((char, position) => {
        if (char === ' ') return <span key={position}> </span>
        index += 1
        const doubt = doubts.get(index)
        return doubt ? (
          <span
            key={position}
            className="rounded bg-amber-400/25 underline decoration-dotted decoration-2 underline-offset-4"
            title={`Read as ${doubt.read}; could be ${doubt.could_be.join(', ')}`}
          >
            {char}
          </span>
        ) : (
          <span key={position}>{char}</span>
        )
      })}
    </span>
  )
}

export default PlateReading
