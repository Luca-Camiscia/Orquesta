def moving_average(xs, k):
    """Promedio móvil de ventana k."""
    return [sum(xs[i:i + k]) / k for i in range(len(xs))]
