import React, { forwardRef } from 'react'
import './VisualContext.css'

/**
 * A picture Tau retrieved, framed as retrieved system data rather than as decoration.
 *
 * This replaces the plain thumbnail Phase 40 put under the reply text. Two things changed and
 * both are deliberate:
 *
 * - It sits ABOVE the reply, not below. The picture is the context the answer is built on, so it
 *   should already be on screen by the time the karaoke line starts sweeping - reading the
 *   sentence first and then discovering the image underneath inverts the order the reply was
 *   composed in.
 * - It is framed with corner crop marks and a [SYSTEM] caption instead of being presented as a
 *   photograph. The distinction is worth drawing on a screen that also shows live camera output:
 *   this is something Tau went and fetched, not something the house is looking at right now.
 *
 * The caption carries the source alongside the description rather than dropping it. Provenance is
 * load-bearing in this system - the same reply pipeline treats fetched web content as untrusted
 * data (see MAIN_SYSTEM_PROMPT's fenced-content rules), so a retrieved picture should say where
 * it came from too.
 */
const VisualContext = forwardRef(function VisualContext({ image }, ref) {
  if (!image?.url) return null

  const description = [image.title, image.source].filter(Boolean).join(' — ') || 'Retrieved image'

  return (
    <figure className="visual-context" ref={ref}>
      <a
        className="visual-context-frame"
        href={image.source_url || image.url}
        target="_blank"
        rel="noreferrer noopener"
      >
        <img src={image.url} alt={image.title || 'retrieved image'} loading="lazy" />
        {/* Four corner marks, each one element drawing two borders. Decorative: the frame says
            "system data" visually, and the caption below says it in words for anyone who isn't
            seeing the frame. */}
        <span className="crop-mark tl" aria-hidden="true" />
        <span className="crop-mark tr" aria-hidden="true" />
        <span className="crop-mark bl" aria-hidden="true" />
        <span className="crop-mark br" aria-hidden="true" />
      </a>
      <figcaption className="visual-context-caption">
        [SYSTEM] VISUAL CONTEXT: {description}
      </figcaption>
    </figure>
  )
})

export default VisualContext
