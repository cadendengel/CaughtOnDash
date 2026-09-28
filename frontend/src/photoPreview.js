// The viewable preview for an incident photo, made here rather than on the
// server. The server used to decode every upload to make it -- a 24 MP iPhone
// HEIC peaked at +165 MB even shrunk first, on a 512 MB instance, and once took
// the service down. The browser has to decode the photo to show it anyway.
//
// The original is always uploaded untouched and is what gets fingerprinted;
// this is only for looking at. If no preview can be made, the upload goes
// without one and the server falls back to making it.

export const PREVIEW_MAX_SIDE = 2048
const PREVIEW_QUALITY = 0.85

const isHeic = (file) =>
  /image\/hei[cf]/i.test(file.type || '') || /\.hei[cf]$/i.test(file.name || '')

// Browsers other than Safari cannot decode HEIC, so a decoder is loaded -- only
// when a HEIC is actually chosen, to keep it out of the main bundle. It returns
// a bitmap directly (libheif applies the photo's rotation itself), rather than
// a JPEG that would only be decoded again. heic-to wraps libheif and is
// LGPL-3.0; it ships as its own chunk.
const decodeHeic = async (file) => {
  const { heicTo } = await import('heic-to')
  return heicTo({ blob: file, type: 'bitmap' })
}

const bitmapOf = async (source) => {
  // from-image applies the EXIF orientation, so a phone photo comes out upright.
  return createImageBitmap(source, { imageOrientation: 'from-image' })
}

export const previewSize = (width, height, maxSide = PREVIEW_MAX_SIDE) => {
  const scale = Math.min(1, maxSide / Math.max(width, height))
  return [Math.max(1, Math.round(width * scale)), Math.max(1, Math.round(height * scale))]
}

export async function makePreview(file) {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') {
    return null
  }
  let bitmap
  try {
    bitmap = await bitmapOf(file)
  } catch {
    if (!isHeic(file)) return null
    try {
      bitmap = await decodeHeic(file)
    } catch {
      return null
    }
  }

  try {
    const [width, height] = previewSize(bitmap.width, bitmap.height)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) return null
    context.drawImage(bitmap, 0, 0, width, height)
    // A canvas export carries no EXIF: the preview has no GPS in it, whatever
    // the original had.
    return await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', PREVIEW_QUALITY))
  } finally {
    bitmap.close?.()
  }
}
