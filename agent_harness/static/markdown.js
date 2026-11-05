'use strict';
// Treat model output as untrusted: parse Markdown, then allow only display markup.
window.AgentMarkdown = {
  render(text) {
    const body = document.createElement('div');
    body.className = 'message-body markdown-body';
    if (!window.marked || !window.DOMPurify) {
      body.textContent = text;
      body.classList.remove('markdown-body');
      return body;
    }
    const html = marked.parse(text, {gfm: true, breaks: true, async: false});
    const fragment = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: ['p','br','strong','em','del','s','a','code','pre','blockquote',
        'ul','ol','li','h1','h2','h3','h4','h5','h6','hr','table','thead','tbody','tr','th','td'],
      ALLOWED_ATTR: ['href','title','start','class'],
      ALLOW_DATA_ATTR: false,
      ALLOW_ARIA_ATTR: false,
      RETURN_DOM_FRAGMENT: true,
    });
    fragment.querySelectorAll('a[href]').forEach(a => {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
    });
    body.append(fragment);
    return body;
  }
};
