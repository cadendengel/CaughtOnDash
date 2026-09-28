// Class strings shared across pages. They began life in App.jsx; the few the
// incident report also needs live here so both import one definition rather
// than drifting copies. See App.jsx for why each exists.

// Shared card chrome. One definition rather than three: this was a grouped
// CSS rule covering feed cards, empty-state cards and the upload form, so
// migrating any one of them alone would have duplicated it and let the three
// drift apart.
export const CARD = 'rounded-card border border-ink/10 bg-white/[0.86] shadow-card'

// Buttons. The most reused classes in the app -- 17 call sites for the ghost
// variant alone -- so they are constants rather than repeated strings.
export const GHOST_BTN = 'cursor-pointer rounded-full border border-ink/15 bg-white/80 px-4 py-2 ' +
  'font-semibold text-ink transition-[background,transform,border-color] duration-200 ' +
  'hover:-translate-y-px hover:bg-white/95 ' +
  'disabled:translate-y-0 disabled:cursor-wait disabled:opacity-60'
export const ACTION_ROW = 'mt-3 flex flex-wrap items-center gap-2 max-[640px]:gap-2'
export const FORM_ACTIONS = 'mt-1 flex flex-wrap gap-3'
export const FORM_MESSAGE = 'rounded-control px-4 py-3 font-semibold'
export const FORM_MESSAGE_ERROR = `${FORM_MESSAGE} bg-red-600/10 text-[#991b1b]`
export const FORM_MESSAGE_SUCCESS = `${FORM_MESSAGE} bg-green-600/10 text-good`
export const PAGE_CONTENT = 'grid w-[min(1040px,100%)] gap-4'
export const PAGE_HEADING = '[&>h2]:font-heading [&>h2]:text-[clamp(1.8rem,3vw,2.8rem)] ' +
  '[&>h2]:tracking-[-0.03em] [&>h2]:text-ink [&>p]:mt-1 [&>p]:text-muted'
export const EYEBROW = 'inline-flex items-center gap-2 text-[0.72rem] font-bold uppercase ' +
  'tracking-[0.24em] text-muted'
export const BTN_BASE = 'mt-4 cursor-pointer rounded-full px-4 py-2 font-semibold'
export const PRIMARY_BTN = BTN_BASE + ' border-none bg-gradient-to-br from-ink to-brand text-white'
export const SECONDARY_BTN = BTN_BASE + ' border border-ink/15 bg-white/70 text-ink'
