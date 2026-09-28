import { expect, describe, it } from 'vitest'

import { makePreview, previewSize, PREVIEW_MAX_SIDE } from '../photoPreview'

describe('photo previews', () => {
  it('shrinks the case photo to the server limit, keeping its shape', () => {
    // IMG_3110.HEIC, upright: 4284 x 5712.
    expect(previewSize(4284, 5712)).toEqual([1536, 2048])
    expect(Math.max(...previewSize(8000, 3000))).toBe(PREVIEW_MAX_SIDE)
  })

  it('never enlarges a small photo', () => {
    expect(previewSize(640, 480)).toEqual([640, 480])
  })

  it('gives up quietly where the browser cannot decode, so the server makes it', async () => {
    // jsdom has no createImageBitmap, like a very old browser.
    expect(await makePreview(new File(['x'], 'IMG_1.HEIC', { type: 'image/heic' }))).toBeNull()
  })
})
