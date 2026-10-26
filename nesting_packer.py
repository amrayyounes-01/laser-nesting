import math
import copy
import ezdxf
from ezdxf.math import Vec3, Matrix44
from shapely.geometry import Polygon, MultiPolygon, Point, box
from shapely.affinity import translate, rotate

MARKET_SHEETS = [
    {"name": "1000 × 2000 mm (1 × 2 m)", "w": 2000.0, "h": 1000.0, "area_m2": 2.0},
    {"name": "1250 × 2500 mm (1.25 × 2.5 m)", "w": 2500.0, "h": 1250.0, "area_m2": 3.125},
    {"name": "1500 × 3000 mm (1.5 × 3 m)", "w": 3000.0, "h": 1500.0, "area_m2": 4.5},
]

class PlacedPart:
    def __init__(self, part_index, sheet_index, x, y, rotation, width, height, polygon, part_geom=None, label=""):
        self.part_index = part_index
        self.sheet_index = sheet_index
        self.x = x  # lower-left X on sheet
        self.y = y  # lower-left Y on sheet
        self.rotation = rotation  # 0 or 90 degrees
        self.width = width
        self.height = height
        self.polygon = polygon
        self.part_geom = part_geom
        self.label = label

class SheetResult:
    def __init__(self, sheet_index, width, height, sheet_name=None, is_remnant=False):
        self.sheet_index = sheet_index
        self.width = float(width)
        self.height = float(height)
        self.sheet_name = sheet_name or f"Sheet {sheet_index}"
        self.is_remnant = is_remnant
        self.placed_parts = []
        self.utilization_pct = 0.0
        self.scrap_pct = 100.0

def calculate_minimum_remnant(part_geom, quantity, spacing=6.0, margin=15.0, allow_rotation=True):
    """
    Calculates the minimum bounding offcut plate for a single part repeated quantity times.
    """
    minx, miny, maxx, maxy = part_geom.bounds
    pw = maxx - minx
    ph = maxy - miny
    if pw <= 0 or ph <= 0 or quantity <= 0:
        return None

    rotations = [0]
    if allow_rotation:
        rotations.append(90)

    best = None
    for rot in rotations:
        w_part = pw if rot == 0 else ph
        h_part = ph if rot == 0 else pw
        eff_w = w_part + spacing
        eff_h = h_part + spacing
        
        for c in range(1, quantity + 1):
            r = math.ceil(quantity / c)
            width = c * eff_w - spacing + 2 * margin
            height = r * eff_h - spacing + 2 * margin
            area = width * height
            aspect = max(width, height) / max(1.0, min(width, height))
            score = area * (1.0 + 0.05 * max(0.0, aspect - 2.2))
            
            if best is None or score < best['score']:
                best = {
                    'width': round(width, 1),
                    'height': round(height, 1),
                    'area_m2': round(area / 1e6, 3),
                    'cols': c,
                    'rows': r,
                    'rotation': rot,
                    'score': score
                }
    return best

def calculate_multi_part_remnant(parts_list, spacing=6.0, margin=15.0, allow_rotation=True):
    """
    Estimates the minimum offcut plate needed to fit an assortment of multiple parts.
    """
    items = []
    for p in parts_list:
        for _ in range(p.quantity):
            items.append(p)
            
    if not items:
        return None

    total_net_area = sum(p.area for p in items)
    # Estimate dimensions based on area with 1.6:1 aspect ratio and ~70% packing efficiency
    est_material_area = total_net_area / 0.72
    aspect = 1.6
    est_h = math.sqrt(est_material_area / aspect)
    est_w = est_h * aspect

    # Test candidate bounding box dimensions
    best_res = None
    for mult in [0.9, 1.0, 1.15, 1.3, 1.5, 1.8]:
        w_test = math.ceil((est_w * mult) / 50.0) * 50.0 + 2 * margin
        h_test = math.ceil((est_h * mult) / 50.0) * 50.0 + 2 * margin
        engine = NestingEngine(sheet_width=w_test, sheet_height=h_test, part_spacing=spacing, sheet_margin=margin, allow_rotation=allow_rotation)
        sheets = engine._pack_items_shelf(items, w_test, h_test)
        if len(sheets) == 1 and len(sheets[0].placed_parts) == len(items):
            area = w_test * h_test
            if best_res is None or area < best_res['area']:
                best_res = {
                    'width': w_test,
                    'height': h_test,
                    'area': area,
                    'area_m2': round(area / 1e6, 3)
                }
    return best_res

class NestingEngine:
    def __init__(self, 
                 sheet_width=3000.0, 
                 sheet_height=1500.0, 
                 part_spacing=6.0, 
                 sheet_margin=15.0, 
                 kerf=0.2, 
                 allow_rotation=True):
        self.sheet_w = float(sheet_width)
        self.sheet_h = float(sheet_height)
        self.spacing = float(part_spacing)
        self.margin = float(sheet_margin)
        self.kerf = float(kerf)
        self.allow_rotation = allow_rotation

    def pack(self, part_geom, total_quantity):
        """Single-part manual packing."""
        return self._pack_single_fixed(part_geom, total_quantity, self.sheet_w, self.sheet_h)

    def pack_auto_procurement(self, part_geom, total_quantity, target_mode="market_sheets"):
        """Single-part auto market optimization."""
        return self.pack_multi_auto_procurement([part_geom], target_mode=target_mode)

    def pack_multi_parts_fixed(self, parts_list, sheet_w=None, sheet_h=None, sheet_name=None, is_remnant=False):
        """Packs a list of heterogeneous parts onto fixed-size sheet(s)."""
        sw = float(sheet_w or self.sheet_w)
        sh = float(sheet_h or self.sheet_h)

        items = []
        for p in parts_list:
            for i in range(p.quantity):
                items.append(p)

        sheets = self._pack_items_shelf(items, sw, sh, sheet_name=sheet_name, is_remnant=is_remnant)
        return self._build_multi_summary(parts_list, items, sheets, sw, sh)

    def pack_multi_auto_procurement(self, parts_list, target_mode="market_sheets"):
        """
        Smart Workshop Optimization for Multi-Part Assortments:
        - Evaluates market standard sheets (1x2m, 1.25x2.5m, 1.5x3m).
        - Selects the smallest sheet that accommodates all parts, or packs onto multiple sheets.
        - Computes minimum offcut (remnant) dimensions for the entire job.
        """
        items = []
        for p in parts_list:
            for i in range(p.quantity):
                items.append(p)

        total_placed_needed = len(items)
        if total_placed_needed == 0:
            raise ValueError("Total quantity is 0!")

        # 1. Calculate minimum remnant
        if len(parts_list) == 1:
            remnant = calculate_minimum_remnant(parts_list[0], total_placed_needed, self.spacing, self.margin, self.allow_rotation)
        else:
            remnant = calculate_multi_part_remnant(parts_list, self.spacing, self.margin, self.allow_rotation)

        if target_mode == "remnant_only" and remnant:
            sheets = self._pack_items_shelf(
                items, remnant['width'], remnant['height'], 
                sheet_name=f"Remnant Plate ({int(remnant['width'])} × {int(remnant['height'])} mm)", 
                is_remnant=True
            )
            res = self._build_multi_summary(parts_list, items, sheets, remnant['width'], remnant['height'])
            res['remnant_info'] = remnant
            res['procurement_advice'] = (
                f"💡 Nested on minimum possible offcut plate: ({int(remnant['width'])} × {int(remnant['height'])} mm) "
                f"requiring only {remnant['area_m2']} m² without cutting a new full sheet!"
            )
            return res

        # 2. Check if entire order fits on a single market sheet (ascending by size)
        for s in MARKET_SHEETS:
            sheets = self._pack_items_shelf(items, s['w'], s['h'], sheet_name=f"Standard Sheet {s['name']}")
            if len(sheets) == 1 and len(sheets[0].placed_parts) == total_placed_needed:
                res = self._build_multi_summary(parts_list, items, sheets, s['w'], s['h'])
                res['remnant_info'] = remnant
                rem_text = f"({int(remnant['width'])} × {int(remnant['height'])} mm - {remnant['area_m2']} m²)" if remnant else ""
                res['procurement_advice'] = (
                    f"✅ Procurement Recommendation: Order 1 sheet of {s['name']} only. "
                    f"All {total_placed_needed} parts fit inside, avoiding oversized sheets.\n\n"
                    f"♻️ Workshop Remnant Alternative: If you have a scrap/offcut plate of at least {rem_text}, "
                    f"you can cut this entire job from it and save opening any new sheet!"
                )
                return res

        # 3. Exceeds single sheet: Multi-sheet strategy with primary 1500x3000 mm
        bulk_sheet = MARKET_SHEETS[2]  # 1500x3000
        sheets = self._pack_items_shelf(items, bulk_sheet['w'], bulk_sheet['h'], sheet_name=f"Sheet {bulk_sheet['name']}")
        res = self._build_multi_summary(parts_list, items, sheets, bulk_sheet['w'], bulk_sheet['h'])
        res['remnant_info'] = remnant
        
        num_sheets = len(sheets)
        rem_text = f"({int(remnant['width'])} × {int(remnant['height'])} mm)" if remnant else ""
        res['procurement_advice'] = (
            f"✅ Workshop Procurement Plan: Order {num_sheets} sheet(s) of 1500 × 3000 mm.\n"
            f"Total parts: {total_placed_needed} distributed across {num_sheets} sheet(s).\n\n"
            f"♻️ Workshop Check: Total minimum offcut required is ~{rem_text} if using remnants."
        )
        return res

    def _pack_items_shelf(self, items, sheet_w, sheet_h, sheet_name=None, is_remnant=False):
        """
        Shelf & Best-Fit decreasing 2D packing algorithm for heterogeneous items.
        """
        usable_w = sheet_w - 2 * self.margin
        usable_h = sheet_h - 2 * self.margin
        
        if usable_w <= 0 or usable_h <= 0:
            raise ValueError("Sheet margin exceeds sheet dimensions!")

        # Sort items by height descending (tallest first)
        def sort_key(it):
            w = it.width
            h = it.height
            return max(w, h) if self.allow_rotation else h

        sorted_items = sorted(items, key=sort_key, reverse=True)

        sheets = []
        current_sheet_idx = 1
        current_sh = SheetResult(current_sheet_idx, sheet_w, sheet_h, sheet_name=sheet_name or f"Sheet {current_sheet_idx}", is_remnant=is_remnant)
        shelves = []  # list of {'y': float, 'height': float, 'current_x': float}
        sheets.append(current_sh)

        part_inst_counter = {}

        for item in sorted_items:
            pw = item.width
            ph = item.height
            base_poly = translate(item.outer_poly, xoff=-item.bounds[0], yoff=-item.bounds[1])

            # Orientations to test
            orientations = [(pw, ph, 0, base_poly)]
            if self.allow_rotation and abs(pw - ph) > 0.1:
                rot_poly = rotate(base_poly, 90, origin=(0, 0))
                rot_aligned = translate(rot_poly, xoff=-rot_poly.bounds[0], yoff=-rot_poly.bounds[1])
                orientations.append((ph, pw, 90, rot_aligned))

            placed = False
            # 1. Try placing in existing shelves on the active sheet
            for sh in shelves:
                for w, h, rot, poly_rot in orientations:
                    eff_w = w + self.spacing
                    if (sh['current_x'] + eff_w - self.spacing <= usable_w) and (h <= sh['height']):
                        pos_x = self.margin + sh['current_x']
                        pos_y = self.margin + sh['y']
                        sh['current_x'] += eff_w
                        
                        part_inst_counter[item.name] = part_inst_counter.get(item.name, 0) + 1
                        lbl = f"{item.name} #{part_inst_counter[item.name]}"
                        
                        placed_poly = translate(poly_rot, xoff=pos_x, yoff=pos_y)
                        current_sh.placed_parts.append(PlacedPart(
                            part_index=len(current_sh.placed_parts) + 1,
                            sheet_index=current_sh.sheet_index,
                            x=pos_x,
                            y=pos_y,
                            rotation=rot,
                            width=w,
                            height=h,
                            polygon=placed_poly,
                            part_geom=item,
                            label=lbl
                        ))
                        placed = True
                        break
                if placed:
                    break

            if placed:
                continue

            # 2. Try opening a new shelf on the active sheet
            current_top = sum(sh['height'] + self.spacing for sh in shelves)
            for w, h, rot, poly_rot in orientations:
                eff_w = w + self.spacing
                eff_h = h + self.spacing
                if (current_top + eff_h - self.spacing <= usable_h) and (eff_w - self.spacing <= usable_w):
                    new_sh = {'y': current_top, 'height': h, 'current_x': eff_w}
                    shelves.append(new_sh)
                    pos_x = self.margin
                    pos_y = self.margin + current_top
                    
                    part_inst_counter[item.name] = part_inst_counter.get(item.name, 0) + 1
                    lbl = f"{item.name} #{part_inst_counter[item.name]}"
                    
                    placed_poly = translate(poly_rot, xoff=pos_x, yoff=pos_y)
                    current_sh.placed_parts.append(PlacedPart(
                        part_index=len(current_sh.placed_parts) + 1,
                        sheet_index=current_sh.sheet_index,
                        x=pos_x,
                        y=pos_y,
                        rotation=rot,
                        width=w,
                        height=h,
                        polygon=placed_poly,
                        part_geom=item,
                        label=lbl
                    ))
                    placed = True
                    break

            if placed:
                continue

            # 3. Need a new sheet
            current_sheet_idx += 1
            current_sh = SheetResult(current_sheet_idx, sheet_w, sheet_h, sheet_name=sheet_name or f"Sheet {current_sheet_idx}", is_remnant=is_remnant)
            shelves = []
            sheets.append(current_sh)

            for w, h, rot, poly_rot in orientations:
                eff_w = w + self.spacing
                eff_h = h + self.spacing
                if (eff_h - self.spacing <= usable_h) and (eff_w - self.spacing <= usable_w):
                    new_sh = {'y': 0.0, 'height': h, 'current_x': eff_w}
                    shelves.append(new_sh)
                    pos_x = self.margin
                    pos_y = self.margin
                    
                    part_inst_counter[item.name] = part_inst_counter.get(item.name, 0) + 1
                    lbl = f"{item.name} #{part_inst_counter[item.name]}"
                    
                    placed_poly = translate(poly_rot, xoff=pos_x, yoff=pos_y)
                    current_sh.placed_parts.append(PlacedPart(
                        part_index=len(current_sh.placed_parts) + 1,
                        sheet_index=current_sh.sheet_index,
                        x=pos_x,
                        y=pos_y,
                        rotation=rot,
                        width=w,
                        height=h,
                        polygon=placed_poly,
                        part_geom=item,
                        label=lbl
                    ))
                    placed = True
                    break

        # Calculate metrics for each sheet
        sheet_area = sheet_w * sheet_h
        for s in sheets:
            parts_area = sum(p.part_geom.area for p in s.placed_parts if p.part_geom)
            s.utilization_pct = (parts_area / sheet_area) * 100.0 if sheet_area > 0 else 0.0
            s.scrap_pct = 100.0 - s.utilization_pct

        return sheets

    def _build_multi_summary(self, parts_list, items, sheets, sheet_w, sheet_h):
        total_items = len(items)
        total_placed = sum(len(s.placed_parts) for s in sheets)
        total_parts_net_area = sum(p.area for p in items)
        total_material_area = sum(s.width * s.height for s in sheets)
        
        overall_util = (total_parts_net_area / total_material_area) * 100.0 if total_material_area > 0 else 0.0
        overall_scrap = 100.0 - overall_util
        total_cut_m = sum(p.total_perimeter for p in items) / 1000.0

        return {
            "requested_quantity": total_items,
            "placed_quantity": total_placed,
            "sheets_needed": len(sheets),
            "parts_per_sheet": f"{total_placed} parts across {len(sheets)} sheet(s)",
            "sheet_size_mm": f"{int(sheet_w)} × {int(sheet_h)}",
            "spacing_mm": self.spacing,
            "margin_mm": self.margin,
            "kerf_mm": self.kerf,
            "overall_utilization_pct": round(overall_util, 2),
            "overall_scrap_pct": round(overall_scrap, 2),
            "total_cut_length_meters": round(total_cut_m, 2),
            "sheets": sheets
        }

    def _pack_single_fixed(self, part_geom, quantity, sheet_w, sheet_h):
        return self.pack_multi_parts_fixed([part_geom], sheet_w, sheet_h)

    def export_nested_dxf(self, original_dxf_doc, sheets, output_path):
        """
        Exports the placed parts onto standard DXF sheet(s).
        Preserves original CAD geometry for every individual part,
        and adds part identification text labels (e.g. P1 #1, P2 #1).
        """
        out_doc = ezdxf.new('R2010')
        out_msp = out_doc.modelspace()

        out_doc.layers.add(name='SHEET_FRAME', color=1)    # Red (border)
        out_doc.layers.add(name='SHEET_MARGIN', color=5)   # Blue (usable margin)
        out_doc.layers.add(name='LASER_CUT', color=3)      # Green (part contours)
        out_doc.layers.add(name='LASER_HOLES', color=2)    # Yellow (inner cutouts)
        out_doc.layers.add(name='TEXT_INFO', color=7)      # White (annotations)

        current_offset_x = 0.0

        for s_idx, sheet in enumerate(sheets):
            sw = sheet.width
            sh = sheet.height
            
            # 1. Sheet outer frame
            out_msp.add_lwpolyline([
                (current_offset_x, 0),
                (current_offset_x + sw, 0),
                (current_offset_x + sw, sh),
                (current_offset_x, sh),
                (current_offset_x, 0)
            ], dxfattribs={'layer': 'SHEET_FRAME', 'color': 1})

            # 2. Usable margin box
            out_msp.add_lwpolyline([
                (current_offset_x + self.margin, self.margin),
                (current_offset_x + sw - self.margin, self.margin),
                (current_offset_x + sw - self.margin, sh - self.margin),
                (current_offset_x + self.margin, sh - self.margin),
                (current_offset_x + self.margin, self.margin)
            ], dxfattribs={'layer': 'SHEET_MARGIN', 'color': 5})

            # 3. Sheet info banner
            title_text = getattr(sheet, 'sheet_name', f"SHEET {sheet.sheet_index}")
            info_str = f"{title_text} ({int(sw)}x{int(sh)}mm) | Qty: {len(sheet.placed_parts)} | Yield: {sheet.utilization_pct:.1f}% | Scrap: {sheet.scrap_pct:.1f}%"
            out_msp.add_text(info_str, dxfattribs={
                'layer': 'TEXT_INFO',
                'height': 30.0,
                'insert': (current_offset_x + self.margin, sh + 40.0)
            })

            # 4. Replicate each placed part instance
            for placed in sheet.placed_parts:
                target_x = current_offset_x + placed.x
                target_y = placed.y
                rot = placed.rotation
                pgeom = placed.part_geom
                
                # Part label on cut sheet
                if placed.label:
                    out_msp.add_text(placed.label, dxfattribs={
                        'layer': 'TEXT_INFO',
                        'height': 12.0,
                        'insert': (target_x + 8.0, target_y + 8.0)
                    })
                
                if pgeom and pgeom.dxf_entities:
                    src_minx = pgeom.bounds[0]
                    src_miny = pgeom.bounds[1]
                    pw = pgeom.width
                    ph = pgeom.height

                    for entity in pgeom.dxf_entities:
                        etype = entity.dxftype()
                        if etype == 'LINE':
                            p1 = self._transform_pt(entity.dxf.start.x - src_minx, entity.dxf.start.y - src_miny, rot, target_x, target_y, pw, ph)
                            p2 = self._transform_pt(entity.dxf.end.x - src_minx, entity.dxf.end.y - src_miny, rot, target_x, target_y, pw, ph)
                            out_msp.add_line(p1, p2, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                            
                        elif etype == 'LWPOLYLINE':
                            pts = entity.get_points()
                            t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y, pw, ph) for p in pts]
                            out_msp.add_lwpolyline(t_pts, close=entity.is_closed, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                            
                        elif etype == 'POLYLINE':
                            pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                            t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y, pw, ph) for p in pts]
                            out_msp.add_lwpolyline(t_pts, close=entity.is_closed, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                            
                        elif etype in ('SPLINE', 'ELLIPSE'):
                            from ezdxf.path import make_path
                            p = make_path(entity)
                            pts = [(v.x, v.y) for v in p.flattening(distance=0.1)]
                            t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y, pw, ph) for p in pts]
                            out_msp.add_lwpolyline(t_pts, close=False, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                            
                        elif etype == 'CIRCLE':
                            cx_rel = entity.dxf.center.x - src_minx
                            cy_rel = entity.dxf.center.y - src_miny
                            c_pt = self._transform_pt(cx_rel, cy_rel, rot, target_x, target_y, pw, ph)
                            out_msp.add_circle(c_pt, radius=entity.dxf.radius, dxfattribs={'layer': 'LASER_HOLES', 'color': 2})
                            
                        elif etype == 'ARC':
                            cx_rel = entity.dxf.center.x - src_minx
                            cy_rel = entity.dxf.center.y - src_miny
                            c_pt = self._transform_pt(cx_rel, cy_rel, rot, target_x, target_y, pw, ph)
                            new_start = (entity.dxf.start_angle + rot) % 360.0
                            new_end = (entity.dxf.end_angle + rot) % 360.0
                            out_msp.add_arc(c_pt, radius=entity.dxf.radius, start_angle=new_start, end_angle=new_end, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                else:
                    # Fallback to polygon coords (both exterior perimeter and interior holes)
                    if placed.polygon and not placed.polygon.is_empty:
                        for g in ([placed.polygon] if isinstance(placed.polygon, Polygon) else placed.polygon.geoms):
                            x, y = g.exterior.xy
                            out_msp.add_lwpolyline(list(zip(x, y)), close=True, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                            for interior in g.interiors:
                                ix, iy = interior.xy
                                out_msp.add_lwpolyline(list(zip(ix, iy)), close=True, dxfattribs={'layer': 'LASER_HOLES', 'color': 2})

            current_offset_x += sw + 300.0

        out_doc.saveas(output_path)
        return output_path

    @staticmethod
    def _transform_pt(x, y, rotation_deg, target_x, target_y, pw=0.0, ph=0.0):
        if rotation_deg == 90:
            rx = ph - y
            ry = x
            return (target_x + rx, target_y + ry)
        elif rotation_deg == 0:
            return (target_x + x, target_y + y)
        else:
            rad = math.radians(rotation_deg)
            rx = x * math.cos(rad) - y * math.sin(rad)
            ry = x * math.sin(rad) + y * math.cos(rad)
            return (target_x + rx, target_y + ry)
