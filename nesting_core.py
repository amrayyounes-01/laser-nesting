import os
import math
import tempfile
import ezdxf
from shapely.geometry import Polygon, MultiPolygon, Point, box, LineString
from shapely.ops import polygonize, unary_union

class PartGeometry:
    def __init__(self, name="Part"):
        self.name = name
        self.outer_poly = None  # Shapely Polygon representing outer perimeter
        self.holes = []         # List of Shapely Polygons for holes
        self.bounds = (0, 0, 0, 0)  # minx, miny, maxx, maxy
        self.width = 0.0
        self.height = 0.0
        self.area = 0.0
        self.total_perimeter = 0.0
        self.doc = None

def parse_dxf(dxf_input):
    """
    Parses a DXF file from a file path, bytes, or file-like object.
    Supports LWPOLYLINE, POLYLINE, LINE, ARC, CIRCLE, SPLINE.
    Uses polygonize and unary_union to reconstruct closed loops seamlessly.
    """
    temp_path = None
    try:
        if isinstance(dxf_input, str) and os.path.exists(dxf_input):
            doc = ezdxf.readfile(dxf_input)
        elif isinstance(dxf_input, bytes):
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
                tf.write(dxf_input)
                temp_path = tf.name
            doc = ezdxf.readfile(temp_path)
        else:
            # File-like object (e.g., Streamlit UploadedFile)
            data = dxf_input.read()
            if isinstance(data, str):
                data = data.encode('utf-8', errors='ignore')
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
                tf.write(data)
                temp_path = tf.name
            doc = ezdxf.readfile(temp_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    msp = doc.modelspace()
    part = PartGeometry()
    part.doc = doc

    edges = []
    explicit_polygons = []
    all_points = []
    total_cut_length = 0.0

    # 1. LWPOLYLINE
    for entity in msp.query('LWPOLYLINE'):
        try:
            pts = [(p[0], p[1]) for p in entity.get_points()]
            if len(pts) >= 2:
                all_points.extend(pts)
                seg_len = 0.0
                for i in range(len(pts) - 1):
                    seg_len += math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
                    edges.append(LineString([pts[i], pts[i+1]]))
                if entity.is_closed and len(pts) >= 3:
                    seg_len += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
                    edges.append(LineString([pts[-1], pts[0]]))
                    p_poly = Polygon(pts)
                    if p_poly.is_valid and p_poly.area > 0.001:
                        explicit_polygons.append(p_poly)
                total_cut_length += seg_len
        except Exception:
            pass

    # 2. 2D/3D POLYLINE
    for entity in msp.query('POLYLINE'):
        try:
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            if len(pts) >= 2:
                all_points.extend(pts)
                for i in range(len(pts) - 1):
                    edges.append(LineString([pts[i], pts[i+1]]))
                if entity.is_closed and len(pts) >= 3:
                    edges.append(LineString([pts[-1], pts[0]]))
                    p_poly = Polygon(pts)
                    if p_poly.is_valid and p_poly.area > 0.001:
                        explicit_polygons.append(p_poly)
        except Exception:
            pass

    # 3. LINE
    for entity in msp.query('LINE'):
        try:
            p1 = (entity.dxf.start.x, entity.dxf.start.y)
            p2 = (entity.dxf.end.x, entity.dxf.end.y)
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if length > 0.0001:
                total_cut_length += length
                edges.append(LineString([p1, p2]))
                all_points.extend([p1, p2])
        except Exception:
            pass

    # 4. CIRCLE
    for entity in msp.query('CIRCLE'):
        try:
            center = (entity.dxf.center.x, entity.dxf.center.y)
            radius = entity.dxf.radius
            if radius > 0.001:
                total_cut_length += 2 * math.pi * radius
                c_poly = Point(center).buffer(radius, resolution=32)
                if c_poly.is_valid and c_poly.area > 0.001:
                    explicit_polygons.append(c_poly)
                all_points.extend([
                    (center[0] - radius, center[1] - radius),
                    (center[0] + radius, center[1] + radius)
                ])
        except Exception:
            pass

    # 5. ARC
    for entity in msp.query('ARC'):
        try:
            cx, cy = entity.dxf.center.x, entity.dxf.center.y
            r = entity.dxf.radius
            start_ang = math.radians(entity.dxf.start_angle)
            end_ang = math.radians(entity.dxf.end_angle)
            span = (end_ang - start_ang) % (2 * math.pi)
            if span == 0:
                span = 2 * math.pi
            total_cut_length += r * span

            # Discretize arc into line segments
            num_segs = max(8, int(span / (math.pi / 16)))
            arc_pts = []
            for i in range(num_segs + 1):
                ang = start_ang + span * (i / num_segs)
                arc_pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
            
            for i in range(len(arc_pts) - 1):
                edges.append(LineString([arc_pts[i], arc_pts[i+1]]))
            all_points.extend(arc_pts)
        except Exception:
            pass

    # 6. SPLINE
    for entity in msp.query('SPLINE'):
        try:
            pts = [(p[0], p[1]) for p in entity.flattening(distance=0.1)]
            if len(pts) >= 2:
                all_points.extend(pts)
                for i in range(len(pts) - 1):
                    edges.append(LineString([pts[i], pts[i+1]]))
                    total_cut_length += math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
                if entity.closed and len(pts) >= 3:
                    edges.append(LineString([pts[-1], pts[0]]))
        except Exception:
            pass

    # Reconstruct closed loops from edges using polygonize
    if edges:
        try:
            merged_lines = unary_union(edges)
            reconstructed = list(polygonize(merged_lines))
            explicit_polygons.extend(reconstructed)
        except Exception:
            pass

    # Clean, validate, and deduplicate polygons
    unique_polys = []
    for p in explicit_polygons:
        if p.is_valid and p.area > 0.01:
            is_dup = False
            for u in unique_polys:
                if abs(p.area - u.area) < 0.01 and p.centroid.distance(u.centroid) < 0.05:
                    is_dup = True
                    break
            if not is_dup:
                unique_polys.append(p)

    if not unique_polys:
        if all_points:
            # Fallback to bounding box if lines don't form a closed loop
            xs = [pt[0] for pt in all_points]
            ys = [pt[1] for pt in all_points]
            minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
            outer = box(minx, miny, maxx, maxy)
            holes = []
        else:
            raise ValueError("الملف لا يحتوي على أشكال هندسية مدعومة (Lines/Polylines/Circles/Arcs)!")
    else:
        # Sort polygons by area descending: the largest one is the outer contour
        unique_polys.sort(key=lambda p: p.area, reverse=True)
        outer = unique_polys[0]
        holes = []
        for other in unique_polys[1:]:
            # A valid interior hole must be smaller than the outer contour and contained within it
            if (other.area < outer.area * 0.95) and (outer.contains(other) or outer.contains(other.centroid)):
                holes.append(other)
            elif not other.intersects(outer):
                # Multiple disjoint outer parts in one DXF: union them
                outer = outer.union(other)

    minx, miny, maxx, maxy = outer.bounds
    part.outer_poly = outer
    part.holes = holes
    part.bounds = (minx, miny, maxx, maxy)
    part.width = maxx - minx
    part.height = maxy - miny
    part.area = max(0.01, outer.area - sum(h.area for h in holes))
    part.total_perimeter = total_cut_length if total_cut_length > 0 else (2 * (part.width + part.height))

    return part
