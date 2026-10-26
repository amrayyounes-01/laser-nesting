import os
import math
import re
import tempfile
import ezdxf
from shapely.geometry import Polygon, MultiPolygon, Point, box, LineString
from shapely.ops import polygonize, unary_union

class PartGeometry:
    def __init__(self, name="Part", part_id=1):
        self.part_id = part_id
        self.name = name
        self.matched_text = ""
        self.quantity = 1
        self.outer_poly = None  # Shapely Polygon representing outer perimeter
        self.holes = []         # List of Shapely Polygons for holes
        self.bounds = (0, 0, 0, 0)  # minx, miny, maxx, maxy
        self.width = 0.0
        self.height = 0.0
        self.area = 0.0
        self.total_perimeter = 0.0
        self.dxf_entities = []  # Specific entities belonging to this part
        self.doc = None

def parse_quantity_text(text_val):
    """
    Parses quantity from text labels like: Q10, Q 10, QTY 10, Qty: 10, x10, 10x, 10 pcs, etc.
    """
    if not text_val:
        return None
    patterns = [
        r'[Qq](?:ty)?[:\s\-]*([0-9]+)',       # Q10, QTY: 10, Q 10
        r'(?:^|\s)x\s*([0-9]+)',              # x10, x 10
        r'([0-9]+)\s*(?:x|pcs|pieces|قطع)',   # 10x, 10 pcs, 10 pieces
        r'(?:^|\s)no[:\s\-]*([0-9]+)',        # NO: 10, No 10
        r'^\s*([0-9]+)\s*$'                   # Just a standalone number like 10
    ]
    for pat in patterns:
        m = re.search(pat, text_val, re.IGNORECASE)
        if m:
            try:
                val = int(m.group(1))
                if 1 <= val <= 100000:
                    return val
            except Exception:
                pass
    return None

def parse_dxf_multi(dxf_input):
    """
    Parses a DXF file that may contain one or MULTIPLE parts arranged next to each other,
    with text tags (e.g. Q10, Q9, Q1...) indicating the quantity of each part.
    Returns a list of PartGeometry objects.
    """
    temp_path = None
    try:
        if isinstance(dxf_input, str) and os.path.exists(dxf_input):
            doc = ezdxf.readfile(dxf_input)
        elif isinstance(dxf_input, (bytes, bytearray)):
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
                tf.write(dxf_input)
                temp_path = tf.name
            doc = ezdxf.readfile(temp_path)
        elif hasattr(dxf_input, "getvalue"):
            data = dxf_input.getvalue()
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
                tf.write(data)
                temp_path = tf.name
            doc = ezdxf.readfile(temp_path)
        elif hasattr(dxf_input, "read"):
            data = dxf_input.read()
            if isinstance(data, str):
                data = data.encode('utf-8', errors='ignore')
            with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tf:
                tf.write(data)
                temp_path = tf.name
            doc = ezdxf.readfile(temp_path)
        else:
            raise ValueError(f"Unsupported DXF input type: {type(dxf_input)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    msp = doc.modelspace()

    # 1. Collect all TEXT and MTEXT entities
    texts = []
    for entity in msp.query('TEXT MTEXT'):
        try:
            val = entity.dxf.text if entity.dxftype() == 'TEXT' else entity.text
            clean_val = val.strip() if val else ""
            pos_x = entity.dxf.insert.x
            pos_y = entity.dxf.insert.y
            parsed_q = parse_quantity_text(clean_val)
            texts.append({
                'text': clean_val,
                'quantity': parsed_q,
                'x': pos_x,
                'y': pos_y,
                'entity': entity
            })
        except Exception:
            pass

    # 2. Extract geometry edges and explicit closed polygons
    edges = []
    explicit_polygons = []
    geom_entities = []

    # LWPOLYLINE
    for entity in msp.query('LWPOLYLINE'):
        try:
            pts = [(p[0], p[1]) for p in entity.get_points()]
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    edges.append(LineString([pts[i], pts[i+1]]))
                if entity.is_closed and len(pts) >= 3:
                    edges.append(LineString([pts[-1], pts[0]]))
                    p_poly = Polygon(pts)
                    if p_poly.is_valid and p_poly.area > 0.01:
                        explicit_polygons.append(p_poly)
                geom_entities.append(entity)
        except Exception:
            pass

    # 2D/3D POLYLINE
    for entity in msp.query('POLYLINE'):
        try:
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    edges.append(LineString([pts[i], pts[i+1]]))
                if entity.is_closed and len(pts) >= 3:
                    edges.append(LineString([pts[-1], pts[0]]))
                    p_poly = Polygon(pts)
                    if p_poly.is_valid and p_poly.area > 0.01:
                        explicit_polygons.append(p_poly)
                geom_entities.append(entity)
        except Exception:
            pass

    # LINE
    for entity in msp.query('LINE'):
        try:
            p1 = (entity.dxf.start.x, entity.dxf.start.y)
            p2 = (entity.dxf.end.x, entity.dxf.end.y)
            if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) > 0.0001:
                edges.append(LineString([p1, p2]))
                geom_entities.append(entity)
        except Exception:
            pass

    # CIRCLE
    for entity in msp.query('CIRCLE'):
        try:
            center = (entity.dxf.center.x, entity.dxf.center.y)
            radius = entity.dxf.radius
            if radius > 0.001:
                c_poly = Point(center).buffer(radius, resolution=32)
                if c_poly.is_valid and c_poly.area > 0.01:
                    explicit_polygons.append(c_poly)
                geom_entities.append(entity)
        except Exception:
            pass

    # ARC
    for entity in msp.query('ARC'):
        try:
            cx, cy = entity.dxf.center.x, entity.dxf.center.y
            r = entity.dxf.radius
            start_ang = math.radians(entity.dxf.start_angle)
            end_ang = math.radians(entity.dxf.end_angle)
            span = (end_ang - start_ang) % (2 * math.pi)
            if span == 0:
                span = 2 * math.pi

            num_segs = max(8, int(span / (math.pi / 16)))
            arc_pts = []
            for i in range(num_segs + 1):
                ang = start_ang + span * (i / num_segs)
                arc_pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
            
            for i in range(len(arc_pts) - 1):
                edges.append(LineString([arc_pts[i], arc_pts[i+1]]))
            geom_entities.append(entity)
        except Exception:
            pass

    # SPLINE
    for entity in msp.query('SPLINE'):
        try:
            pts = [(p[0], p[1]) for p in entity.flattening(distance=0.1)]
            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    edges.append(LineString([pts[i], pts[i+1]]))
                if entity.closed and len(pts) >= 3:
                    edges.append(LineString([pts[-1], pts[0]]))
                geom_entities.append(entity)
        except Exception:
            pass

    # Reconstruct loops from lines and arcs
    if edges:
        try:
            merged_lines = unary_union(edges)
            reconstructed = list(polygonize(merged_lines))
            explicit_polygons.extend(reconstructed)
        except Exception:
            pass

    # Clean and deduplicate polygons
    unique_polys = []
    for p in explicit_polygons:
        if p.is_valid and p.area > 0.01:
            if not any(abs(p.area - u.area) < 0.05 and p.centroid.distance(u.centroid) < 0.1 for u in unique_polys):
                unique_polys.append(p)

    if not unique_polys:
        raise ValueError("No closed geometry or cutting contours detected in this DXF file!")

    # Separate top-level outer boundaries:
    # A polygon is an outer boundary if it is NOT contained inside another strictly larger polygon
    outer_polygons = []
    for p in unique_polys:
        is_inner = False
        for other in unique_polys:
            if other != p and other.contains(p) and (other.area > p.area * 1.05):
                is_inner = True
                break
        if not is_inner:
            outer_polygons.append(p)

    # Sort parts left-to-right (by X coordinate) to match visual layout in CAD
    outer_polygons.sort(key=lambda p: p.bounds[0])

    parts = []
    for idx, outer in enumerate(outer_polygons):
        part = PartGeometry(name=f"Part {idx + 1}", part_id=idx + 1)
        part.doc = doc
        part.outer_poly = outer
        minx, miny, maxx, maxy = outer.bounds
        part.bounds = (minx, miny, maxx, maxy)
        part.width = maxx - minx
        part.height = maxy - miny

        # Identify holes strictly belonging to this outer boundary
        part_holes = []
        for u in unique_polys:
            if u != outer and outer.contains(u) and (u.area < outer.area * 0.95):
                # Ensure it's not a hole of a different part
                part_holes.append(u)
        part.holes = part_holes
        part.area = max(0.01, outer.area - sum(h.area for h in part_holes))

        # Match closest quantity text (e.g. Q10, Q9...)
        cx = (minx + maxx) / 2.0
        cy = (miny + maxy) / 2.0
        best_text = None
        min_dist = float('inf')
        
        for t in texts:
            # Heavily weight X alignment (columns) because Q-labels are placed above/below the part
            dx = abs(t['x'] - cx)
            dy = abs(t['y'] - cy)
            
            # Check if text is roughly in the same X column (within part width + 50mm)
            if dx <= (part.width / 2.0 + 80.0):
                dist = dx * 3.0 + dy
            else:
                dist = dx * 10.0 + dy
                
            if dist < min_dist:
                min_dist = dist
                best_text = t

        if best_text and best_text.get('quantity'):
            part.quantity = best_text['quantity']
            part.matched_text = best_text['text']
            part.name = f"Part {idx + 1} ({best_text['text']})"
        else:
            part.quantity = 1
            part.matched_text = ""
            part.name = f"Part {idx + 1}"

        # Collect DXF entities belonging to this part
        part_bbox = box(minx - 1.5, miny - 1.5, maxx + 1.5, maxy + 1.5)
        part_ents = []
        part_cut_length = 0.0

        for e in geom_entities:
            try:
                etype = e.dxftype()
                epos = None
                elen = 0.0
                if etype == 'LINE':
                    p1 = (e.dxf.start.x, e.dxf.start.y)
                    p2 = (e.dxf.end.x, e.dxf.end.y)
                    epos = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
                    elen = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                elif etype in ('CIRCLE', 'ARC'):
                    epos = (e.dxf.center.x, e.dxf.center.y)
                    r = e.dxf.radius
                    if etype == 'CIRCLE':
                        elen = 2 * math.pi * r
                    else:
                        sa = math.radians(e.dxf.start_angle)
                        ea = math.radians(e.dxf.end_angle)
                        sp = (ea - sa) % (2 * math.pi)
                        elen = r * sp
                elif etype in ('LWPOLYLINE', 'POLYLINE', 'SPLINE'):
                    pts = e.get_points() if etype == 'LWPOLYLINE' else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    if pts:
                        epos = (pts[0][0], pts[0][1])
                        for i in range(len(pts) - 1):
                            elen += math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])

                if epos and part_bbox.contains(Point(epos)):
                    part_ents.append(e)
                    part_cut_length += elen
            except Exception:
                pass

        part.dxf_entities = part_ents
        part.total_perimeter = part_cut_length if part_cut_length > 0 else (2 * (part.width + part.height))
        parts.append(part)

    return parts

def parse_dxf(dxf_input):
    """Backwards-compatible wrapper returning the primary or first part."""
    parts = parse_dxf_multi(dxf_input)
    if not parts:
        raise ValueError("No parts detected in DXF")
    return parts[0]
