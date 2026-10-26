import matplotlib.pyplot as plt
import matplotlib.patches as patches
from shapely.geometry import Polygon, MultiPolygon
import io

def generate_sheet_preview(sheet_result, sheet_w=None, sheet_h=None, margin=15.0, show_holes=True):
    """
    Renders a high-resolution preview of a single nested sheet using Matplotlib.
    Dynamically uses sheet_result.width and sheet_result.height.
    """
    w = sheet_result.width if (hasattr(sheet_result, 'width') and sheet_result.width > 0) else sheet_w
    h = sheet_result.height if (hasattr(sheet_result, 'height') and sheet_result.height > 0) else sheet_h
    
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    
    # Background sheet
    sheet_rect = patches.Rectangle(
        (0, 0), w, h, 
        linewidth=2, edgecolor='#E63946', facecolor='#F8F9FA'
    )
    ax.add_patch(sheet_rect)
    
    # Margin limit boundary (usable area)
    margin_rect = patches.Rectangle(
        (margin, margin), w - 2 * margin, h - 2 * margin,
        linewidth=1, edgecolor='#457B9D', facecolor='none', linestyle='--'
    )
    ax.add_patch(margin_rect)
    
    # Draw placed parts
    for idx, placed in enumerate(sheet_result.placed_parts):
        poly = placed.polygon
        if poly is not None and not poly.is_empty:
            if isinstance(poly, Polygon):
                geoms = [poly]
            elif isinstance(poly, MultiPolygon):
                geoms = list(poly.geoms)
            else:
                geoms = []
                
            for g in geoms:
                x, y = g.exterior.xy
                # Outer contour fill
                ax.fill(x, y, alpha=0.75, fc='#2A9D8F', ec='#264653', linewidth=1.2)
                
                # Draw holes if any
                if show_holes:
                    for interior in g.interiors:
                        hx, hy = interior.xy
                        ax.fill(hx, hy, fc='#F8F9FA', ec='#E76F51', linewidth=1.0)
                        
            # Part center index label
            centroid = poly.centroid
            ax.text(centroid.x, centroid.y, str(idx + 1), 
                    color='#FFFFFF', fontsize=7, fontweight='bold',
                    ha='center', va='center',
                    bbox=dict(boxstyle='circle,pad=0.2', facecolor='#1D3557', alpha=0.7, edgecolor='none'))

    # Styling and dimensions
    ax.set_xlim(-w * 0.03, w * 1.03)
    ax.set_ylim(-h * 0.05, h * 1.1)
    ax.set_aspect('equal')
    sheet_title = getattr(sheet_result, 'sheet_name', f"Sheet {sheet_result.sheet_index}")
    ax.set_title(f"Preview: {sheet_title} ({int(w)} × {int(h)} mm) | Parts: {len(sheet_result.placed_parts)} | Yield: {sheet_result.utilization_pct:.1f}%", fontsize=11, pad=12, fontweight='bold')
    
    ax.set_xlabel(f"Width (X): {int(w)} mm", fontsize=9)
    ax.set_ylabel(f"Height (Y): {int(h)} mm", fontsize=9)
    ax.grid(True, linestyle=':', alpha=0.3)
    
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf
