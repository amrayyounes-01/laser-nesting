import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import streamlit as st
import io
import os
import tempfile
import importlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches

import nesting_core
import nesting_packer
import preview
importlib.reload(nesting_core)
importlib.reload(nesting_packer)
importlib.reload(preview)

from nesting_core import parse_dxf
from nesting_packer import NestingEngine, calculate_minimum_remnant, MARKET_SHEETS
from preview import generate_sheet_preview
from create_sample_dxf import create_sample_bracket_dxf

st.set_page_config(
    page_title="AI Laser Nesting & Procurement Optimizer",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling for modern industrial CAM look
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        direction: ltr;
        text-align: left;
    }
    .metric-card {
        background: linear-gradient(135deg, #1f2937, #111827);
        border: 1px solid #374151;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
        color: #f9fafb;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .metric-value {
        font-size: 26px;
        font-weight: 800;
        color: #38bdf8;
    }
    .metric-label {
        font-size: 13px;
        color: #9ca3af;
        margin-top: 4px;
        font-weight: 500;
    }
    .advice-card {
        background: linear-gradient(135deg, #064e3b, #022c22);
        border: 2px solid #10b981;
        border-radius: 12px;
        padding: 20px;
        color: #ecfdf5;
        margin-bottom: 20px;
        font-size: 15px;
        line-height: 1.7;
    }
    .remnant-card {
        background: linear-gradient(135deg, #1e1b4b, #0f172a);
        border: 2px solid #818cf8;
        border-radius: 12px;
        padding: 18px;
        color: #e0e7ff;
        margin-bottom: 20px;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        font-weight: 700;
        font-size: 16px;
        padding: 10px;
    }
</style>
""", unsafe_allow_html=True)

# Application Header
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    st.title("⚙️ AI Laser Nesting & Sheet Procurement Optimizer")
    st.markdown("Smart CAM nesting engine for CNC fiber lasers. Automatically selects the **lowest-cost market sheet size** and calculates **exact workshop offcut (remnant) dimensions** to minimize material waste.")
with col_h2:
    st.info("CAM Version 2.0\nFiber Laser Ready")

# Sidebar Configuration
st.sidebar.header("🛠️ Material & Laser Machine Setup")

procurement_mode = st.sidebar.radio(
    "🎯 Sheet Selection Strategy:",
    [
        "🧠 Smart Auto-Procurement (Smallest Market Sheet + Remnant Check)",
        "♻️ Nest on Minimum Remnant Only (Offcut)",
        "📐 Manual Sheet Dimensions"
    ],
    index=0
)

sheet_w = 3000.0
sheet_h = 1500.0

if procurement_mode == "📐 Manual Sheet Dimensions":
    sheet_preset = st.sidebar.selectbox(
        "Standard Sheet Preset:",
        [
            "3000 × 1500 mm (Most Common)",
            "2500 × 1250 mm",
            "2000 × 1000 mm",
            "4000 × 2000 mm",
            "Custom Dimensions"
        ],
        index=0
    )
    if sheet_preset == "3000 × 1500 mm (Most Common)":
        default_w, default_h = 3000.0, 1500.0
    elif sheet_preset == "2500 × 1250 mm":
        default_w, default_h = 2500.0, 1250.0
    elif sheet_preset == "2000 × 1000 mm":
        default_w, default_h = 2000.0, 1000.0
    elif sheet_preset == "4000 × 2000 mm":
        default_w, default_h = 4000.0, 2000.0
    else:
        default_w, default_h = 3000.0, 1500.0

    col_sw1, col_sw2 = st.sidebar.columns(2)
    with col_sw1:
        sheet_w = st.number_input("Sheet Length (X) mm", min_value=100.0, max_value=12000.0, value=default_w, step=50.0)
    with col_sw2:
        sheet_h = st.number_input("Sheet Width (Y) mm", min_value=100.0, max_value=4000.0, value=default_h, step=50.0)
else:
    st.sidebar.markdown("""
    <div style="background: rgba(16, 185, 129, 0.1); border-left: 4px solid #10b981; padding: 10px; border-radius: 4px; font-size: 13px;">
    🔍 <b>Smart Procurement Active:</b><br/>
    The optimizer evaluates market sheets (1×2m, 1.25×2.5m, 1.5×3m) to select the most economical option, while computing exact scrap/remnant plate requirements.
    </div>
    """, unsafe_allow_html=True)

# Thickness & Thermal safety advice
st.sidebar.write("")
thickness = st.sidebar.slider("Sheet Thickness (mm)", min_value=0.5, max_value=25.0, value=3.0, step=0.5)

# Calculate recommended spacing based on thickness
if thickness <= 1.5:
    rec_spacing = 4.0
elif thickness <= 3.0:
    rec_spacing = 6.0
elif thickness <= 6.0:
    rec_spacing = 8.0
elif thickness <= 12.0:
    rec_spacing = 12.0
else:
    rec_spacing = 15.0

st.sidebar.markdown(f"💡 *Thermal Safety Recommendation for {thickness} mm:* **{rec_spacing} mm spacing**")

part_spacing = st.sidebar.number_input(
    "Part-to-Part Spacing (mm)", 
    min_value=1.0, max_value=50.0, value=rec_spacing, step=0.5,
    help="Thermal barrier distance to prevent heat distortion and skeleton warping during cutting."
)

sheet_margin = st.sidebar.number_input(
    "Sheet Edge Margin (mm)", 
    min_value=0.0, max_value=100.0, value=15.0, step=1.0,
    help="Safety perimeter around sheet edges to protect laser nozzle and avoid unlevel borders."
)

laser_kerf = st.sidebar.number_input(
    "Laser Kerf Width (mm)", 
    min_value=0.05, max_value=1.0, value=0.20, step=0.05,
    help="Beam cut width compensation for fiber laser cutting."
)

allow_rot = st.sidebar.checkbox("Allow 90° Part Rotation for Optimization", value=True)

# Main Area: File Input
st.subheader("1. Upload CAD Part (DXF File)")

col_u1, col_u2 = st.columns([3, 1])
with col_u1:
    uploaded_file = st.file_uploader("Choose a DXF file from your computer", type=["dxf"])
with col_u2:
    st.write("Or try a sample part:")
    use_sample = st.button("📁 Load Sample Bracket")

sample_file_path = "sample_bracket.dxf"
if not os.path.exists(sample_file_path):
    create_sample_bracket_dxf(sample_file_path)

active_dxf_bytes = None
active_filename = ""

if uploaded_file is not None:
    active_dxf_bytes = uploaded_file.getvalue()
    active_filename = uploaded_file.name
elif use_sample:
    with open(sample_file_path, "rb") as f:
        active_dxf_bytes = f.read()
    active_filename = "sample_bracket.dxf"

if active_dxf_bytes is not None:
    try:
        # Parse DXF
        part = parse_dxf(active_dxf_bytes)
        
        # Display Part Info
        st.success(f"File parsed successfully: **{active_filename}**")
        
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{part.width:.1f} × {part.height:.1f}</div>
                <div class="metric-label">Part Dimensions (mm)</div>
            </div>
            """, unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{(part.area / 100):.1f}</div>
                <div class="metric-label">Net Surface Area (cm²)</div>
            </div>
            """, unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{len(part.holes)}</div>
                <div class="metric-label">Internal Holes / Cutouts</div>
            </div>
            """, unsafe_allow_html=True)
        with c4:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{(part.total_perimeter / 1000):.2f}</div>
                <div class="metric-label">Cut Length / Part (m)</div>
            </div>
            """, unsafe_allow_html=True)

        st.write("")
        st.subheader("2. Production Quantity & Order Setup")
        
        col_q1, col_q2 = st.columns([2, 2])
        with col_q1:
            quantity = st.number_input("Required Production Quantity (parts)", min_value=1, max_value=50000, value=12, step=1)
        with col_q2:
            st.write("")
            st.write("")
            start_nesting = st.button("🚀 Run Nesting & Calculate Optimal Material", type="primary")

        if start_nesting:
            with st.spinner("Analyzing market sheet sizes and optimizing material usage..."):
                engine = NestingEngine(
                    sheet_width=sheet_w,
                    sheet_height=sheet_h,
                    part_spacing=part_spacing,
                    sheet_margin=sheet_margin,
                    kerf=laser_kerf,
                    allow_rotation=allow_rot
                )
                
                if procurement_mode == "🧠 Smart Auto-Procurement (Smallest Market Sheet + Remnant Check)":
                    result = engine.pack_auto_procurement(part, quantity, target_mode="market_sheets")
                elif procurement_mode == "♻️ Nest on Minimum Remnant Only (Offcut)":
                    result = engine.pack_auto_procurement(part, quantity, target_mode="remnant_only")
                else:
                    result = engine.pack(part, quantity)
                    rem = calculate_minimum_remnant(part, quantity, part_spacing, sheet_margin, allow_rot)
                    result['remnant_info'] = rem
                
                st.write("")
                st.subheader("💡 Workshop Purchasing & Material Recommendation")
                
                # Purchasing Advice Card
                if 'procurement_advice' in result and result['procurement_advice']:
                    st.markdown(f"""
                    <div class="advice-card">
                        {result['procurement_advice'].replace(chr(10), '<br/>')}
                    </div>
                    """, unsafe_allow_html=True)

                # Remnant Highlight Box
                rem_info = result.get('remnant_info')
                if rem_info:
                    st.markdown(f"""
                    <div class="remnant-card">
                        <h4 style="margin: 0 0 10px 0; color: #a5b4fc;">♻️ Minimum Workshop Offcut (Remnant) Required:</h4>
                        <div style="font-size: 18px; font-weight: bold; color: #ffffff;">
                            Required Dimensions: <span style="color: #38bdf8;">{int(rem_info['width'])} × {int(rem_info['height'])} mm</span> 
                            (Area: <span style="color: #4ade80;">{rem_info['area_m2']} m²</span>)
                        </div>
                        <p style="margin: 6px 0 0 0; color: #cbd5e1; font-size: 13px;">
                            📌 If you have an offcut plate in the workshop matching or exceeding these dimensions, you can cut this entire job from it and save opening a new sheet!
                        </p>
                    </div>
                    """, unsafe_allow_html=True)

                st.write("")
                st.subheader("📊 Production Key Metrics")
                
                # KPIs
                r1, r2, r3, r4, r5 = st.columns(5)
                with r1:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-value" style="color: #4ade80;">{result['sheets_needed']}</div>
                        <div class="metric-label">Sheets / Plates Needed</div>
                    </div>
                    """, unsafe_allow_html=True)
                with r2:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-value">{result['parts_per_sheet']}</div>
                        <div class="metric-label">Part Allocation</div>
                    </div>
                    """, unsafe_allow_html=True)
                with r3:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-value" style="color: #60a5fa;">{result['overall_utilization_pct']}%</div>
                        <div class="metric-label">Material Yield (Efficiency)</div>
                    </div>
                    """, unsafe_allow_html=True)
                with r4:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-value" style="color: #f87171;">{result['overall_scrap_pct']}%</div>
                        <div class="metric-label">Scrap Rate (%)</div>
                    </div>
                    """, unsafe_allow_html=True)
                with r5:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-value">{result['total_cut_length_meters']} m</div>
                        <div class="metric-label">Total Laser Cut Path</div>
                    </div>
                    """, unsafe_allow_html=True)

                st.write("")
                st.subheader("🖼️ Nested Sheet Visualizations")
                
                # Sheet visualization
                tabs = st.tabs([f"{s.sheet_name}" for s in result['sheets']])
                for idx, tab in enumerate(tabs):
                    sheet_obj = result['sheets'][idx]
                    with tab:
                        img_buf = generate_sheet_preview(sheet_obj, margin=sheet_margin)
                        st.image(img_buf, use_container_width=True)
                        st.caption(f"{sheet_obj.sheet_name} — Dimensions: {int(sheet_obj.width)} × {int(sheet_obj.height)} mm — Contains {len(sheet_obj.placed_parts)} parts — Yield: {sheet_obj.utilization_pct:.1f}%")

                # Export Nested DXF
                st.write("")
                st.subheader("💾 Export Machine & Production Files")
                
                output_dxf_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
                output_dxf_path = output_dxf_temp.name
                output_dxf_temp.close()
                
                engine.export_nested_dxf(part.doc, result['sheets'], output_dxf_path)
                
                with open(output_dxf_path, "rb") as df:
                    dxf_bytes = df.read()
                    
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    st.download_button(
                        label="📥 Download Nested DXF (Ready for Laser CAM)",
                        data=dxf_bytes,
                        file_name=f"nested_{active_filename}",
                        mime="application/dxf",
                        type="primary"
                    )
                with col_d2:
                    summary_text = f"""CNC Laser Procurement & Production Report:
-----------------------------------------------------------
Part File: {active_filename}
Order Quantity: {quantity} parts
Sheet Thickness: {thickness} mm
Part Spacing: {part_spacing} mm
Edge Margin: {sheet_margin} mm
Laser Kerf: {laser_kerf} mm

Purchasing & Material Recommendation:
{result.get('procurement_advice', 'Custom/Manual Selection')}

Minimum Remnant (Offcut) Required:
- Dimensions: {int(rem_info['width'])} × {int(rem_info['height'])} mm
- Required Area: {rem_info['area_m2']} m²

Production Metrics:
- Sheets Required: {result['sheets_needed']}
- Overall Material Yield: {result['overall_utilization_pct']}%
- Overall Scrap Rate: {result['overall_scrap_pct']}%
- Total Cutting Path: {result['total_cut_length_meters']} meters
"""
                    st.download_button(
                        label="📄 Download Production & Procurement Report (TXT)",
                        data=summary_text,
                        file_name="laser_procurement_report.txt",
                        mime="text/plain"
                    )

    except Exception as err:
        st.error(f"Error processing file: {str(err)}")

else:
    st.info("👆 Please upload a DXF part file or click 'Load Sample Bracket' to test the system.")
