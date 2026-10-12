def parse_csv_line(line: str) -> list[str]:
    """Separa una línea CSV en campos."""
    return line.split(",")
