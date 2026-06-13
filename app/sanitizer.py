# app/sanitizer.py
import logging
import bleach

logger = logging.getLogger(__name__)

ALLOWED_TAGS = [
    'p', 'br', 'b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dd', 'dt',
    'a',
    'img',
    'blockquote', 'code', 'pre',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'figure', 'figcaption',
]

ALLOWED_ATTRIBUTES = {
    '*': ['class', 'id', 'style'],
    'a': ['href', 'title', 'target', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'style'],
    'table': ['summary'],
    'td': ['colspan', 'rowspan', 'align', 'valign'],
    'th': ['colspan', 'rowspan', 'align', 'valign', 'scope'],
}


def sanitize_html_content(html_content: str) -> str:
    if not html_content:
        return ""
    safe_protocols = ['http', 'https', 'mailto', 'ftp']
    cleaned_html = bleach.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=safe_protocols,
        strip=True,
        strip_comments=True,
    )
    logger.debug(f"Sanitized HTML. Original length: {len(html_content)}, Cleaned length: {len(cleaned_html)}")
    return cleaned_html
