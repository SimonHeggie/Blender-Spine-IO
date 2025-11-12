import math

def normalize_deg(a: float) -> float:
    a = (a + 180.0) % 360.0 - 180.0
    if a == -180.0: 
        return 180.0
    return a

def is_vertical_rel_deg(rel_ccw: float, tol: float) -> bool:
    rel_n = abs(normalize_deg(rel_ccw))
    return abs(rel_n - 90.0) <= tol

def rotate2d(x: float, y: float, deg: float):
    if not deg or abs(deg) < 1e-9:
        return x, y
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return (c*x - s*y, s*x + c*y)

__all__ = ["normalize_deg","is_vertical_rel_deg","rotate2d"]
