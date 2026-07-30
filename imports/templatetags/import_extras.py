from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dict lookup by variable key in templates: ``mapping|get_item:field.name``."""
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None
