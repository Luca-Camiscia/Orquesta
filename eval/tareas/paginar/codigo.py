def paginate(items, page, per_page):
    """Devuelve la página `page` (empezando en 1) de `items`."""
    start = page * per_page
    return items[start:start + per_page]
