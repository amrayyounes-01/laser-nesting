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
    def __init__(self, part_index, sheet_index, x, y, rotation, width, height, polygon):
        self.part_index = part_index
        self.sheet_index = sheet_index
        self.x = x  # lower-left X on sheet
        self.y = y  # lower-left Y on sheet
        self.rotation = rotation  # 0 or 90 degrees
        self.width = width
        self.height = height
        self.polygon = polygon

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
    Calculates the absolute minimum bounding offcut / remnant plate
    required to produce the given quantity, avoiding cutting a full new sheet.
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
        
        self.usable_w = max(0.0, self.sheet_w - 2 * self.margin)
        self.usable_h = max(0.0, self.sheet_h - 2 * self.margin)

    def pack(self, part_geom, total_quantity):
        """Standard single-sheet-size packing (manual mode)."""
        return self._pack_fixed_sheet_size(part_geom, total_quantity, self.sheet_w, self.sheet_h)

    def pack_auto_procurement(self, part_geom, total_quantity, target_mode="market_sheets"):
        """
        Smart Workshop Optimization Mode:
        - If 'remnant_only': packs on the exact minimum offcut.
        - If 'market_sheets': automatically selects the smallest standard market sheet(s)
          and computes offcut recommendations for leftovers.
        """
        minx, miny, maxx, maxy = part_geom.bounds
        pw = maxx - minx
        ph = maxy - miny
        
        # 1. Calculate minimum remnant (offcut)
        remnant = calculate_minimum_remnant(part_geom, total_quantity, self.spacing, self.margin, self.allow_rotation)

        if target_mode == "remnant_only" and remnant:
            res = self._pack_fixed_sheet_size(
                part_geom, total_quantity, 
                sheet_w=remnant['width'], 
                sheet_h=remnant['height'], 
                sheet_name=f"Remnant Plate ({int(remnant['width'])} × {int(remnant['height'])} mm)",
                is_remnant=True
            )
            res['remnant_info'] = remnant
            res['procurement_advice'] = (
                f"💡 Nested on minimum possible offcut plate: ({int(remnant['width'])} × {int(remnant['height'])} mm) "
                f"requiring only {remnant['area_m2']} m² without cutting a new full sheet!"
            )
            return res

        # 2. Market Sheet Selection Strategy
        single_opts = []
        for s in MARKET_SHEETS:
            cap = self._get_sheet_capacity(part_geom, s['w'], s['h'])
            if cap >= total_quantity:
                single_opts.append((s, cap))

        if single_opts:
            best_sheet, cap = single_opts[0]
            res = self._pack_fixed_sheet_size(
                part_geom, total_quantity, 
                sheet_w=best_sheet['w'], 
                sheet_h=best_sheet['h'], 
                sheet_name=f"Standard Sheet {best_sheet['name']}"
            )
            res['remnant_info'] = remnant
            res['procurement_advice'] = (
                f"✅ Procurement Recommendation: Order 1 sheet of {best_sheet['name']} only (capacity: {cap} parts). "
                f"No need to purchase a larger sheet, minimizing material cost.\n\n"
                f"♻️ Workshop Remnant Alternative: If you have a scrap/offcut plate measuring at least "
                f"({int(remnant['width'])} × {int(remnant['height'])} mm - {remnant['area_m2']} m²), "
                f"you can run this entire order from it and save opening any new sheet!"
            )
            return res
        else:
            # Multi-sheet strategy: bulk 1500x3000 sheets
            bulk_sheet = MARKET_SHEETS[2]  # 1500x3000
            cap_bulk = self._get_sheet_capacity(part_geom, bulk_sheet['w'], bulk_sheet['h'])
            if cap_bulk == 0:
                raise ValueError("Part dimensions exceed standard market sheet sizes!")
            
            full_sheets_count = total_quantity // cap_bulk
            rem_qty = total_quantity % cap_bulk

            all_sheets = []
            rem_remnant_info = None
            
            placed_so_far = 0
            for i in range(full_sheets_count):
                sheet_idx = i + 1
                sh_res = self._simulate_sheet_parts(
                    part_geom, cap_bulk, bulk_sheet['w'], bulk_sheet['h'], 
                    sheet_idx=sheet_idx, 
                    sheet_name=f"Primary Sheet {bulk_sheet['name']} ({sheet_idx}/{full_sheets_count + (1 if rem_qty > 0 else 0)})"
                )
                all_sheets.append(sh_res)
                placed_so_far += len(sh_res.placed_parts)

            rem_sheet_name = ""
            if rem_qty > 0:
                rem_sheet_choice = bulk_sheet
                for s in MARKET_SHEETS:
                    if self._get_sheet_capacity(part_geom, s['w'], s['h']) >= rem_qty:
                        rem_sheet_choice = s
                        break
                        
                rem_remnant_info = calculate_minimum_remnant(part_geom, rem_qty, self.spacing, self.margin, self.allow_rotation)
                rem_sheet_idx = len(all_sheets) + 1
                rem_sh_res = self._simulate_sheet_parts(
                    part_geom, rem_qty, rem_sheet_choice['w'], rem_sheet_choice['h'], 
                    sheet_idx=rem_sheet_idx, 
                    sheet_name=f"Remainder Sheet {rem_sheet_choice['name']} ({rem_sheet_idx}/{rem_sheet_idx})"
                )
                all_sheets.append(rem_sh_res)
                placed_so_far += len(rem_sh_res.placed_parts)
                rem_sheet_name = rem_sheet_choice['name']

            total_placed = placed_so_far
            total_sheet_material_area = sum(s.width * s.height for s in all_sheets)
            total_parts_area = total_placed * part_geom.area
            overall_util = (total_parts_area / total_sheet_material_area) * 100.0 if total_sheet_material_area > 0 else 0.0
            overall_scrap = 100.0 - overall_util
            total_cut_m = (total_placed * part_geom.total_perimeter) / 1000.0

            if rem_qty > 0 and rem_remnant_info:
                advice = (
                    f"✅ Workshop Procurement Plan:\n"
                    f"• Order {full_sheets_count} sheet(s) of 1500 × 3000 mm.\n"
                    f"• For remainder ({rem_qty} parts): Order 1 smaller sheet of {rem_sheet_name} instead of a full 3m sheet.\n\n"
                    f"♻️ Maximum Savings: If an offcut plate of at least "
                    f"({int(rem_remnant_info['width'])} × {int(rem_remnant_info['height'])} mm) is available in the workshop, "
                    f"you can cut the remaining {rem_qty} parts from it and save purchasing the extra sheet completely!"
                )
            else:
                advice = f"✅ Workshop Procurement Plan: Order exactly {full_sheets_count} sheet(s) of 1500 × 3000 mm (exact fit with no remainder)."

            return {
                "requested_quantity": total_quantity,
                "placed_quantity": total_placed,
                "sheets_needed": len(all_sheets),
                "parts_per_sheet": f"Distributed across {len(all_sheets)} sheet(s)",
                "sheet_size_mm": f"{len(all_sheets)} sheet(s) (optimized sizes)",
                "part_size_mm": f"{pw:.1f} × {ph:.1f}",
                "spacing_mm": self.spacing,
                "margin_mm": self.margin,
                "kerf_mm": self.kerf,
                "overall_utilization_pct": round(overall_util, 2),
                "overall_scrap_pct": round(overall_scrap, 2),
                "total_cut_length_meters": round(total_cut_m, 2),
                "part_net_area_cm2": round(part_geom.area / 100.0, 2),
                "sheets": all_sheets,
                "remnant_info": remnant,
                "procurement_advice": advice
            }

    def _get_sheet_capacity(self, part_geom, sw, sh):
        minx, miny, maxx, maxy = part_geom.bounds
        pw = maxx - minx
        ph = maxy - miny
        uw = max(0.0, sw - 2 * self.margin)
        uh = max(0.0, sh - 2 * self.margin)
        
        c0 = int((uw + self.spacing) // (pw + self.spacing))
        r0 = int((uh + self.spacing) // (ph + self.spacing))
        cap0 = max(0, c0 * r0)
        
        cap90 = 0
        if self.allow_rotation:
            c90 = int((uw + self.spacing) // (ph + self.spacing))
            r90 = int((uh + self.spacing) // (pw + self.spacing))
            cap90 = max(0, c90 * r90)
            
        return max(cap0, cap90)

    def _pack_fixed_sheet_size(self, part_geom, total_quantity, sheet_w, sheet_h, sheet_name=None, is_remnant=False):
        minx, miny, maxx, maxy = part_geom.bounds
        pw = maxx - minx
        ph = maxy - miny
        
        uw = max(0.0, sheet_w - 2 * self.margin)
        uh = max(0.0, sheet_h - 2 * self.margin)
        
        if uw <= 0 or uh <= 0:
            raise ValueError("Sheet margin is larger than the sheet dimensions!")

        base_poly = translate(part_geom.outer_poly, xoff=-minx, yoff=-miny)
        
        sheets_0, cap_0 = self._simulate_grid_shelf_custom(pw, ph, base_poly, total_quantity, 0, sheet_w, sheet_h, sheet_name, is_remnant)
        if self.allow_rotation:
            sheets_90, cap_90 = self._simulate_grid_shelf_custom(ph, pw, rotate(base_poly, 90, origin=(0,0)), total_quantity, 90, sheet_w, sheet_h, sheet_name, is_remnant)
            if cap_90 > cap_0:
                best_sheets = sheets_90
                parts_per_sheet = cap_90
            else:
                best_sheets = sheets_0
                parts_per_sheet = cap_0
        else:
            best_sheets = sheets_0
            parts_per_sheet = cap_0

        num_sheets = len(best_sheets)
        total_placed = sum(len(s.placed_parts) for s in best_sheets)
        sheet_area = sheet_w * sheet_h
        
        for s in best_sheets:
            sheet_parts_area = len(s.placed_parts) * part_geom.area
            s.utilization_pct = (sheet_parts_area / sheet_area) * 100.0
            s.scrap_pct = 100.0 - s.utilization_pct

        total_parts_area = total_placed * part_geom.area
        total_sheet_material_area = num_sheets * sheet_area
        overall_utilization = (total_parts_area / total_sheet_material_area) * 100.0 if total_sheet_material_area > 0 else 0.0
        overall_scrap = 100.0 - overall_utilization
        total_cut_length_m = (total_placed * part_geom.total_perimeter) / 1000.0

        return {
            "requested_quantity": total_quantity,
            "placed_quantity": total_placed,
            "sheets_needed": num_sheets,
            "parts_per_sheet": parts_per_sheet,
            "sheet_size_mm": f"{int(sheet_w)} × {int(sheet_h)}",
            "part_size_mm": f"{pw:.1f} × {ph:.1f}",
            "spacing_mm": self.spacing,
            "margin_mm": self.margin,
            "kerf_mm": self.kerf,
            "overall_utilization_pct": round(overall_utilization, 2),
            "overall_scrap_pct": round(overall_scrap, 2),
            "total_cut_length_meters": round(total_cut_length_m, 2),
            "part_net_area_cm2": round(part_geom.area / 100.0, 2),
            "sheets": best_sheets
        }

    def _simulate_sheet_parts(self, part_geom, qty, sheet_w, sheet_h, sheet_idx=1, sheet_name=None, is_remnant=False):
        minx, miny, maxx, maxy = part_geom.bounds
        pw = maxx - minx
        ph = maxy - miny
        base_poly = translate(part_geom.outer_poly, xoff=-minx, yoff=-miny)
        
        sheets_0, cap_0 = self._simulate_grid_shelf_custom(pw, ph, base_poly, qty, 0, sheet_w, sheet_h, sheet_name, is_remnant)
        if self.allow_rotation:
            sheets_90, cap_90 = self._simulate_grid_shelf_custom(ph, pw, rotate(base_poly, 90, origin=(0,0)), qty, 90, sheet_w, sheet_h, sheet_name, is_remnant)
            target_sheet = sheets_90[0] if (cap_90 > cap_0 and sheets_90) else (sheets_0[0] if sheets_0 else None)
        else:
            target_sheet = sheets_0[0] if sheets_0 else None
            
        if target_sheet:
            target_sheet.sheet_index = sheet_idx
            sh_area = sheet_w * sheet_h
            target_sheet.utilization_pct = (len(target_sheet.placed_parts) * part_geom.area / sh_area) * 100.0
            target_sheet.scrap_pct = 100.0 - target_sheet.utilization_pct
            return target_sheet
        else:
            return SheetResult(sheet_idx, sheet_w, sheet_h, sheet_name, is_remnant)

    def _simulate_grid_shelf_custom(self, pw, ph, oriented_poly, total_qty, rot_angle, sheet_w, sheet_h, sheet_name=None, is_remnant=False):
        uw = max(0.0, sheet_w - 2 * self.margin)
        uh = max(0.0, sheet_h - 2 * self.margin)
        
        eff_w = pw + self.spacing
        eff_h = ph + self.spacing

        cols = int((uw + self.spacing) // eff_w)
        rows = int((uh + self.spacing) // eff_h)
        cols = max(1, cols)
        rows = max(1, rows)
        
        if ((cols - 1) * eff_w + pw) > uw:
            cols = max(1, cols - 1)
        if ((rows - 1) * eff_h + ph) > uh:
            rows = max(1, rows - 1)

        parts_per_sheet = cols * rows
        if parts_per_sheet == 0:
            return [], 0

        sheets = []
        remaining_qty = total_qty
        sheet_idx = 0
        part_global_idx = 0

        minx, miny, maxx, maxy = oriented_poly.bounds
        poly_zero = translate(oriented_poly, xoff=-minx, yoff=-miny)

        while remaining_qty > 0:
            sheet_idx += 1
            sh_label = sheet_name or f"Sheet {sheet_idx}"
            sheet = SheetResult(sheet_idx, sheet_w, sheet_h, sheet_name=sh_label, is_remnant=is_remnant)
            
            for r in range(rows):
                for c in range(cols):
                    if remaining_qty <= 0:
                        break
                    part_global_idx += 1
                    pos_x = self.margin + (c * eff_w)
                    pos_y = self.margin + (r * eff_h)
                    
                    placed_poly = translate(poly_zero, xoff=pos_x, yoff=pos_y)
                    placed = PlacedPart(
                        part_index=part_global_idx,
                        sheet_index=sheet_idx,
                        x=pos_x,
                        y=pos_y,
                        rotation=rot_angle,
                        width=pw,
                        height=ph,
                        polygon=placed_poly
                    )
                    sheet.placed_parts.append(placed)
                    remaining_qty -= 1
                    
                if remaining_qty <= 0:
                    break
                    
            sheets.append(sheet)

        return sheets, parts_per_sheet

    def export_nested_dxf(self, original_dxf_doc, sheets, output_path):
        """
        Exports the placed parts onto standard DXF sheet(s).
        Dynamically handles different sheet sizes in the same job layout!
        """
        out_doc = ezdxf.new('R2010')
        out_msp = out_doc.modelspace()

        out_doc.layers.add(name='SHEET_FRAME', color=1)    # Red (border)
        out_doc.layers.add(name='SHEET_MARGIN', color=5)   # Blue (usable margin)
        out_doc.layers.add(name='LASER_CUT', color=3)      # Green (part contours)
        out_doc.layers.add(name='LASER_HOLES', color=2)    # Yellow (inner cutouts)
        out_doc.layers.add(name='TEXT_INFO', color=7)      # White

        src_msp = original_dxf_doc.modelspace()
        
        all_x, all_y = [], []
        for e in src_msp:
            if e.dxftype() in ('LINE', 'LWPOLYLINE', 'CIRCLE', 'ARC', 'POLYLINE', 'SPLINE'):
                try:
                    if e.dxftype() == 'LINE':
                        all_x.extend([e.dxf.start.x, e.dxf.end.x])
                        all_y.extend([e.dxf.start.y, e.dxf.end.y])
                    elif e.dxftype() == 'LWPOLYLINE':
                        pts = e.get_points()
                        all_x.extend([p[0] for p in pts])
                        all_y.extend([p[1] for p in pts])
                    elif e.dxftype() == 'POLYLINE':
                        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                        all_x.extend([p[0] for p in pts])
                        all_y.extend([p[1] for p in pts])
                    elif e.dxftype() == 'SPLINE':
                        pts = [(p[0], p[1]) for p in e.flattening(distance=0.1)]
                        all_x.extend([p[0] for p in pts])
                        all_y.extend([p[1] for p in pts])
                    elif e.dxftype() in ('CIRCLE', 'ARC'):
                        cx, cy = e.dxf.center.x, e.dxf.center.y
                        r = e.dxf.radius
                        all_x.extend([cx - r, cx + r])
                        all_y.extend([cy - r, cy + r])
                except Exception:
                    pass
                    
        src_minx = min(all_x) if all_x else 0.0
        src_miny = min(all_y) if all_y else 0.0

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

            # 3. Sheet info text
            title_text = getattr(sheet, 'sheet_name', f"SHEET {sheet.sheet_index}")
            info_str = f"{title_text} ({int(sw)}x{int(sh)}mm) | Qty: {len(sheet.placed_parts)} | Yield: {sheet.utilization_pct:.1f}% | Scrap: {sheet.scrap_pct:.1f}%"
            out_msp.add_text(info_str, dxfattribs={
                'layer': 'TEXT_INFO',
                'height': 35.0,
                'insert': (current_offset_x + self.margin, sh + 40.0)
            })

            # 4. Replicate parts
            for placed in sheet.placed_parts:
                target_x = current_offset_x + placed.x
                target_y = placed.y
                rot = placed.rotation
                
                for entity in src_msp:
                    etype = entity.dxftype()
                    if etype == 'LINE':
                        p1 = self._transform_pt(entity.dxf.start.x - src_minx, entity.dxf.start.y - src_miny, rot, target_x, target_y)
                        p2 = self._transform_pt(entity.dxf.end.x - src_minx, entity.dxf.end.y - src_miny, rot, target_x, target_y)
                        out_msp.add_line(p1, p2, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                        
                    elif etype == 'LWPOLYLINE':
                        pts = entity.get_points()
                        t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y) for p in pts]
                        out_msp.add_lwpolyline(t_pts, close=entity.is_closed, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                        
                    elif etype == 'POLYLINE':
                        pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                        t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y) for p in pts]
                        out_msp.add_lwpolyline(t_pts, close=entity.is_closed, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                        
                    elif etype == 'SPLINE':
                        pts = [(p[0], p[1]) for p in entity.flattening(distance=0.1)]
                        t_pts = [self._transform_pt(p[0] - src_minx, p[1] - src_miny, rot, target_x, target_y) for p in pts]
                        out_msp.add_lwpolyline(t_pts, close=entity.closed, dxfattribs={'layer': 'LASER_CUT', 'color': 3})
                        
                    elif etype == 'CIRCLE':
                        c_pt = self._transform_pt(entity.dxf.center.x - src_minx, entity.dxf.center.y - src_miny, rot, target_x, target_y)
                        out_msp.add_circle(c_pt, radius=entity.dxf.radius, dxfattribs={'layer': 'LASER_HOLES', 'color': 2})
                        
                    elif etype == 'ARC':
                        c_pt = self._transform_pt(entity.dxf.center.x - src_minx, entity.dxf.center.y - src_miny, rot, target_x, target_y)
                        new_start = (entity.dxf.start_angle + rot) % 360.0
                        new_end = (entity.dxf.end_angle + rot) % 360.0
                        out_msp.add_arc(c_pt, radius=entity.dxf.radius, start_angle=new_start, end_angle=new_end, dxfattribs={'layer': 'LASER_CUT', 'color': 3})

            current_offset_x += sw + 300.0

        out_doc.saveas(output_path)
        return output_path

    @staticmethod
    def _transform_pt(x, y, rotation_deg, target_x, target_y):
        rad = math.radians(rotation_deg)
        rx = x * math.cos(rad) - y * math.sin(rad)
        ry = x * math.sin(rad) + y * math.cos(rad)
        return (target_x + rx, target_y + ry)
