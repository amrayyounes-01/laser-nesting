import ezdxf

def create_sample_bracket_dxf(output_path="sample_bracket.dxf"):
    """
    Creates an industrial sheet metal bracket DXF with outer contour and 3 inner mounting holes.
    """
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    # Outer contour of a bracket: (0,0) -> (220, 0) -> (220, 80) -> (120, 160) -> (0, 160) -> (0,0)
    outer_points = [
        (0, 0),
        (220, 0),
        (220, 80),
        (120, 160),
        (0, 160),
        (0, 0)
    ]
    msp.add_lwpolyline(outer_points, close=True, dxfattribs={'layer': 'CONTOUR', 'color': 3})
    
    # Inner mounting holes
    msp.add_circle((40, 40), radius=12, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_circle((160, 40), radius=10, dxfattribs={'layer': 'HOLES', 'color': 2})
    msp.add_circle((50, 120), radius=14, dxfattribs={'layer': 'HOLES', 'color': 2})
    
    doc.saveas(output_path)
    return output_path

if __name__ == "__main__":
    create_sample_bracket_dxf("sample_bracket.dxf")
    print("Created sample_bracket.dxf successfully!")
