"""Allowlist-based HTML sanitization for email bodies.

The composer lets the user write Markdown which is converted to HTML for the
email. Markdown (and hand-written inline HTML) can contain scripts, event
handlers, or `javascript:` links; anything sent to a third party's mail client
must be cleaned first. We keep a small allowlist of formatting tags that an
email body legitimately needs.
"""
import nh3

ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del",
    "a", "ul", "ol", "li", "h1", "h2", "h3", "h4",
    "blockquote", "code", "pre", "span", "table", "thead", "tbody",
    "tr", "th", "td",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
}

ALLOWED_SCHEMES = {"http", "https", "mailto"}


def sanitize_email_html(html):
    """Return `html` with only allowlisted tags/attributes/schemes retained."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer",
    )
