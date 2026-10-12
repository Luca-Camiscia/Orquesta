def slugify(text: str) -> str:
    """Convierte un título en un slug para URLs."""
    return text.lower().replace(" ", "-")
