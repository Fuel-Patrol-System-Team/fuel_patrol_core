from django.utils.safestring import mark_safe


def format_logs_display(logs_text):
    """Форматирование логов для отображения в админке"""
    if not logs_text:
        return "Нет данных"

    formatted = logs_text.replace('\n', '<br>')
    formatted = formatted.replace('●', '<span style="color: green;">●</span>')
    formatted = formatted.replace('Active:', '<strong>Active:</strong>')
    formatted = formatted.replace('Loaded:', '<strong>Loaded:</strong>')

    return mark_safe(
        f'<div style="font-family: monospace; font-size: 12px; background: #f5f5f5; padding: 10px; border-radius: 5px; max-height: 400px; overflow-y: auto;">{formatted}</div>')
