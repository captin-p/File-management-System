from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from documents.models import SEARCH_HIGHLIGHT_END, SEARCH_HIGHLIGHT_START


register = template.Library()


@register.filter
def search_highlight(value):
    if value is None:
        return ''

    escaped = conditional_escape(str(value))
    escaped = escaped.replace(
        conditional_escape(SEARCH_HIGHLIGHT_START),
        '<mark class="search-hit">',
    )
    escaped = escaped.replace(
        conditional_escape(SEARCH_HIGHLIGHT_END),
        '</mark>',
    )
    return mark_safe(escaped)
