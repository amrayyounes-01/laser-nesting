import ezdxf

def create_sample_multi_part_dxf(output_path="sample_multi_parts.dxf"):
    """
    Creates a sample DXF with 4 distinct parts placed in a row with Q-text above each.
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # Part 1: at x=0
    p1 = [(0, 0), (180, 0), (180, 30), (100, 30), (100, 45), (80, 45), (80, 30), (0, 30), (0, 0)]
    msp.add_lwpolyline(p1, close=True, dxfattribs={'layer': 'CONTOUR', 'color': 3})
    msp.add_circle((40, 15), radius=5, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_circle((140, 15), radius=5, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_text("Q10", dxfattribs={'insert': (80, 65), 'height': 15, 'layer': 'TEXT'})

    # Part 2: at x=240
    p2 = [(240, 0), (440, 0), (440, 40), (240, 40), (240, 0)]
    msp.add_lwpolyline(p2, close=True, dxfattribs={'layer': 'CONTOUR', 'color': 3})
    msp.add_circle((280, 20), radius=6, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_circle((400, 20), radius=6, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_text("Q9", dxfattribs={'insert': (330, 65), 'height': 15, 'layer': 'TEXT'})

    # Part 3: at x=500
    p3 = [(500, 0), (660, 0), (660, 50), (500, 50), (500, 0)]
    msp.add_lwpolyline(p3, close=True, dxfattribs={'layer': 'CONTOUR', 'color': 3})
    msp.add_circle((540, 25), radius=7, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_circle((620, 25), radius=7, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_text("Q5", dxfattribs={'insert': (570, 75), 'height': 15, 'layer': 'TEXT'})

    # Part 4: at x=720
    p4 = [(720, 0), (840, 0), (840, 60), (720, 60), (720, 0)]
    msp.add_lwpolyline(p4, close=True, dxfattribs={'layer': 'CONTOUR', 'color': 3})
    msp.add_circle((780, 30), radius=10, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_text("Q12", dxfattribs={'insert': (770, 85), 'height': 15, 'layer': 'TEXT'})

    doc.saveas(output_path)
    print("Created", output_path)
    return output_path

if __name__ == "__main__":
    create_sample_multi_part_dxf()
