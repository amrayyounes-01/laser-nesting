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

class CompactMaxRectsPacker:
    """
    Maximal Rectangles (MaxRects) 2D bin packing engine with 
    compact bounding-box minimization and Bottom-Left clustering.
    Packs parts in the minimum possible area while strictly respecting 
    inter-part spacing and sheet margins.
    """
    def __init__(self, sheet_w, sheet_h, spacing=5.0, margin=15.0, allow_rotation=True, target_aspect=1.4):
        self.sheet_w = float(sheet_w)
        self.sheet_h = float(sheet_h)
        self.spacing = float(spacing)
        self.margin = float(margin)
        self.allow_rotation = allow_rotation
        self.target_aspect = float(target_aspect)
        
        usable_w = self.sheet_w - 2 * self.margin
        usable_h = self.sheet_h - 2 * self.margin
        self.free_rects = [[self.margin, self.margin, usable_w, usable_h]]
        self.placed_entries = []

    def pack_items(self, items):
        """
        Packs a list of PartGeometry items onto this sheet.
        Returns a tuple: (list of placed dictionaries, list of unplaced items).
        """
        unplaced = []
        for item in items:
            best_pos = None
            best_score = float('inf')

            pw = item.width
            ph = item.height
            orientations = [(pw, ph, 0)]
            if self.allow_rotation and abs(pw - ph) > 0.1:
                orientations.append((ph, pw, 90))

            current_maxx = max([p['x'] + p['w'] for p in self.placed_entries], default=self.margin)
            current_maxy = max([p['y'] + p['h'] for p in self.placed_entries], default=self.margin)

            for w, h, rot in orientations:
                for r in self.free_rects:
                    rx, ry, rw, rh = r
                    if rw >= w and rh >= h:
                        new_mx = max(current_maxx, rx + w)
                        new_my = max(current_maxy, ry + h)
                        
                        # Encourage compact, aspect-balanced clustering (e.g. 1.4:1)
                        eff_dim = max(new_mx, new_my * self.target_aspect)
                        area_score = new_mx * new_my
                        score = area_score * 10.0 + (eff_dim ** 2) * 5.0 + (ry * 2.0 + rx)

                        if score < best_score:
                            best_score = score
                            best_pos = {
                                'item': item,
                                'x': rx,
                                'y': ry,
                                'w': w,
                                'h': h,
                                'rotation': rot
                            }

            if not best_pos:
                unplaced.append(item)
                continue

            self.placed_entries.append(best_pos)
            self._split_free(best_pos['x'], best_pos['y'], best_pos['w'], best_pos['h'])

        return self.placed_entries, unplaced

    def _split_free(self, x, y, w, h):
        occ_x, occ_y = x, y
        occ_w, occ_h = w + self.spacing, h + self.spacing
        new_free = []
        for r in self.free_rects:
            rx, ry, rw, rh = r
            if not (occ_x >= rx + rw or occ_x + occ_w <= rx or occ_y >= ry + rh or occ_y + occ_h <= ry):
                if occ_x > rx and occ_x < rx + rw:
                    new_free.append([rx, ry, occ_x - rx, rh])
                if occ_x + occ_w < rx + rw:
                    new_free.append([occ_x + occ_w, ry, (rx + rw) - (occ_x + occ_w), rh])
                if occ_y > ry and occ_y < ry + rh:
                    new_free.append([rx, ry, rw, occ_y - ry])
                if occ_y + occ_h < ry + rh:
                    new_free.append([rx, occ_y + occ_h, rw, (ry + rh) - (occ_y + occ_h)])
            else:
                new_free.append(r)

        pruned = []
        for i, r1 in enumerate(new_free):
            contained = False
            for j, r2 in enumerate(new_free):
                if i != j:
                    if r1[0] >= r2[0] and r1[1] >= r2[1] and r1[0] + r1[2] <= r2[0] + r2[2] and r1[1] + r1[3] <= r2[1] + r2[3]:
                        contained = True
                        break
            if not contained and r1[2] > 1.0 and r1[3] > 1.0:
                pruned.append(r1)
        self.free_rects = pruned

def calculate_minimum_remnant(part_geom, quantity, spacing=6.0, margin=15.0, allow_rotation=True):
    """
    Calculates the minimum bounding offcut plate for a single part repeated quantity times.
    """
    items = [part_geom] * quantity
    return calculate_multi_part_remnant([part_geom], spacing=spacing, margin=margin, allow_rotation=allow_rotation)

def calculate_multi_part_remnant(parts_list, spacing=6.0, margin=15.0, allow_rotation=True):
    """
    Calculates the absolute minimum offcut plate needed to fit an assortment of multiple parts compactly.
    """
    items = []
    for p in parts_list:
        for _ in range(p.quantity):
            items.append(p)
            
    if not items:
        return None

    sorted_items = sorted(items, key=lambda it: (it.area, max(it.width, it.height)), reverse=True)
    
    # Pack on large virtual sheet to find optimal minimum bounding envelope
    packer = CompactMaxRectsPacker(6000.0, 6000.0, spacing=spacing, margin=margin, allow_rotation=allow_rotation)
    placed, unplaced = packer.pack_items(sorted_items)
    if not placed:
        return None

    maxx = max(p['x'] + p['w'] for p in placed)
    maxy = max(p['y'] + p['h'] for p in placed)
    rem_w = math.ceil((maxx + margin) / 10.0) * 10.0
    rem_h = math.ceil((maxy + margin) / 10.0) * 10.0
    area = rem_w * rem_h

    return {
        'width': rem_w,
        'height': rem_h,
        'area': area,
        'area_m2': round(area / 1e6, 3)
    }

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
        """Packs a list of heterogeneous parts onto fixed-size sheet(s) compactly."""
        sw = float(sheet_w or self.sheet_w)
        sh = float(sheet_h or self.sheet_h)

        items = []
        for p in parts_list:
            for _ in range(p.quantity):
                items.append(p)

        sheets = self._pack_items_compact(items, sw, sh, sheet_name=sheet_name, is_remnant=is_remnant)
        return self._build_multi_summary(parts_list, items, sheets, sw, sh)

    def pack_multi_auto_procurement(self, parts_list, target_mode="market_sheets"):
        """
        Smart Workshop Optimization for Multi-Part Assortments:
        - Evaluates market standard sheets (1x2m, 1.25x2.5m, 1.5x3m).
        - Packs all parts in the minimum possible area in the bottom-left corner.
        - Calculates the minimal offcut (remnant) dimensions for the job.
        """
        items = []
        for p in parts_list:
            for _ in range(p.quantity):
                items.append(p)

        total_placed_needed = len(items)
        if total_placed_needed == 0:
            raise ValueError("Total quantity is 0!")

        # 1. Calculate minimum compact remnant
        remnant = calculate_multi_part_remnant(parts_list, self.spacing, self.margin, self.allow_rotation)

        if target_mode == "remnant_only" and remnant:
            sheets = self._pack_items_compact(
                items, remnant['width'], remnant['height'], 
                sheet_name=f"Remnant Plate ({int(remnant['width'])} × {int(remnant['height'])} mm)", 
                is_remnant=True
            )
            res = self._build_multi_summary(parts_list, items, sheets, remnant['width'], remnant['height'])
            res['remnant_info'] = remnant
            res['procurement_advice'] = (
                f"💡 Compact Nesting on Minimum Offcut: ({int(remnant['width'])} × {int(remnant['height'])} mm) "
                f"requiring only {remnant['area_m2']} m² without cutting a new full sheet!"
            )
            return res

        # 2. Check market sheets (ascending by size)
        for s in MARKET_SHEETS:
            # Check if all parts fit on single sheet of this size
            packer = CompactMaxRectsPacker(s['w'], s['h'], spacing=self.spacing, margin=self.margin, allow_rotation=self.allow_rotation)
            sorted_items = sorted(items, key=lambda it: (it.area, max(it.width, it.height)), reverse=True)
            placed, unplaced = packer.pack_items(sorted_items)
            
            if len(unplaced) == 0:
                sheets = self._pack_items_compact(items, s['w'], s['h'], sheet_name=f"Standard Sheet {s['name']}")
                res = self._build_multi_summary(parts_list, items, sheets, s['w'], s['h'])
                res['remnant_info'] = remnant
                rem_text = f"({int(remnant['width'])} × {int(remnant['height'])} mm - {remnant['area_m2']} m²)" if remnant else ""
                res['procurement_advice'] = (
                    f"✅ Procurement Recommendation: Order 1 sheet of {s['name']} only.\n"
                    f"All {total_placed_needed} parts fit compactly in the bottom-left corner, leaving the rest of the sheet clean.\n\n"
                    f"♻️ Workshop Remnant Alternative: If you have an offcut piece of at least {rem_text}, "
                    f"you can cut this job directly from it!"
                )
                return res

        # 3. Exceeds single sheet: Multi-sheet strategy with 1500x3000 mm
        bulk_sheet = MARKET_SHEETS[2]  # 1500x3000
        sheets = self._pack_items_compact(items, bulk_sheet['w'], bulk_sheet['h'], sheet_name=f"Sheet {bulk_sheet['name']}")
        res = self._build_multi_summary(parts_list, items, sheets, bulk_sheet['w'], bulk_sheet['h'])
        res['remnant_info'] = remnant
        
        num_sheets = len(sheets)
        rem_text = f"({int(remnant['width'])} × {int(remnant['height'])} mm)" if remnant else ""
        res['procurement_advice'] = (
            f"✅ Workshop Procurement Plan: Order {num_sheets} sheet(s) of 1500 × 3000 mm.\n"
            f"Total parts: {total_placed_needed} distributed across {num_sheets} sheet(s).\n\n"
            f"♻️ Total minimum offcut required is ~{rem_text} if using remnant plates."
        )
        return res

    def _pack_items_compact(self, items, sheet_w, sheet_h, sheet_name=None, is_remnant=False):
        """
        Packs items using Compact 2D MaxRects algorithm minimizing bounding envelope and respecting spacing.
        Supports multi-sheet overflow.
        """
        sorted_items = sorted(items, key=lambda it: (it.area, max(it.width, it.height)), reverse=True)
        sheets = []
        current_sheet_idx = 1
        remaining_items = sorted_items
        part_inst_counter = {}

        while remaining_items:
            packer = CompactMaxRectsPacker(sheet_w, sheet_h, spacing=self.spacing, margin=self.margin, allow_rotation=self.allow_rotation)
            placed_entries, unplaced = packer.pack_items(remaining_items)
            
            if not placed_entries:
                raise ValueError(f"Part dimensions exceed sheet usable area ({int(sheet_w - 2*self.margin)} × {int(sheet_h - 2*self.margin)} mm)!")

            sh_name = sheet_name or f"Sheet {current_sheet_idx}"
            if len(sheets) > 0 and not sheet_name:
                sh_name = f"Sheet {current_sheet_idx}"
            elif len(sheets) > 0 and sheet_name:
                sh_name = f"{sheet_name} (Page {current_sheet_idx})"

            current_sh = SheetResult(current_sheet_idx, sheet_w, sheet_h, sheet_name=sh_name, is_remnant=is_remnant)

            for entry in placed_entries:
                it = entry['item']
                pos_x = entry['x']
                pos_y = entry['y']
                pw = entry['w']
                ph = entry['h']
                rot = entry['rotation']

                base_poly = translate(it.outer_poly, xoff=-it.bounds[0], yoff=-it.bounds[1])
                if rot == 90:
                    rot_poly = rotate(base_poly, 90, origin=(0, 0))
                    rot_aligned = translate(rot_poly, xoff=-rot_poly.bounds[0], yoff=-rot_poly.bounds[1])
                    placed_poly = translate(rot_aligned, xoff=pos_x, yoff=pos_y)
                else:
                    placed_poly = translate(base_poly, xoff=pos_x, yoff=pos_y)

                part_inst_counter[it.name] = part_inst_counter.get(it.name, 0) + 1
                lbl = f"{it.name} #{part_inst_counter[it.name]}"

                current_sh.placed_parts.append(PlacedPart(
                    part_index=len(current_sh.placed_parts) + 1,
                    sheet_index=current_sheet_idx,
                    x=pos_x,
                    y=pos_y,
                    rotation=rot,
                    width=pw,
                    height=ph,
                    polygon=placed_poly,
                    part_geom=it,
                    label=lbl
                ))

            sheet_area = sheet_w * sheet_h
            parts_area = sum(p.part_geom.area for p in current_sh.placed_parts if p.part_geom)
            current_sh.utilization_pct = (parts_area / sheet_area) * 100.0 if sheet_area > 0 else 0.0
            current_sh.scrap_pct = 100.0 - current_sh.utilization_pct

            sheets.append(current_sh)
            current_sheet_idx += 1
            remaining_items = unplaced

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
            "total_material_area_m2": round(total_material_area / 1e6, 3),
            "total_parts_net_area_m2": round(total_parts_net_area / 1e6, 3),
            "utilization_pct": round(overall_util, 1),
            "scrap_pct": round(overall_scrap, 1),
            "total_cut_length_m": round(total_cut_m, 2),
            "sheets": sheets
        }

    def _pack_single_fixed(self, part_geom, quantity, sheet_w, sheet_h):
        return self.pack_multi_parts_fixed([part_geom], sheet_w, sheet_h)

    def export_nested_dxf(self, original_dxf_doc, sheets, output_path, include_labels=False):
        """
        Exports the placed parts onto standard DXF sheet(s).
        Preserves original CAD geometry for every individual part.
        When include_labels is False (default), NO text is written, preventing laser cutting of text.
        """
        out_doc = ezdxf.new('R2010')
        out_msp = out_doc.modelspace()

        out_doc.layers.add(name='SHEET_FRAME', color=1)    # Red (border)
        out_doc.layers.add(name='SHEET_MARGIN', color=5)   # Blue (usable margin)
        out_doc.layers.add(name='LASER_CUT', color=3)      # Green (part contours)
        out_doc.layers.add(name='LASER_HOLES', color=2)    # Yellow (inner cutouts)
        if include_labels:
            out_doc.layers.add(name='TEXT_INFO', color=7)  # White (annotations)

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

            # 3. Sheet info banner (only if labels enabled)
            if include_labels:
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
                
                # Part label on cut sheet (only if labels enabled)
                if include_labels and placed.label:
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
