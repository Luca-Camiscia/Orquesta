def parse_duration(text: str) -> int:
    """Convierte duraciones como "1h30m" o "45s" a segundos."""
    total = 0
    for unidad, factor in (("h", 3600), ("m", 60), ("s", 1)):
        if unidad in text:
            numero, text = text.split(unidad)
            total += int(numero) * factor
    return total
