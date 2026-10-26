import os
import math
import re
import tempfile
import ezdxf
from ezdxf.path import make_path
from shapely.geometry import Polygon, MultiPolygon, Point, box, LineString, MultiPoint
from shapely.ops import polygonize, unary_union

class PartGeometry:
    def __init__(self, name="Part", part_id=1):
        self.part_id = part_id
        self.name = name
        self.matched_text = ""
        self.quantity = 1
        self.outer_poly = None  # Shapely Polygon representing outer perimeter (including interior holes)
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
    Parses a DXF file containing one or MULTIPLE parts arranged next to each other,
    with text tags (e.g. Q10, Q9, Q1...) indicating the quantity of each part.
    
    Robust CAM Features:
    - Explodes block references (INSERT) to capture components.
    - Discretizes lines, arcs, splines, ellipses, and polylines with bulges.
    - Groups parts by spatial connectivity (island clustering).
    - Heals drafting micro-gaps (snapping tolerance = 1.5 mm).
    - Unifies loops to absorb internal crosshairs and centerlines.
    - Correctly maps circular bolt holes to their enclosing parts.
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

    # 1. Explode blocks (INSERT) so library components are accessible
    try:
        inserts = list(msp.query('INSERT'))
        for ins in inserts:
            try:
                ins.explode()
            except Exception:
                pass
    except Exception:
        pass

    # 2. Collect all TEXT and MTEXT entities
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

    # 3. Extract geometry entities
    IGNORE_LAYERS = {'DEFPOINTS', 'DIM', 'DIMENSIONS'}
    contour_items = []
    hole_items = []

    # Polylines, Lines, Arcs, Splines, Ellipses
    for e in msp.query('LWPOLYLINE POLYLINE LINE ARC SPLINE ELLIPSE'):
        try:
            if hasattr(e.dxf, 'layer') and e.dxf.layer.upper() in IGNORE_LAYERS:
                continue
            
            p = make_path(e)
            pts = [(v.x, v.y) for v in p.flattening(distance=0.1)]
            if len(pts) >= 2:
                lines = [LineString([pts[i], pts[i+1]]) for i in range(len(pts)-1)]
                if getattr(e, 'is_closed', False) or getattr(e, 'closed', False):
                    lines.append(LineString([pts[-1], pts[0]]))
                minx = min(pt[0] for pt in pts)
                maxx = max(pt[0] for pt in pts)
                miny = min(pt[1] for pt in pts)
                maxy = max(pt[1] for pt in pts)
                contour_items.append({
                    'entity': e,
                    'lines': lines,
                    'pts': pts,
                    'bbox': box(minx, miny, maxx, maxy)
                })
        except Exception:
            pass

    # Circles
    for e in msp.query('CIRCLE'):
        try:
            if hasattr(e.dxf, 'layer') and e.dxf.layer.upper() in IGNORE_LAYERS:
                continue
            cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
            if r > 0.001:
                hole_items.append({
                    'entity': e,
                    'center': (cx, cy),
                    'radius': r,
                    'poly': Point(cx, cy).buffer(r, resolution=32),
                    'bbox': box(cx - r, cy - r, cx + r, cy + r)
                })
        except Exception:
            pass

    # Handle rare cases where ONLY circles exist (e.g. cutting circular blanks)
    if not contour_items and hole_items:
        parts = []
        for idx, h in enumerate(hole_items):
            part = PartGeometry(name=f"Part {idx + 1} (Circle)", part_id=idx + 1)
            part.doc = doc
            part.outer_poly = h['poly']
            minx, miny, maxx, maxy = h['bbox'].bounds
            part.bounds = (minx, miny, maxx, maxy)
            part.width = maxx - minx
            part.height = maxy - miny
            part.area = math.pi * (h['radius'] ** 2)
            part.total_perimeter = 2 * math.pi * h['radius']
            part.dxf_entities = [h['entity']]
            parts.append(part)
        return parts

    if not contour_items:
        raise ValueError("No cutting geometry detected in this DXF file!")

    # 4. Cluster contour lines by spatial proximity (distance <= 3.0 mm)
    clusters = []
    for idx, item in enumerate(contour_items):
        matched = []
        b_exp = item['bbox'].buffer(3.0)
        for c_idx, cl in enumerate(clusters):
            if any(b_exp.intersects(contour_items[o_idx]['bbox']) for o_idx in cl):
                matched.append(c_idx)
        if not matched:
            clusters.append([idx])
        elif len(matched) == 1:
            clusters[matched[0]].append(idx)
        else:
            new_cl = [idx]
            for c_i in sorted(matched, reverse=True):
                new_cl.extend(clusters.pop(c_i))
            clusters.append(new_cl)

    # Sort clusters from left to right (X ascending) to match visual CAD layout
    clusters.sort(key=lambda cl: min(contour_items[i]['bbox'].bounds[0] for i in cl))

    parts = []
    for idx, cl in enumerate(clusters):
        cl_items = [contour_items[i] for i in cl]
        cl_lines = [l for it in cl_items for l in it['lines']]
        cl_ents = [it['entity'] for it in cl_items]

        # Snap endpoints with 1.5mm tolerance to heal undershoots and drafting micro-gaps
        pts = []
        for l in cl_lines:
            coords = list(l.coords)
            pts.append(coords[0])
            pts.append(coords[-1])
        
        snap_tol = 1.5
        c_pts = []
        for p in pts:
            m = False
            for cp in c_pts:
                if math.hypot(p[0] - cp[0][0], p[1] - cp[0][1]) <= snap_tol:
                    cp[1].append(p)
                    cp[0] = (sum(pt[0] for pt in cp[1])/len(cp[1]), sum(pt[1] for pt in cp[1])/len(cp[1]))
                    m = True
                    break
            if not m:
                c_pts.append([(p[0], p[1]), [p]])

        def get_snapped(p):
            for cp in c_pts:
                if math.hypot(p[0] - cp[0][0], p[1] - cp[0][1]) <= snap_tol:
                    return cp[0]
            return p

        snapped_lines = []
        for l in cl_lines:
            coords = list(l.coords)
            n_coords = [get_snapped(coords[0])]
            for mid in coords[1:-1]:
                n_coords.append(mid)
            n_coords.append(get_snapped(coords[-1]))
            if len(n_coords) >= 2 and (n_coords[0] != n_coords[-1] or len(n_coords) > 2):
                snapped_lines.append(LineString(n_coords))

        # Reconstruct closed loop(s)
        merged = unary_union(snapped_lines)
        polys = list(polygonize(merged))
        outer_shell = None
        if polys:
            u = unary_union(polys)
            if isinstance(u, Polygon):
                outer_shell = u
            elif isinstance(u, MultiPolygon):
                outer_shell = max(u.geoms, key=lambda p: p.area)
        
        # Robust fallback: convex hull of all line points
        if not outer_shell:
            all_pts = [p for l in cl_lines for p in l.coords]
            if len(all_pts) >= 3:
                outer_shell = MultiPoint(all_pts).convex_hull

        if not outer_shell or outer_shell.area <= 0.01:
            continue

        minx, miny, maxx, maxy = outer_shell.bounds
        part_box = box(minx - 1.0, miny - 1.0, maxx + 1.0, maxy + 1.0)

        # Match holes inside this part
        part_holes = []
        for h in hole_items:
            if part_box.contains(Point(h['center'])):
                part_holes.append(h['poly'])
                cl_ents.append(h['entity'])

        # Build Polygon with interior cutouts
        if part_holes:
            try:
                outer_poly = Polygon(outer_shell.exterior.coords, [h.exterior.coords for h in part_holes])
            except Exception:
                outer_poly = outer_shell
        else:
            outer_poly = outer_shell

        part = PartGeometry(name=f"Part {idx + 1}", part_id=idx + 1)
        part.doc = doc
        part.outer_poly = outer_poly
        part.holes = part_holes
        part.bounds = (minx, miny, maxx, maxy)
        part.width = maxx - minx
        part.height = maxy - miny
        part.area = max(0.01, outer_poly.area)

        # Match closest quantity text (e.g. Q10, Q9...)
        cx = (minx + maxx) / 2.0
        cy = (miny + maxy) / 2.0
        best_text = None
        min_dist = float('inf')
        
        for t in texts:
            dx = abs(t['x'] - cx)
            dy = abs(t['y'] - cy)
            
            # Text is vertically aligned with this part's column
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

        # Calculate cutting perimeter
        part_cut_length = 0.0
        for e in cl_ents:
            try:
                etype = e.dxftype()
                if etype == 'LINE':
                    p1 = (e.dxf.start.x, e.dxf.start.y)
                    p2 = (e.dxf.end.x, e.dxf.end.y)
                    part_cut_length += math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                elif etype == 'CIRCLE':
                    part_cut_length += 2 * math.pi * e.dxf.radius
                elif etype == 'ARC':
                    r = e.dxf.radius
                    sa = math.radians(e.dxf.start_angle)
                    ea = math.radians(e.dxf.end_angle)
                    sp = (ea - sa) % (2 * math.pi)
                    part_cut_length += r * sp
                else:
                    p = make_path(e)
                    pts = [(v.x, v.y) for v in p.flattening(distance=0.1)]
                    for i in range(len(pts) - 1):
                        part_cut_length += math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
            except Exception:
                pass

        part.dxf_entities = cl_ents
        part.total_perimeter = part_cut_length if part_cut_length > 0 else (2 * (part.width + part.height))
        parts.append(part)

    if not parts:
        raise ValueError("No valid parts could be reconstructed from this DXF file!")

    return parts

def parse_dxf(dxf_input):
    """Backwards-compatible wrapper returning the primary or first part."""
    parts = parse_dxf_multi(dxf_input)
    if not parts:
        raise ValueError("No parts detected in DXF")
    return parts[0]
